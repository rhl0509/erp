"""총계정원장(GL) 전기 서비스 — 설계: docs/gl-design.md (전기형 B + 재생성 가능).

원칙:
- GL 은 새 진실을 만들지 않는다. 기존 서비스가 이미 확정한 값(공급가액·세액·
  이동평균원가)을 그대로 복식부기 형식으로 옮긴다. 모든 전표는 원천 행의
  결정적 함수라서 최초 전기 == 백필 == 재전기(rebuild)가 같은 코드 경로를 탄다.
- 원천과 같은 DB 트랜잭션에서 전기한다(호출자는 post_movement/post_return/
  payments 라우터). 이동은 성공했는데 전표가 없는 상태는 구조적으로 불가능.
- (source_type, source_id) UNIQUE 로 원천 1행=전표 1건 멱등성 보장.
- 차대 합 일치·0원 라인 금지는 post_journal 이 flush 전에 강제한다(위반 시
  예외 → 전체 롤백).

무전기(§4-H): TRANSFER(회사 전체 평가액 불변), 세금계산서 발행/취소(이동 시점에
이미 인식 — 발행 시 또 전기하면 이중계상), PO/SO 확정·취소·draft 삭제.
"""
import calendar
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select, delete, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import (
    Account, AccountingPeriod, JournalEntry, JournalLine, StockMovement,
    StockBalance, Payment,
)
from .services import generate_no_lock_split


class GLError(Exception):
    """GL 정합성 위반(차대 불일치, 계정 미정의, 재대사 실패, 마감 기간 기표 등)."""


class PeriodClosedError(GLError):
    """마감된 회계기간을 바꾸려는 시도(소급 기표·마감분 삭제)."""


# ---------- 계정과목 (docs/gl-design.md §3 — 시드 12계정 고정) ----------
ACC_CASH = "1110"        # 현금및예금 — 지급/수금 상대, partner 없는 유상 이동의 상대(§4 엣지)
ACC_AR = "1120"          # 외상매출금 (AR)
ACC_INVENTORY = "1130"   # 상품 (재고자산) — 잔액 == Σ on_hand×avg_cost
ACC_VAT_IN = "1140"      # 부가세대급금 (매입세액)
ACC_AP = "2110"          # 외상매입금 (AP)
ACC_VAT_OUT = "2120"     # 부가세예수금 (매출세액)
ACC_RETAINED = "3110"    # 이월이익잉여금/기초자본 (기초잔액 수기 전표 전용)
ACC_SALES = "4110"       # 상품매출 (반품은 차변 기입 — 순액 표시)
ACC_ADJ_GAIN = "4910"    # 재고조정이익 (ADJUST 증가분)
ACC_COGS = "5110"        # 매출원가 (유상 출고만 — margin 리포트와 정의 일치)
ACC_SHRINK = "5120"      # 재고감모손실 (0원 출고, ADJUST 감소분 — §9-3)
ACC_COST_VAR = "5130"    # 재고원가차이 (매입반품 매입단가−이동평균 차이 — §9-2)

# (code, name, account_type, normal_side)
ACCOUNTS_SEED: list[tuple[str, str, str, str]] = [
    (ACC_CASH,      "현금및예금",          "asset",     "debit"),
    (ACC_AR,        "외상매출금",          "asset",     "debit"),
    (ACC_INVENTORY, "상품",                "asset",     "debit"),
    (ACC_VAT_IN,    "부가세대급금",        "asset",     "debit"),
    (ACC_AP,        "외상매입금",          "liability", "credit"),
    (ACC_VAT_OUT,   "부가세예수금",        "liability", "credit"),
    (ACC_RETAINED,  "이월이익잉여금",      "equity",    "credit"),
    (ACC_SALES,     "상품매출",            "revenue",   "credit"),
    (ACC_ADJ_GAIN,  "재고조정이익",        "revenue",   "credit"),
    (ACC_COGS,      "매출원가",            "expense",   "debit"),
    (ACC_SHRINK,    "재고감모손실",        "expense",   "debit"),
    (ACC_COST_VAR,  "재고원가차이",        "expense",   "debit"),
]
_SEED_BY_CODE = {c: (n, t, s) for c, n, t, s in ACCOUNTS_SEED}

# 재대사 허용 오차(원): MySQL 은 avg_cost 를 DECIMAL(18,4)로 반올림 저장하므로
# GL(정확한 공급가 합산)과 valuation(반올림된 평균×수량)이 원 미만에서 어긋날 수 있다.
# SQLite 테스트에서는 diff 가 정확히 0 이어야 한다(테스트가 단언).
RECON_TOLERANCE = Decimal("1")


def ensure_accounts(db: Session) -> None:
    """계정과목 12개를 멱등 시드한다(마이그레이션 시드와 동일 내용).
    SQLite(create_all 경로)·시드 스크립트에서 호출한다."""
    existing = {c for (c,) in db.execute(select(Account.code)).all()}
    for code, name, atype, side in ACCOUNTS_SEED:
        if code not in existing:
            db.add(Account(code=code, name=name, account_type=atype, normal_side=side))
    db.flush()


