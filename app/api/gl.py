"""총계정원장(GL) 조회 API — 분개장 / 계정별원장 / 시산표 / 재대사 / 재전기.

설계: docs/gl-design.md §7. 권한은 새 코드를 늘리지 않고 기존 회계 권한을
재사용한다(문서 권장 확인) — 조회는 payment:read(회계 조회를 가진 역할 전부),
재전기(rebuild)는 payment:write.
"""
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Account, JournalEntry, JournalLine, Partner, User
from ..schemas import (
    Page, JournalEntryOut, JournalLineOut, LedgerOut, LedgerLineOut,
    TrialBalanceOut, TrialBalanceRow, ReconcileOut, GLRebuildOut,
    ManualEntryIn, IncomeStatementOut, BalanceSheetOut, FinancialRow,
    PeriodOut, PeriodListOut, PeriodCloseOut,
)
from ..deps import require_permission
from ..services import paginate, record_audit
from ..gl import (
    rebuild_gl, reconcile, post_manual_entry, close_period, reopen_period,
    ensure_periods, period_net_income, assert_period_open, current_period,
    GLError, PeriodClosedError, SOURCE_CLOSING,
)

router = APIRouter(prefix="/api/gl", tags=["gl"])


def _entry_out(e: JournalEntry, partner_names: dict[int, str] | None = None) -> JournalEntryOut:
    names = partner_names or {}
    return JournalEntryOut(
        id=e.id, entry_no=e.entry_no, entry_date=e.entry_date,
        description=e.description, source_type=e.source_type, source_id=e.source_id,
        status=e.status, created_at=e.created_at,
        lines=[
            JournalLineOut(
                id=l.id, line_no=l.line_no,
                account_code=l.account.code, account_name=l.account.name,
                debit=float(l.debit), credit=float(l.credit),
                partner_id=l.partner_id,
                partner_name=names.get(l.partner_id, "") if l.partner_id else "",
                memo=l.memo,
            )
            for l in e.lines
        ],
    )


def _partner_names(db: Session, entries: list[JournalEntry]) -> dict[int, str]:
    """전표 라인들의 거래처 이름을 한 번의 쿼리로 조회한다(N+1 방지)."""
    ids = {l.partner_id for e in entries for l in e.lines if l.partner_id}
    if not ids:
        return {}
    return dict(db.execute(select(Partner.id, Partner.name).where(Partner.id.in_(ids))).all())


# ---------- 분개장 ----------
@router.get("/journal", response_model=Page[JournalEntryOut])
def list_journal(
    date_from: str | None = Query(None, description="entry_date 이상 (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="entry_date 이하 (YYYY-MM-DD)"),
    source_type: str | None = Query(None, description="MOVEMENT/PAYMENT/MANUAL 필터"),
    source_id: int | None = Query(None, description="원천 id 필터(source_type 과 함께)"),
    account_code: str | None = Query(None, description="특정 계정이 포함된 전표만"),
    q: str | None = Query(None, description="전표번호·적요 검색"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("payment:read")),
):
    """분개장(전표 목록 + 라인). 최신 전표가 위로 오도록 내림차순."""
    stmt = select(JournalEntry).order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc())
    if date_from:
        stmt = stmt.where(JournalEntry.entry_date >= date_from)
    if date_to:
        stmt = stmt.where(JournalEntry.entry_date <= date_to)
    if source_type:
        stmt = stmt.where(JournalEntry.source_type == source_type)
    if source_id is not None:
        stmt = stmt.where(JournalEntry.source_id == source_id)
    if account_code:
        # 해당 계정 라인이 하나라도 있는 전표 (EXISTS — 라인 조인 중복 방지)
        sub = (
            select(JournalLine.id)
            .join(Account, JournalLine.account_id == Account.id)
            .where(JournalLine.entry_id == JournalEntry.id, Account.code == account_code)
        )
        stmt = stmt.where(sub.exists())
    if q:
        stmt = stmt.where(or_(
            JournalEntry.entry_no.like(f"%{q}%"),
            JournalEntry.description.like(f"%{q}%"),
        ))
    result = paginate(db, stmt, page, page_size)
    names = _partner_names(db, result["items"])
    result["items"] = [_entry_out(e, names) for e in result["items"]]
    return result


@router.get("/journal/{entry_id}", response_model=JournalEntryOut)
def get_journal_entry(
    entry_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("payment:read")),
):
    e = db.get(JournalEntry, entry_id)
    if not e:
        raise HTTPException(status_code=404, detail="전표를 찾을 수 없습니다")
    return _entry_out(e, _partner_names(db, [e]))


