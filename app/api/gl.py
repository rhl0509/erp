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
)
from ..deps import require_permission
from ..services import paginate, record_audit
from ..gl import rebuild_gl, reconcile, post_manual_entry, GLError

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
            db, entry_date=payload.entry_date, description=payload.description,
            lines=[l.model_dump() for l in payload.lines],
        )
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
    삭제해야 동반 삭제되므로(§9-4) 여기서 지우면 대사가 깨진다 — 400 으로 막는다."""
    e = db.get(JournalEntry, entry_id)
    if not e:
        raise HTTPException(status_code=404, detail="전표를 찾을 수 없습니다")
    if e.source_type != "MANUAL":
        raise HTTPException(
            status_code=400,
            detail="자동 전기된 전표는 원천 문서에서만 삭제할 수 있습니다(수기 전표만 직접 삭제 가능)",
        )
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
) -> list[tuple[Account, Decimal]]:
    """계정유형별 (Account, net_debit=Σdebit−Σcredit). entry_date 기간 필터.
    거래 없는 활성 계정도 0 으로 포함(시산표와 동일 관례). MANUAL 포함 —
    재무제표는 총계 관점이라 수기 분개도 반영해야 한다."""
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
    agg = agg.group_by(JournalLine.account_id).subquery()

    rows = db.execute(
        select(Account, func.coalesce(agg.c.net, 0))
        .outerjoin(agg, agg.c.aid == Account.id)
        .where(Account.account_type.in_(account_types), Account.is_active.is_(True))
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
    비용은 차변 정상잔액이라 각각 부호를 뒤집어 양수로 표시한다."""
    rev = _account_nets(db, ("revenue",), date_from=date_from, date_to=date_to)
    exp = _account_nets(db, ("expense",), date_from=date_from, date_to=date_to)
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
    """간이 재무상태표: 자산 = 부채 + 자본 + 당기순이익(§7). 마감 분개를 두지
    않으므로 당기순이익(손익계정 누계)을 자본 아래 별도 표시한다. 시산표가
    균형이면 항상 대차가 맞는다(각 전표가 자기완결적이므로)."""
    assets = _account_nets(db, ("asset",), date_to=date_to)
    liabs = _account_nets(db, ("liability",), date_to=date_to)
    equity = _account_nets(db, ("equity",), date_to=date_to)
    rev = _account_nets(db, ("revenue",), date_to=date_to)
    exp = _account_nets(db, ("expense",), date_to=date_to)

    asset_rows = [FinancialRow(account_code=a.code, account_name=a.name, amount=float(net)) for a, net in assets]
    liab_rows = [FinancialRow(account_code=a.code, account_name=a.name, amount=float(-net)) for a, net in liabs]
    equity_rows = [FinancialRow(account_code=a.code, account_name=a.name, amount=float(-net)) for a, net in equity]

    total_assets = float(sum((net for _, net in assets), Decimal("0")))
    total_liabilities = float(sum((-net for _, net in liabs), Decimal("0")))
    total_equity = float(sum((-net for _, net in equity), Decimal("0")))
    net_income = float(
        sum((-net for _, net in rev), Decimal("0")) - sum((net for _, net in exp), Decimal("0"))
    )
    tle = total_liabilities + total_equity + net_income
    return BalanceSheetOut(
        date_to=date_to, assets=asset_rows, liabilities=liab_rows, equity=equity_rows,
        total_assets=total_assets, total_liabilities=total_liabilities,
        total_equity=total_equity, net_income=net_income,
        total_liabilities_and_equity=tle,
        balanced=round(total_assets, 4) == round(tle, 4),
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