def _account_id(db: Session, code: str) -> int:
    """계정코드 → id. 없으면 시드 정의로 즉석 생성(마이그레이션을 안 거치는
    create_all 경로 방어). 동시 최초 생성 경합은 unique(code) 충돌을
    세이브포인트로 흡수하고 상대가 만든(커밋된) 행을 다시 읽는다."""
    acc_id = db.execute(select(Account.id).where(Account.code == code)).scalar_one_or_none()
    if acc_id is not None:
        return acc_id
    meta = _SEED_BY_CODE.get(code)
    if meta is None:
        raise GLError(f"미정의 계정코드: {code}")
    savepoint = db.begin_nested()
    try:
        acc = Account(code=code, name=meta[0], account_type=meta[1], normal_side=meta[2])
        db.add(acc)
        db.flush()
        savepoint.commit()
        return acc.id
    except IntegrityError:
        savepoint.rollback()
        return db.execute(select(Account.id).where(Account.code == code)).scalar_one()


# ---------- 기간 마감 (period close) ----------
# 마감 전표의 원천 유형. rebuild 는 MANUAL 과 함께 이 전표를 보존한다(원천이 없어
# 재생성 불가). UNIQUE(source_type, source_id) 가 기간당 마감 전표 1건을 보장한다.
SOURCE_CLOSING = "CLOSING"
SOURCE_MANUAL = "MANUAL"


def period_of(entry_date: str) -> str:
    """'YYYY-MM-DD' → 'YYYY-MM'. 회계기간은 월 단위다."""
    return entry_date[:7]


def month_end(period: str) -> str:
    """'YYYY-MM' → 그 달의 말일 'YYYY-MM-DD'(마감 전표의 전표일자)."""
    year, month = int(period[:4]), int(period[5:7])
    return f"{period}-{calendar.monthrange(year, month)[1]:02d}"


def valid_date(entry_date: str) -> str:
    """전표일자를 진짜 날짜로 검증한다('2026-06-31'·'2026-6-15'·'' 같은 값을 막는다).

    이게 없으면 차단 기준(period_of = 앞 7자)과 마감 합산 기준(월초~월말 문자열 범위)이
    갈라져, 존재하지 않는 날짜의 전표가 '마감된 달에 속하지만 마감 대체분개에서는 빠지는'
    상태가 된다 — 마감된 달의 손익이 0 이 되지 않는다."""
    try:
        parsed = date.fromisoformat(entry_date)
    except (ValueError, TypeError):
        raise GLError(f"전표일자 형식이 올바르지 않습니다(YYYY-MM-DD): {entry_date!r}")
    return parsed.isoformat()


def closed_periods(db: Session) -> set[str]:
    return {
        p for (p,) in db.execute(
            select(AccountingPeriod.period).where(AccountingPeriod.status == "closed")
        ).all()
    }


def close_watermark(db: Session) -> str:
    """마감선 = 마감된 기간 중 가장 최근 달. 없으면 빈 문자열.
    이 달 **이하**의 모든 기간이 얼어붙는다(아래 assert_period_open)."""
    latest = db.execute(
        select(func.max(AccountingPeriod.period)).where(AccountingPeriod.status == "closed")
    ).scalar_one()
    return latest or ""


def _get_or_create_period(db: Session, period: str, *, lock: bool = False) -> AccountingPeriod:
    """기간 행 get-or-create. 동시 생성 경합은 unique(period) 충돌을 세이브포인트로
    흡수하고 상대가 만든 행을 다시 읽는다(_account_id 와 같은 패턴).

    lock=True 면 행을 FOR UPDATE 로 선점한다 — 마감/해제가 이 락을 잡고, 기표는
    같은 행을 FOR SHARE 로 읽으므로(assert_period_open) 둘이 직렬화된다."""
    stmt = select(AccountingPeriod).where(AccountingPeriod.period == period)
    if lock:
        stmt = stmt.with_for_update()
    row = db.execute(stmt).scalar_one_or_none()
    if row is not None:
        return row

    savepoint = db.begin_nested()
    try:
        row = AccountingPeriod(period=period, status="open")
        db.add(row)
        db.flush()
        savepoint.commit()
        return row
    except IntegrityError:
        savepoint.rollback()
        stmt2 = select(AccountingPeriod).where(AccountingPeriod.period == period)
        if lock:
            stmt2 = stmt2.with_for_update()
        return db.execute(stmt2).scalar_one()


def is_period_closed(db: Session, entry_date: str) -> bool:
    """그 날짜가 마감선 이하인가(= 얼어붙은 기간인가)."""
    watermark = close_watermark(db)
    return bool(watermark) and period_of(valid_date(entry_date)) <= watermark