# ---------- S6: 수기 전표(MANUAL) ----------
@router.post("/manual", response_model=JournalEntryOut, status_code=201)
def create_manual_entry(
    payload: ManualEntryIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("payment:write")),
):
    """수기 전표 등록(기초잔액·마감·수정 분개). 차대 균형은 스키마·서비스가
    이중 검증한다. source_type=MANUAL, source_id=NULL 이라 rebuild 가 보존한다."""
    try:
        entry = post_manual_entry(
            db, entry_date=payload.entry_date.isoformat(), description=payload.description,
            lines=[l.model_dump() for l in payload.lines],
        )
    except PeriodClosedError:
        # 마감 위반은 전역 핸들러가 code=period_closed 로 내려보낸다(화면이 분기할 수 있게).
        db.rollback()
        raise
    except GLError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    record_audit(db, user, "CREATE", "gl", entry.id,
                 after={"entry_no": entry.entry_no, "description": entry.description})
    db.commit()
    db.refresh(entry)
    return _entry_out(entry, _partner_names(db, [entry]))


@router.delete("/journal/{entry_id}", status_code=204)
def delete_manual_entry(
    entry_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("payment:write")),
):
    """수기 전표만 직접 삭제 가능. 자동 전기(MOVEMENT/PAYMENT) 전표는 원천 문서를
    삭제해야 동반 삭제되므로(§9-4) 여기서 지우면 대사가 깨진다 — 400 으로 막는다.
    마감 전표(CLOSING)는 기간을 마감 해제해야 지워진다. 마감된 기간의 수기 전표도 못 지운다."""
    e = db.get(JournalEntry, entry_id)
    if not e:
        raise HTTPException(status_code=404, detail="전표를 찾을 수 없습니다")
    if e.source_type == SOURCE_CLOSING:
        raise HTTPException(
            status_code=400,
            detail="마감 전표는 직접 삭제할 수 없습니다. 해당 회계기간의 마감을 해제하세요",
        )
    if e.source_type != "MANUAL":
        raise HTTPException(
            status_code=400,
            detail="자동 전기된 전표는 원천 문서에서만 삭제할 수 있습니다(수기 전표만 직접 삭제 가능)",
        )
    assert_period_open(db, e.entry_date, "전표를 삭제")   # GLError → 400(main 핸들러)
    before = {"entry_no": e.entry_no, "description": e.description}
    db.delete(e)   # 라인은 relationship cascade(delete-orphan)로 함께 삭제
    record_audit(db, user, "DELETE", "gl", entry_id, before=before)
    db.commit()


# ---------- 계정별원장 ----------
@router.get("/ledger", response_model=LedgerOut)
def account_ledger(
    account_code: str = Query(..., description="계정코드 (예: 1130)"),
    date_from: str | None = Query(None, description="기간 시작 YYYY-MM-DD"),
    date_to: str | None = Query(None, description="기간 종료 YYYY-MM-DD"),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("payment:read")),
):
    """계정별원장: 기초잔액(기간 이전 누계) + 기간 라인 러닝밸런스 + 기말잔액.
    잔액은 정상잔액 방향(normal_side) 기준 부호로 표시한다."""
    acct = db.execute(select(Account).where(Account.code == account_code)).scalar_one_or_none()
    if not acct:
        raise HTTPException(status_code=404, detail="계정을 찾을 수 없습니다")
    sign = 1 if acct.normal_side == "debit" else -1

    # 기초잔액: date_from 이전 전표의 순차변 누계 × 부호
    opening = Decimal("0")
    if date_from:
        val = db.execute(
            select(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0))
            .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
            .where(JournalLine.account_id == acct.id, JournalEntry.entry_date < date_from)
        ).scalar_one()
        opening = Decimal(str(val or 0)) * sign

    stmt = (
        select(JournalLine, JournalEntry)
        .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
        .where(JournalLine.account_id == acct.id)
        .order_by(JournalEntry.entry_date.asc(), JournalEntry.id.asc(), JournalLine.line_no.asc())
    )
    if date_from:
        stmt = stmt.where(JournalEntry.entry_date >= date_from)
    if date_to:
        stmt = stmt.where(JournalEntry.entry_date <= date_to)

    running = opening
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    lines: list[LedgerLineOut] = []
    for line, entry in db.execute(stmt).all():
        d, c = Decimal(line.debit or 0), Decimal(line.credit or 0)
        total_debit += d
        total_credit += c
        running += (d - c) * sign
        lines.append(LedgerLineOut(
            entry_id=entry.id, entry_no=entry.entry_no, entry_date=entry.entry_date,
            description=entry.description, source_type=entry.source_type,
            source_id=entry.source_id,
            debit=float(d), credit=float(c), balance=float(running),
        ))

    return LedgerOut(
        account_code=acct.code, account_name=acct.name, account_type=acct.account_type,
        normal_side=acct.normal_side, date_from=date_from, date_to=date_to,
        opening_balance=float(opening),
        total_debit=float(total_debit), total_credit=float(total_credit),
        closing_balance=float(running), lines=lines,
    )


# ---------- 시산표 ----------
@router.get("/trial-balance", response_model=TrialBalanceOut)
def trial_balance(
    date_from: str | None = Query(None, description="기간 시작 YYYY-MM-DD(미지정 시 전체)"),
    date_to: str | None = Query(None, description="기간 종료 YYYY-MM-DD"),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("payment:read")),
):
    """시산표: 계정별 차/대 합계·정상잔액 방향 잔액. Σ차변 == Σ대변이어야 한다.
    활성 계정은 거래가 없어도 0 으로 표시한다(누락과 무거래를 구분)."""
    sub = (
        select(
            JournalLine.account_id.label("account_id"),
            func.coalesce(func.sum(JournalLine.debit), 0).label("d"),
            func.coalesce(func.sum(JournalLine.credit), 0).label("c"),
        )
        .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
    )
    if date_from:
        sub = sub.where(JournalEntry.entry_date >= date_from)
    if date_to:
        sub = sub.where(JournalEntry.entry_date <= date_to)
    sub = sub.group_by(JournalLine.account_id).subquery()

    rows = db.execute(
        select(Account, func.coalesce(sub.c.d, 0), func.coalesce(sub.c.c, 0))
        .outerjoin(sub, sub.c.account_id == Account.id)
        .where(Account.is_active.is_(True))
        .order_by(Account.code.asc())
    ).all()

    out_rows: list[TrialBalanceRow] = []
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    for acct, d, c in rows:
        d, c = Decimal(str(d or 0)), Decimal(str(c or 0))
        total_debit += d
        total_credit += c
        balance = (d - c) if acct.normal_side == "debit" else (c - d)
        out_rows.append(TrialBalanceRow(
            account_code=acct.code, account_name=acct.name,
            account_type=acct.account_type, normal_side=acct.normal_side,
            debit_total=float(d), credit_total=float(c), balance=float(balance),
        ))

    return TrialBalanceOut(
        date_from=date_from, date_to=date_to, rows=out_rows,
        total_debit=float(total_debit), total_credit=float(total_credit),
        balanced=(total_debit == total_credit),
    )


# ---------- S6: 간이 재무제표 (손익계산서 / 재무상태표) ----------
def _account_nets(
    db: Session, account_types: tuple[str, ...],
    *, date_from: str | None = None, date_to: str | None = None,
    exclude_closing: bool = False,
) -> list[tuple[Account, Decimal]]:
    """계정유형별 (Account, net_debit=Σdebit−Σcredit). entry_date 기간 필터.
    거래 없는 활성 계정도 0 으로 포함(시산표와 동일 관례). MANUAL 포함 —
    재무제표는 총계 관점이라 수기 분개도 반영해야 한다.

    exclude_closing=True 는 손익계산서용이다. 마감 대체분개는 손익계정을 0 으로 만들기
    위한 장치일 뿐 그 달의 영업 실적이 아니므로, 빼야 마감 후에도 손익이 그대로 보인다.
    반대로 재무상태표는 마감 전표를 포함해야 한다 — 그래야 마감된 이익이 이월이익잉여금
    (자본)으로 넘어가고 손익계정에는 '미마감분'만 남는다."""
    agg = (
        select(
            JournalLine.account_id.label("aid"),
            func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0).label("net"),
        )
        .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
    )
    if date_from:
        agg = agg.where(JournalEntry.entry_date >= date_from)
    if date_to:
        agg = agg.where(JournalEntry.entry_date <= date_to)
    if exclude_closing:
        agg = agg.where(JournalEntry.source_type != SOURCE_CLOSING)
    agg = agg.group_by(JournalLine.account_id).subquery()

    rows = db.execute(
        select(Account, func.coalesce(agg.c.net, 0))
        .outerjoin(agg, agg.c.aid == Account.id)
        .where(
            Account.account_type.in_(account_types),
            # 비활성 계정이라도 잔액이 남아 있으면 재무제표에서 뺄 수 없다(대차가 깨진다).
            or_(Account.is_active.is_(True), agg.c.net.isnot(None)),
        )
        .order_by(Account.code.asc())
    ).all()
    return [(acct, Decimal(str(net or 0))) for acct, net in rows]