def assert_period_open(db: Session, entry_date: str, what: str = "기표") -> None:
    """마감된 회계기간을 건드리는 모든 경로의 단일 관문.

    전기(post_journal)와 전표 삭제(delete_for_source)가 이 함수를 통과하므로,
    수기 전표·과거 날짜 결제·마감월 재고이동·원천 삭제가 전부 여기서 막힌다.

    판정 기준은 '그 달이 closed 인가'가 아니라 **마감선(가장 최근 마감월) 이하인가**다.
    앞선 달이 아직 열려 있다는 이유로 소급 기표를 허용하면, 이미 마감·보고한 누계
    재무상태표가 나중에 바뀐다(마감의 의미가 사라진다).

    기간 행을 FOR SHARE 로 잡아, 검사 통과 후 커밋 전에 그 달이 마감되는 경합
    (마감 트랜잭션은 FOR UPDATE 를 잡는다)을 차단한다. SQLite 는 단일 writer 라 무해.
    """
    entry_date = valid_date(entry_date)
    period = period_of(entry_date)

    # 기간 행을 확보하고 공유 잠금 — 마감(FOR UPDATE)과 직렬화된다.
    row = _get_or_create_period(db, period)
    locked = db.execute(
        select(AccountingPeriod)
        .where(AccountingPeriod.id == row.id)
        .with_for_update(read=True)
    ).scalar_one()

    watermark = close_watermark(db)
    if locked.status == "closed" or (watermark and period <= watermark):
        raise PeriodClosedError(
            f"{period} 회계기간이 마감되어 {what}할 수 없습니다"
            + (f"(마감선 {watermark})" if watermark and period < watermark else "")
            + ". 필요하면 해당 기간의 마감을 해제한 뒤 다시 시도하세요."
        )


def current_period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def ensure_periods(db: Session) -> list[AccountingPeriod]:
    """전표가 존재하는 첫 달부터 이번 달까지의 기간 행을 채우고 전부 돌려준다.
    (행은 마감 상태를 담는 그릇일 뿐이라 미리 만들 필요가 없어 지연 생성한다.)"""
    first = db.execute(select(func.min(JournalEntry.entry_date))).scalar_one()
    now = current_period()
    start = period_of(first) if first else now

    wanted: list[str] = []
    year, month = int(start[:4]), int(start[5:7])
    while f"{year:04d}-{month:02d}" <= now:
        wanted.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year, month = year + 1, 1

    for period in wanted:
        _get_or_create_period(db, period)   # 동시 생성 경합은 세이브포인트로 흡수
    db.flush()
    return sorted(
        db.execute(select(AccountingPeriod)).scalars().all(),
        key=lambda p: p.period,
    )


def period_net_income(db: Session, period: str) -> Decimal:
    """그 달의 당기순이익 = 수익 − 비용 (마감 전표 자체는 제외).
    마감 전표를 빼야 마감 후에도 '그 달이 얼마를 벌었는지'가 그대로 보인다."""
    start, end = f"{period}-01", month_end(period)
    rows = db.execute(
        select(Account.account_type,
               func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0))
        .join(JournalLine, JournalLine.account_id == Account.id)
        .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
        .where(
            Account.account_type.in_(("revenue", "expense")),
            JournalEntry.source_type != SOURCE_CLOSING,
            JournalEntry.entry_date >= start,
            JournalEntry.entry_date <= end,
        )
        .group_by(Account.account_type)
    ).all()
    nets = {t: _dec(v) for t, v in rows}
    # 수익은 대변 정상잔액이라 순차변이 음수 → 부호를 뒤집어 더한다.
    return -nets.get("revenue", Decimal("0")) - nets.get("expense", Decimal("0"))