@router.get("/income-statement", response_model=IncomeStatementOut)
def income_statement(
    date_from: str | None = Query(None, description="기간 시작 YYYY-MM-DD"),
    date_to: str | None = Query(None, description="기간 종료 YYYY-MM-DD"),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("payment:read")),
):
    """간이 손익계산서: 수익 − 비용 = 당기순이익(§7). 수익은 대변 정상잔액,
    비용은 차변 정상잔액이라 각각 부호를 뒤집어 양수로 표시한다.
    마감 대체분개(CLOSING)는 제외한다 — 마감했다고 그 달 매출이 0 이 되면 안 된다."""
    rev = _account_nets(db, ("revenue",), date_from=date_from, date_to=date_to,
                        exclude_closing=True)
    exp = _account_nets(db, ("expense",), date_from=date_from, date_to=date_to,
                        exclude_closing=True)
    revenues = [FinancialRow(account_code=a.code, account_name=a.name, amount=float(-net)) for a, net in rev]
    expenses = [FinancialRow(account_code=a.code, account_name=a.name, amount=float(net)) for a, net in exp]
    total_revenue = float(sum((-net for _, net in rev), Decimal("0")))
    total_expense = float(sum((net for _, net in exp), Decimal("0")))
    return IncomeStatementOut(
        date_from=date_from, date_to=date_to,
        revenues=revenues, expenses=expenses,
        total_revenue=total_revenue, total_expense=total_expense,
        net_income=total_revenue - total_expense,
    )


@router.get("/balance-sheet", response_model=BalanceSheetOut)
def balance_sheet(
    date_to: str | None = Query(None, description="기준일 YYYY-MM-DD(이하 누계, 미지정 시 전체)"),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("payment:read")),
):
    """간이 재무상태표: 자산 = 부채 + 자본 + 당기순이익(§7).

    마감 전표를 **포함**해 집계한다 — 마감된 기간의 손익은 이미 3110 이월이익잉여금
    (자본)으로 대체되었으므로 여기 net_income 에는 미마감 기간의 손익만 남는다.
    시산표가 균형이면 항상 대차가 맞는다(각 전표가 자기완결적이므로)."""
    assets = _account_nets(db, ("asset",), date_to=date_to)
    liabs = _account_nets(db, ("liability",), date_to=date_to)
    equity = _account_nets(db, ("equity",), date_to=date_to)
    rev = _account_nets(db, ("revenue",), date_to=date_to)
    exp = _account_nets(db, ("expense",), date_to=date_to)

    asset_rows = [FinancialRow(account_code=a.code, account_name=a.name, amount=float(net)) for a, net in assets]
    liab_rows = [FinancialRow(account_code=a.code, account_name=a.name, amount=float(-net)) for a, net in liabs]
    equity_rows = [FinancialRow(account_code=a.code, account_name=a.name, amount=float(-net)) for a, net in equity]

    # 대차 판정은 Decimal 로 한다(float 동등 비교는 1원짜리 차이도 놓치거나 없는 차이를 만든다).
    d_assets = sum((net for _, net in assets), Decimal("0"))
    d_liabs = sum((-net for _, net in liabs), Decimal("0"))
    d_equity = sum((-net for _, net in equity), Decimal("0"))
    d_net_income = (
        sum((-net for _, net in rev), Decimal("0")) - sum((net for _, net in exp), Decimal("0"))
    )
    d_tle = d_liabs + d_equity + d_net_income
    return BalanceSheetOut(
        date_to=date_to, assets=asset_rows, liabilities=liab_rows, equity=equity_rows,
        total_assets=float(d_assets), total_liabilities=float(d_liabs),
        total_equity=float(d_equity), net_income=float(d_net_income),
        total_liabilities_and_equity=float(d_tle),
        balanced=(d_assets == d_tle),
    )


# ---------- 기간 마감 (period close) ----------
@router.get("/periods", response_model=PeriodListOut)
def list_periods(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("payment:read")),
):
    """회계기간 목록(첫 전표가 있는 달 ~ 이번 달) + 각 달의 손익·마감 상태.
    next_closable 은 가장 오래된 미마감 기간 — 마감은 이 순서대로만 가능하다."""
    periods = ensure_periods(db)
    db.commit()   # ensure_periods 가 만든 기간 행을 확정(조회지만 지연 생성이라)

    closing_entries = {
        e.source_id: e
        for e in db.execute(
            select(JournalEntry).where(JournalEntry.source_type == SOURCE_CLOSING)
        ).scalars().all()
    }

    rows: list[PeriodOut] = []
    for p in periods:
        entry = closing_entries.get(p.id)
        rows.append(PeriodOut(
            period=p.period,
            status=p.status,
            net_income=float(period_net_income(db, p.period)),
            closed_at=p.closed_at,
            closed_by=p.closed_by_name,
            closing_entry_id=entry.id if entry else None,
            closing_entry_no=entry.entry_no if entry else "",
        ))

    # 마감은 '끝난 달'만 가능하다 — 이번 달(진행 중)을 안내하면 그 달의 남은 영업이
    # 전부 막히는 함정으로 사용자를 유도하게 된다.
    now = current_period()
    next_closable = next(
        (r.period for r in rows if r.status != "closed" and r.period < now), ""
    )
    return PeriodListOut(periods=rows, next_closable=next_closable)


@router.post("/periods/{period}/close", response_model=PeriodCloseOut)
def close_accounting_period(
    period: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("payment:write")),
):
    """월 마감: 손익계정을 3110 이월이익잉여금으로 대체하는 마감 전표를 만들고 기간을 닫는다.
    마감 후 그 달에는 어떤 기표도 들어갈 수 없다(수기 전표·과거 날짜 결제·원천 삭제 포함).
    오래된 달부터 순서대로만 마감할 수 있다. 위반은 GLError → 400(main 핸들러)."""
    summary = close_period(db, period, actor_id=user.id, actor_name=user.username)
    record_audit(db, user, "UPDATE", "gl_period", period,
                 after={"status": "closed", "net_income": float(summary["net_income"]),
                        "closing_entry_no": summary["closing_entry_no"]})
    db.commit()
    return PeriodCloseOut(
        period=summary["period"],
        net_income=float(summary["net_income"]),
        closing_entry_id=summary["closing_entry_id"],
        closing_entry_no=summary["closing_entry_no"],
    )


@router.post("/periods/{period}/reopen", response_model=PeriodOut)
def reopen_accounting_period(
    period: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("payment:write")),
):
    """마감 해제: 마감 전표를 지우고 기간을 다시 연다(최근 마감분부터 역순으로만).
    수정 후에는 다시 마감해야 재무제표의 이월이익잉여금이 맞는다.

    삭제되는 마감 전표의 스냅샷을 감사 로그 before 에 남긴다 — 전표 자체는 사라지므로
    '누가 언제 3110 에서 얼마를 되돌렸는가'를 사후에 재구성할 유일한 근거다."""
    result = reopen_period(db, period)
    record_audit(db, user, "UPDATE", "gl_period", period,
                 before=result["before"], after={"status": "open"})
    db.commit()
    return PeriodOut(
        period=period, status="open",
        net_income=float(period_net_income(db, period)),
    )


# ---------- 재대사 / 재전기 ----------
@router.get("/reconcile", response_model=ReconcileOut)
def gl_reconcile(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("payment:read")),
):
    """GL 총계 vs 기존 보조원장 대조(§7): 1130==valuation, 1120==AR, 2110==AP,
    시산표 균형. diff!=0 이면 rebuild 를 권고한다(운영 헬스체크용)."""
    return reconcile(db)


@router.post("/rebuild", response_model=GLRebuildOut)
def gl_rebuild(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("payment:write")),
):
    """GL 전체 재전기(백필과 동일 코드 경로, §6): 전 전표 삭제 후 원천 replay.
    재대사가 통과해야 커밋하고, 실패하면 전체 롤백한다(부분 백필 금지)."""
    try:
        summary = rebuild_gl(db)
        rec = reconcile(db)
        if not rec["ok"]:
            db.rollback()
            raise HTTPException(status_code=409, detail=f"재전기 후 재대사 불일치 — 롤백했습니다: {rec}")
    except GLError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    record_audit(db, user, "UPDATE", "gl", "rebuild", after=summary)
    db.commit()
    return GLRebuildOut(**summary, reconcile=rec)