def close_period(db: Session, period: str, *, actor_id=None, actor_name: str = "") -> dict:
    """월 마감: 손익계정 잔액을 3110 이월이익잉여금으로 대체하는 CLOSING 전표를
    만들고 기간을 닫는다. 이후 그 달 **이하**의 모든 기간에는 기표가 들어갈 수 없다.

    규칙:
    - 아직 끝나지 않은 달(이번 달·미래)은 마감할 수 없다 — 마감하면 그 달의 남은
      영업(재고 입출고·결제)이 전부 막혀 업무가 선다.
    - 앞선 기간이 열려 있으면 마감할 수 없다(구멍 뚫린 마감 방지 — 순서대로만).
    - 손익 활동이 없으면 전표 없이 기간만 닫는다(0원 라인 금지 원칙).
    - 대상 기간 행을 FOR UPDATE 로 선점한 뒤 상태를 다시 확인한다 — 같은 순간 그 달에
      기표하려는 트랜잭션(FOR SHARE)과 직렬화되어, 마감 후 전표가 끼어드는 경합을 막는다.
    """
    if len(period) != 7 or period[4] != "-" or not period[:4].isdigit() or not period[5:].isdigit():
        raise GLError("회계기간 형식이 올바르지 않습니다(YYYY-MM)")
    if not 1 <= int(period[5:]) <= 12:
        raise GLError("회계기간 형식이 올바르지 않습니다(YYYY-MM)")
    if period >= current_period():
        raise GLError(
            f"아직 끝나지 않은 기간({period})은 마감할 수 없습니다. "
            "월 마감은 그 달이 지난 뒤에 합니다."
        )

    ensure_periods(db)
    target = _get_or_create_period(db, period, lock=True)   # 마감 경합 직렬화
    if target.status == "closed":
        raise GLError(f"{period} 기간은 이미 마감되었습니다")

    periods = sorted(db.execute(select(AccountingPeriod)).scalars().all(),
                     key=lambda p: p.period)
    earlier_open = [p.period for p in periods if p.period < period and p.status != "closed"]
    if earlier_open:
        raise GLError(
            f"앞선 기간이 아직 열려 있습니다: {', '.join(earlier_open)}. "
            "회계기간은 오래된 달부터 순서대로 마감해야 합니다."
        )

    start, end = f"{period}-01", month_end(period)
    # 그 달의 손익계정별 순잔액. CLOSING 은 제외한다 — 지금은 그 달의 마감 전표가
    # 아직 없어 결과가 같지만, 정의를 period_net_income 과 하나로 맞춰 둔다.
    rows = db.execute(
        select(Account.code, Account.account_type,
               func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0))
        .join(JournalLine, JournalLine.account_id == Account.id)
        .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
        .where(
            Account.account_type.in_(("revenue", "expense")),
            JournalEntry.source_type != SOURCE_CLOSING,
            JournalEntry.entry_date >= start,
            JournalEntry.entry_date <= end,
        )
        .group_by(Account.code, Account.account_type)
    ).all()

    lines: list[tuple] = []
    net_income = Decimal("0")
    for code, _atype, net in rows:
        net = _dec(net)          # 순차변(Σdebit − Σcredit)
        if net == 0:
            continue
        # 잔액을 반대편에 기입해 손익계정을 0 으로 만든다.
        if net > 0:              # 차변 잔액(비용이 전형) → 대변으로 상계
            lines.append(_line(code, credit=net, memo=f"{period} 마감대체"))
            net_income -= net
        else:                    # 대변 잔액(수익이 전형) → 차변으로 상계
            lines.append(_line(code, debit=-net, memo=f"{period} 마감대체"))
            net_income += -net

    entry = None
    if lines:
        # 상계 합계의 반대편 = 당기순이익(이익이면 자본 증가 → 3110 대변)
        if net_income > 0:
            lines.append(_line(ACC_RETAINED, credit=net_income, memo=f"{period} 당기순이익 대체"))
        else:
            lines.append(_line(ACC_RETAINED, debit=-net_income, memo=f"{period} 당기순손실 대체"))

        entry = post_journal(
            db, source_type=SOURCE_CLOSING, source_id=target.id,
            entry_date=end, description=f"{period} 기간 마감 대체분개",
            lines=lines, allow_closed_period=True,   # 지금 닫는 중인 기간에는 기표해야 한다
        )

    target.status = "closed"
    target.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    target.closed_by_id = actor_id
    target.closed_by_name = actor_name
    target.net_income = net_income
    db.flush()

    return {
        "period": period,
        "net_income": net_income,
        "closing_entry_id": entry.id if entry else None,
        "closing_entry_no": entry.entry_no if entry else "",
    }


def reopen_period(db: Session, period: str) -> dict:
    """마감 해제: 마감 전표를 지우고 기간을 다시 연다.
    가장 최근에 마감된 기간부터 역순으로만 해제할 수 있다(중간 기간만 열면
    그 뒤 기간들의 이월이익잉여금이 어긋난다).

    돌려주는 dict 는 삭제한 마감 전표의 스냅샷을 담는다 — 전표 자체는 사라지므로
    감사 로그에 '무엇을 되돌렸는지'를 남길 수 있는 유일한 근거다(호출자가 기록한다)."""
    ensure_periods(db)
    target = _get_or_create_period(db, period, lock=True)   # 마감/기표와 직렬화
    if target.status != "closed":
        raise GLError(f"{period} 기간은 마감되어 있지 않습니다")

    later_closed = db.execute(
        select(AccountingPeriod.period).where(
            AccountingPeriod.period > period,
            AccountingPeriod.status == "closed",
        ).order_by(AccountingPeriod.period)
    ).scalars().all()
    if later_closed:
        raise GLError(
            f"이후 기간이 마감되어 있습니다: {', '.join(later_closed)}. "
            "마감 해제는 최근 기간부터 역순으로만 할 수 있습니다."
        )

    entry = db.execute(
        select(JournalEntry).where(
            JournalEntry.source_type == SOURCE_CLOSING,
            JournalEntry.source_id == target.id,
        )
    ).scalar_one_or_none()

    before = {
        "closed_at": target.closed_at,
        "closed_by": target.closed_by_name,
        "net_income": float(target.net_income or 0),
        "closing_entry_no": entry.entry_no if entry else "",
        "closing_lines": [
            {"account_id": l.account_id, "debit": float(l.debit), "credit": float(l.credit)}
            for l in (entry.lines if entry else [])
        ],
    }
    if entry is not None:
        db.delete(entry)

    target.status = "open"
    target.closed_at = None
    target.closed_by_id = None
    target.closed_by_name = ""
    target.net_income = Decimal("0")
    db.flush()
    return {"period": period, "reopened": True, "before": before}


# ---------- 전기 코어 ----------
def _line(code: str, debit=0, credit=0, partner_id: int | None = None, memo: str = ""):
    """분개 라인 스펙 튜플. 금액은 Decimal 로 정규화한다."""
    return (code, Decimal(debit), Decimal(credit), partner_id, memo)


def post_journal(
    db: Session,
    *,
    source_type: str,
    source_id: int | None,
    entry_date: str,
    description: str,
    lines: list[tuple],
    allow_closed_period: bool = False,
) -> JournalEntry | None:
    """전표 1건을 전기한다(모든 전기 함수의 단일 경로).

    - 멱등: (source_type, source_id) 전표가 이미 있으면 그대로 반환(재전기 안 함).
    - 0원 라인은 버린다(0원 라인 금지). 유효 라인이 없으면 전표 자체를 만들지
      않는다(예: 원가 0 재고의 0원 출고 — 회계 영향 없음).
    - 차대 합 불일치·음수 금액·차대 동시 기입은 GLError → 전체 트랜잭션 롤백.
    - **마감된 회계기간에는 기표할 수 없다**(PeriodClosedError). 모든 전기가 이 함수를
      지나므로 수기 전표·과거 날짜 결제·마감월 재고이동이 여기서 함께 막힌다.
      allow_closed_period 는 마감 대체분개 자신(close_period)만 쓴다.
    - 전표번호는 이동번호와 같은 락분리 채번(JV-YYYYMM-####, 결번 허용).
    """
    effective = [l for l in lines if l[1] != 0 or l[2] != 0]
    if not effective:
        return None

    if not allow_closed_period:
        assert_period_open(db, entry_date, "기표")

    if source_id is not None:
        existing = db.execute(
            select(JournalEntry).where(
                JournalEntry.source_type == source_type,
                JournalEntry.source_id == source_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    total_debit = Decimal("0")
    total_credit = Decimal("0")
    for code, d, c, _pid, _memo in effective:
        if d < 0 or c < 0:
            raise GLError(f"분개 금액은 음수가 될 수 없습니다 ({code}: {d}/{c})")
        if d > 0 and c > 0:
            raise GLError(f"한 라인에 차변과 대변을 동시에 기입할 수 없습니다 ({code})")
        total_debit += d
        total_credit += c
    if total_debit != total_credit:
        raise GLError(
            f"차대 불일치: 차변 {total_debit} != 대변 {total_credit} "
            f"({source_type}#{source_id})"
        )

    entry = JournalEntry(
        entry_no=generate_no_lock_split(db, "JE", "JV"),
        entry_date=entry_date,
        description=description,
        source_type=source_type,
        source_id=source_id,
    )
    for i, (code, d, c, pid, memo) in enumerate(effective, start=1):
        entry.lines.append(JournalLine(
            line_no=i, account_id=_account_id(db, code),
            debit=d, credit=c, partner_id=pid, memo=memo,
        ))
    db.add(entry)
    db.flush()
    return entry


def delete_for_source(db: Session, source_type: str, source_id: int) -> bool:
    """원천 삭제 시 전표 동반 삭제(§9-4 — 삭제 이력은 AuditLog 가 담당).
    같은 트랜잭션에서 호출해야 한다. ORM delete 로 라인 cascade 를 보장한다
    (SQLite 는 FK cascade 미적용이 기본이라 DB cascade 에 의존하지 않는다).

    마감된 기간의 전표는 지울 수 없다 — 원천(재고이동·결제)을 삭제해 과거 마감분의
    숫자를 바꾸는 경로도 여기서 막힌다(PeriodClosedError → 원천 삭제 자체가 400)."""
    entry = db.execute(
        select(JournalEntry).where(
            JournalEntry.source_type == source_type,
            JournalEntry.source_id == source_id,
        )
    ).scalar_one_or_none()
    if entry is None:
        return False
    assert_period_open(db, entry.entry_date, "전표를 삭제")
    db.delete(entry)
    db.flush()
    return True


# ---------- 경제사건별 전기 (분개 매핑표 §4 그대로) ----------
def post_for_movement(
    db: Session, movement: StockMovement, *, avg_cost: Decimal | None = None,
) -> JournalEntry | None:
    """재고 이동 1건을 사건 판별해 전기한다. post_movement/post_return 이
    flush 직후(같은 트랜잭션) 호출하고, rebuild 도 동일 함수를 쓴다.

    avg_cost: 그 시점 창고별 이동평균원가(apply_stock_delta 반환값 또는 rebuild
    시뮬레이터 값). 매입반품(§4-C)에서만 필수 — movement.cost 에는 매입단가가
    저장되므로 재고 대변은 이 값으로 잡아야 1130 잔액 == valuation 이 유지된다.
    """
    mtype = movement.movement_type
    if mtype == "TRANSFER":
        return None   # §4-H: 회사 전체 재고평가액 불변 — 무전기

    qty = movement.quantity
    aq = abs(qty)
    supply = Decimal(aq * movement.unit_price)              # 공급가액(절대값)
    tax = abs(Decimal(movement.tax_amount or 0))            # 세액(절대값)
    total = supply + tax                                    # AP/AR 청구액(VAT 포함)
    cost_amt = Decimal(movement.cost or 0) * aq             # qty×이동원가
    pid = movement.partner_id
    entry_date = (movement.created_at or datetime.now()).strftime("%Y-%m-%d")

    lines: list[tuple] = []
    if mtype == "IN" and qty > 0:
        # A. 매입입고 — 차) 상품 supply + 부가세대급금 tax / 대) 외상매입금 total.
        # 거래처 없는 유상 수기 입고는 aging 집계에서 빠지므로 GL 도 AP 대신
        # 현금및예금을 상대로(즉시 현금거래 간주) — GL AP == aging(AP) 유지.
        desc = f"매입입고 {movement.movement_no}"
        credit_acc = ACC_AP if pid is not None else ACC_CASH
        lines = [
            _line(ACC_INVENTORY, debit=supply),
            _line(ACC_VAT_IN, debit=tax),
            _line(credit_acc, credit=total, partner_id=pid),
        ]
    elif mtype == "IN" and qty < 0:
        # C. 매입반품(PRET) — AP 차감은 반품단가(total), 재고 대변은 반품 시점
        # 이동평균(aq×avg), 차액은 5130 재고원가차이(§9-2 확정).
        if avg_cost is None:
            raise GLError(f"매입반품 전기에는 반품 시점 이동평균원가가 필요합니다 ({movement.movement_no})")
        desc = f"매입반품 {movement.movement_no}"
        inv_credit = Decimal(avg_cost) * aq
        diff = supply - inv_credit   # >0: 매입단가>평균 → 대변 5130 / <0: 차변 5130
        debit_acc = ACC_AP if pid is not None else ACC_CASH
        lines = [
            _line(debit_acc, debit=total, partner_id=pid),
            _line(ACC_INVENTORY, credit=inv_credit),
            _line(ACC_VAT_IN, credit=tax),
        ]
        if diff > 0:
            lines.append(_line(ACC_COST_VAR, credit=diff, memo="매입단가>이동평균 차이"))
        elif diff < 0:
            lines.append(_line(ACC_COST_VAR, debit=-diff, memo="매입단가<이동평균 차이"))
    elif mtype == "OUT" and qty > 0 and movement.unit_price > 0:
        # B. 매출출고 — 차) AR total + 매출원가 / 대) 매출 supply + 예수금 tax + 상품.
        # cost = 출고 시점 이동평균(movement.cost) — margin 리포트 COGS 와 동일 값.
        desc = f"매출출고 {movement.movement_no}"
        debit_acc = ACC_AR if pid is not None else ACC_CASH
        lines = [
            _line(debit_acc, debit=total, partner_id=pid),
            _line(ACC_SALES, credit=supply),
            _line(ACC_VAT_OUT, credit=tax),
            _line(ACC_COGS, debit=cost_amt),
            _line(ACC_INVENTORY, credit=cost_amt),
        ]
    elif mtype == "OUT" and qty > 0:
        # E. 비매출 출고(0원 — 증정·폐기·감모): 5120 재고감모손실(§9-3 확정 —
        # 5110 에 넣으면 GL COGS != margin COGS). AR·매출·세액 라인 없음.
        desc = f"재고감모(0원 출고) {movement.movement_no}"
        lines = [
            _line(ACC_SHRINK, debit=cost_amt),
            _line(ACC_INVENTORY, credit=cost_amt),
        ]
    elif mtype == "OUT" and qty < 0:
        # D. 매출반품(SRET) — B 의 역방향. cost = 반품 시점 이동평균(원 COGS 근사,
        # 기존 정책 그대로). 재고 증가가 평균 불변·수량 증가라 valuation 과 일치.
        desc = f"매출반품 {movement.movement_no}"
        credit_acc = ACC_AR if pid is not None else ACC_CASH
        lines = [
            _line(ACC_SALES, debit=supply),
            _line(ACC_VAT_OUT, debit=tax),
            _line(credit_acc, credit=total, partner_id=pid),
            _line(ACC_INVENTORY, debit=cost_amt),
            _line(ACC_COGS, credit=cost_amt),
        ]
    elif mtype == "ADJUST":
        # F. 재고조정 — 증가: 재고조정이익(4910) / 감소: 재고감모손실(5120).
        desc = f"재고조정 {movement.movement_no}"
        if qty > 0:
            lines = [
                _line(ACC_INVENTORY, debit=cost_amt),
                _line(ACC_ADJ_GAIN, credit=cost_amt),
            ]
        else:
            lines = [
                _line(ACC_SHRINK, debit=cost_amt),
                _line(ACC_INVENTORY, credit=cost_amt),
            ]
    else:
        return None

    if movement.note:
        desc = f"{desc} — {movement.note}"
    return post_journal(
        db, source_type="MOVEMENT", source_id=movement.id,
        entry_date=entry_date, description=desc[:255], lines=lines,
    )


def post_for_payment(db: Session, payment: Payment) -> JournalEntry | None:
    """G. 결제 — AP 지급: 차) 외상매입금 / 대) 현금및예금.
    AR 수금: 차) 현금및예금 / 대) 외상매출금. method 구분 없이 1110 단일(§9-7)."""
    amount = Decimal(payment.amount)
    entry_date = payment.pay_date or (payment.created_at or datetime.now()).strftime("%Y-%m-%d")
    if payment.kind == "AP":
        desc = f"지급 {payment.payment_no}"
        lines = [
            _line(ACC_AP, debit=amount, partner_id=payment.partner_id),
            _line(ACC_CASH, credit=amount),
        ]
    else:  # AR
        desc = f"수금 {payment.payment_no}"
        lines = [
            _line(ACC_CASH, debit=amount),
            _line(ACC_AR, credit=amount, partner_id=payment.partner_id),
        ]
    return post_journal(
        db, source_type="PAYMENT", source_id=payment.id,
        entry_date=entry_date, description=desc, lines=lines,
    )


# ---------- S6: 수기 전표 (MANUAL) ----------
def post_manual_entry(
    db: Session,
    *,
    entry_date: str,
    description: str,
    lines: list[dict],
) -> JournalEntry:
    """수기 전표(기초잔액·마감·수정 분개용, §2·§9-6 — 3110 상대가 전형).

    source_id=None 이라 멱등 대상이 아니고(항상 신규 생성), rebuild 가 보존한다
    (자동 전기와 달리 원천이 없어 재생성이 불가능하므로). 차대 균형·0원 라인
    금지·계정 존재는 post_journal 이 강제하며, 위반 시 GLError → 롤백.

    lines: [{account_code, debit?, credit?, partner_id?, memo?}, ...]
    """
    specs = [
        _line(
            l["account_code"],
            debit=l.get("debit") or 0,
            credit=l.get("credit") or 0,
            partner_id=l.get("partner_id"),
            memo=l.get("memo") or "",
        )
        for l in lines
    ]
    entry = post_journal(
        db, source_type="MANUAL", source_id=None,
        entry_date=entry_date, description=description, lines=specs,
    )
    if entry is None:
        raise GLError("유효한 분개 라인이 없습니다(모든 라인이 0원).")
    return entry


# ---------- S3: 백필/재전기 (rebuild) ----------
def rebuild_gl(db: Session) -> dict:
    """GL 전체 재전기: 전 전표 삭제 후 원천을 시간순 replay(§6).
    최초 전기와 동일한 매핑 함수(post_for_movement/post_for_payment)를 쓰므로
    드리프트가 원천 차단된다. 커밋은 호출자가 한다(실패 시 전체 롤백 — 부분 백필 금지).

    매입반품의 '반품 시점 이동평균'은 원천에 저장돼 있지 않은 유일한 값이라,
    (item_id, warehouse_id)별 이동평균 시뮬레이터를 apply_stock_delta 와 동일
    수식으로 유지해 복원한다. replay 종료 시 시뮬레이터가 현재 stock_balances 와
    일치해야 하며, 불일치면 GLError 로 중단한다(과거 이력 단절 신호).

    S6 MANUAL 전표(기초잔액·수기 분개)와 CLOSING 마감 전표는 원천이 없어 재생성
    불가하므로 보존한다(자동 전기분 MOVEMENT/PAYMENT 만 삭제·replay). 삭제하면
    재전기가 곧 데이터 손실이 된다.

    **마감된 기간이 하나라도 있으면 재전기하지 않는다** — replay 는 마감된 달의 전표까지
    다시 만들어 마감분 숫자를 바꿀 수 있다. 정말 필요하면 해당 기간을 마감 해제한 뒤
    재전기하고 다시 마감한다."""
    closed = sorted(closed_periods(db))
    if closed:
        raise PeriodClosedError(
            f"마감된 회계기간이 있어 재전기할 수 없습니다: {', '.join(closed)}. "
            "재전기가 꼭 필요하면 해당 기간의 마감을 해제한 뒤 실행하고, 끝나면 다시 마감하세요."
        )

    # 1) 자동 전기분 전표만 삭제(MANUAL·CLOSING 보존). 초기 백필이면 0건.
    preserved = (SOURCE_MANUAL, SOURCE_CLOSING)
    auto_entry_ids = select(JournalEntry.id).where(JournalEntry.source_type.notin_(preserved))
    db.execute(delete(JournalLine).where(JournalLine.entry_id.in_(auto_entry_ids)))
    db.execute(delete(JournalEntry).where(JournalEntry.source_type.notin_(preserved)))
    db.flush()
    ensure_accounts(db)

    # 2) 이동 replay (id 오름차순 = 발생 순서)
    sim: dict[tuple[int, int], list] = {}   # key -> [on_hand, avg_cost(Decimal)]
    movement_entries = 0
    movements = db.execute(
        select(StockMovement).order_by(StockMovement.id.asc())
    ).scalars().all()
    for m in movements:
        key = (m.item_id, m.warehouse_id)
        st = sim.setdefault(key, [0, Decimal("0")])
        delta = -m.quantity if m.movement_type == "OUT" else m.quantity
        # apply_stock_delta 와 동일 규칙: IN(양수)=매입단가 가중평균 갱신,
        # TRANSFER 입고행=이전원가(movement.cost)로 가중평균 갱신,
        # 그 외(OUT/PRET/SRET/ADJUST/TRANSFER 출고행)=평균 불변·수량만 증감.
        unit_cost: Decimal | None = None
        if m.movement_type == "IN" and m.quantity > 0:
            unit_cost = Decimal(m.unit_price)
        elif m.movement_type == "TRANSFER" and m.quantity > 0:
            unit_cost = Decimal(m.cost or 0)
        new_on_hand = st[0] + delta
        if unit_cost is not None and delta != 0 and new_on_hand > 0:
            st[1] = (st[0] * st[1] + delta * unit_cost) / new_on_hand
        st[0] = new_on_hand

        # 평균 불변 사건(PRET 포함)은 갱신 후 값 == 그 시점 평균이므로 st[1] 전달.
        entry = post_for_movement(db, m, avg_cost=st[1])
        if entry is not None:
            movement_entries += 1

    # 3) 결제 replay (pay_date, id 순)
    payment_entries = 0
    payments = db.execute(
        select(Payment).order_by(Payment.pay_date.asc(), Payment.id.asc())
    ).scalars().all()
    for p in payments:
        if post_for_payment(db, p) is not None:
            payment_entries += 1

    # 4) 검증: 시뮬레이터 == 현재 잔고 캐시 (불일치 시 백필 중단·전체 롤백)
    balances = {
        (b.item_id, b.warehouse_id): b
        for b in db.execute(select(StockBalance)).scalars().all()
    }
    mismatches: list[str] = []
    for key, (on_hand, avg) in sim.items():
        b = balances.get(key)
        b_on_hand = b.on_hand if b else 0
        b_avg = Decimal(b.avg_cost) if b else Decimal("0")
        if on_hand != b_on_hand or abs(avg - b_avg) > Decimal("0.01"):
            mismatches.append(
                f"item={key[0]} wh={key[1]}: replay on_hand={on_hand}/avg={avg} "
                f"!= balance on_hand={b_on_hand}/avg={b_avg}"
            )
    for key, b in balances.items():
        if key not in sim and b.on_hand != 0:
            mismatches.append(f"item={key[0]} wh={key[1]}: 이동 이력 없이 잔고 {b.on_hand}")
    if mismatches:
        raise GLError("재전기 검증 실패(이동 이력이 현재 잔고를 재현하지 못함): " + "; ".join(mismatches))

    return {
        "movement_entries": movement_entries,
        "payment_entries": payment_entries,
        "total_entries": movement_entries + payment_entries,
    }


# ---------- S3: 재대사 (reconciliation) ----------
def _dec(v) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v or 0))


def _gl_net_debit(db: Session, code: str, *, auto_only: bool = False) -> Decimal:
    """계정의 GL 순차변 잔액(Σdebit − Σcredit).

    auto_only=True 면 자동 전기분(MOVEMENT/PAYMENT)만 합산한다. 재고·AR·AP
    재대사는 보조원장(valuation·aging)이 자동 전기분만 반영하므로, MANUAL 수기
    전표(기초잔액 등)와 CLOSING 마감 전표를 제외해야 '자동 전기 == 보조원장'
    불변식이 유지된다(그래야 수기·마감 전표가 있어도 재대사가 통과한다)."""
    stmt = (
        select(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0))
        .join(Account, JournalLine.account_id == Account.id)
        .where(Account.code == code)
    )
    if auto_only:
        stmt = stmt.join(JournalEntry, JournalLine.entry_id == JournalEntry.id).where(
            JournalEntry.source_type.notin_((SOURCE_MANUAL, SOURCE_CLOSING))
        )
    return _dec(db.execute(stmt).scalar_one())


def reconcile(db: Session) -> dict:
    """GL 총계와 기존 보조원장(원천 파생값)을 대조한다(§7). 전 항목 diff=0 이
    정상이며, diff 발생 시 rebuild 를 권고한다.

    - 재고자산(1130) == Σ on_hand×avg_cost (valuation)
    - 외상매출금(1120) == Σ(OUT·partner 있음: 수량×단가+세액) − Σ AR 수금
      (aging 총액과 동일 정의 — 부호 있는 합계라 과수금 시 음수 가능)
    - 외상매입금(2110) == Σ(IN·partner 있음) − Σ AP 지급
    - 시산표: Σ차변 == Σ대변
    """
    def item(gl: Decimal, expected: Decimal) -> dict:
        diff = gl - expected
        return {
            "gl": float(gl), "expected": float(expected),
            "diff": float(diff), "ok": abs(diff) <= RECON_TOLERANCE,
        }

    # 재고자산 vs valuation
    inv_expected = _dec(db.execute(
        select(func.coalesce(func.sum(StockBalance.on_hand * StockBalance.avg_cost), 0))
    ).scalar_one())
    inventory = item(_gl_net_debit(db, ACC_INVENTORY, auto_only=True), inv_expected)

    # AR/AP vs 원천(납품 기준 청구 − 결제). partner 없는 이동은 GL 도 1110 으로
    # 보냈으므로 여기서도 제외 — 정의가 정확히 맞물린다.
    def _charges(mv_type: str) -> Decimal:
        val = db.execute(
            select(func.coalesce(func.sum(
                StockMovement.quantity * StockMovement.unit_price + StockMovement.tax_amount
            ), 0)).where(
                StockMovement.movement_type == mv_type,
                StockMovement.partner_id.isnot(None),
            )
        ).scalar_one()
        return _dec(val)

    def _paid(kind: str) -> Decimal:
        val = db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.kind == kind)
        ).scalar_one()
        return _dec(val)

    ar = item(_gl_net_debit(db, ACC_AR, auto_only=True), _charges("OUT") - _paid("AR"))
    # AP 는 대변 정상잔액 → 순대변(credit − debit)으로 비교
    ap = item(-_gl_net_debit(db, ACC_AP, auto_only=True), _charges("IN") - _paid("AP"))

    # 시산표 균형
    td, tc = db.execute(
        select(func.coalesce(func.sum(JournalLine.debit), 0),
               func.coalesce(func.sum(JournalLine.credit), 0))
    ).one()
    td, tc = _dec(td), _dec(tc)
    trial = {"debit": float(td), "credit": float(tc), "ok": td == tc}

    return {
        "inventory": inventory, "ar": ar, "ap": ap, "trial": trial,
        "ok": inventory["ok"] and ar["ok"] and ap["ok"] and trial["ok"],
    }
