import json
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import NumberSequence, AuditLog, StockMovement, StockBalance, Item


class StockError(Exception):
    """재고 부족 등 재고 정합성 위반. 라우터에서 400으로 변환한다."""


# 부가세율(과세). 공급가액(세금 별도) 기준 단가에 곱해 세액을 구한다.
VAT_RATE = Decimal("0.10")


def compute_tax(supply: int | Decimal, taxable: bool) -> int:
    """공급가액과 과세여부로 부가세액(원 단위, 반올림)을 계산한다. 면세면 0."""
    if not taxable:
        return 0
    return int((Decimal(supply) * VAT_RATE).quantize(Decimal("1")))


def paginate(db: Session, stmt, page: int, page_size: int) -> dict:
    """공통 페이지네이션. {items, total, page, page_size, pages} 를 돌려준다."""
    page = max(1, page)
    page_size = min(max(1, page_size), 100)  # 과도한 요청 방어(최대 100)

    total = db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()

    rows = db.execute(
        stmt.limit(page_size).offset((page - 1) * page_size)
    ).scalars().all()

    pages = (total + page_size - 1) // page_size
    return {"items": rows, "total": total, "page": page, "page_size": page_size, "pages": pages}


def _acquire_seq_row(db: Session, seq_key: str, prefix: str, period: str) -> NumberSequence:
    """시퀀스 행을 SELECT ... FOR UPDATE 로 잠가 반환한다(없으면 생성).

    최초 생성 시 두 요청이 동시에 INSERT 하면 uq_seq_key_period 위반이 나는데,
    세이브포인트로 그 INSERT만 롤백하고 상대가 만든 행을 잠가 다시 읽어 재시도한다
    (전체 트랜잭션을 버리지 않고 채번을 이어간다)."""
    def _select_locked():
        return db.execute(
            select(NumberSequence)
            .where(NumberSequence.seq_key == seq_key, NumberSequence.period == period)
            .with_for_update()
        ).scalar_one_or_none()

    row = _select_locked()
    if row is None:
        savepoint = db.begin_nested()
        try:
            row = NumberSequence(seq_key=seq_key, prefix=prefix, period=period, last_seq=0)
            db.add(row)
            db.flush()
            savepoint.commit()
        except IntegrityError:
            savepoint.rollback()
            row = _select_locked()
            if row is None:
                raise
    return row


def generate_code(
    db: Session,
    seq_key: str,
    prefix: str,
    use_period: bool = False,
    width: int = 4,
) -> str:
    """
    채번 서비스(gapless). 같은 트랜잭션 안에서 SELECT ... FOR UPDATE 로 행을 잠가
    동시 요청에도 번호가 중복되지 않게 한다. 락이 트랜잭션 커밋까지 유지되므로
    롤백 시 번호가 소비되지 않는(결번 없는) 대신 같은 시퀀스는 직렬화된다 —
    PARTNER/ITEM/PO/SO 같은 저빈도 채번 전용. 고빈도 STOCK 이동번호는
    generate_movement_no(락 분리, 결번 허용)를 쓴다.

    예) generate_code(db, "PARTNER", "CUST")            -> CUST-0001
        generate_code(db, "PO", "PO", use_period=True)  -> PO-202506-0001
    """
    period = datetime.now().strftime("%Y%m") if use_period else ""
    row = _acquire_seq_row(db, seq_key, prefix, period)

    row.last_seq += 1
    seq = row.last_seq
    db.flush()

    parts = [p for p in (prefix, period, str(seq).zfill(width)) if p]
    return "-".join(parts)


def generate_movement_no(db: Session, width: int = 4) -> str:
    """STOCK 이동번호 채번(고빈도 경로, 결번 허용). 포맷: STK-YYYYMM-####.

    문제(병목): 모든 입출고가 전역 단일 시퀀스 행을 FOR UPDATE 로 잠근 채 비즈니스
    트랜잭션(잔고 갱신+이동 insert) 커밋까지 락을 쥐면 전체 입출고가 직렬화된다.
    완화(락 분리): 채번만 메인 세션과 별개의 새 세션(autonomous 트랜잭션)에서
    수행하고 즉시 커밋해, 전역 락을 last_seq 증분 순간에만 잡는다. 이후 비즈니스
    트랜잭션이 롤백하면 그 번호는 결번(gap)이 되는데, 내부 이동번호라 허용한다
    (사용자 확정 결정 — gapless 가 필요한 저빈도 채번은 generate_code 유지).

    다이얼렉트 분기(중요): SQLite 는 파일 레벨 단일 쓰기락이라, 메인 트랜잭션이
    이미 잔고 UPDATE 로 쓰기락을 쥔 상태에서 두 번째 커넥션이 쓰기를 시도하면
    'database is locked' 교착/실패가 난다. 그래서 SQLite(로컬 개발·테스트)에서는
    별도 커넥션을 열지 않고 기존 in-transaction 채번(generate_code)을 그대로 쓴다.
    SQLite 는 어차피 쓰기가 단일화돼 있어 락 분리의 실익도 없다.
    """
    bind = db.get_bind()
    if bind.dialect.name == "sqlite":
        return generate_code(db, "STOCK", "STK", use_period=True, width=width)

    period = datetime.now().strftime("%Y%m")
    # 메인 세션과 같은 엔진(커넥션 풀)에서 새 세션을 연다. 요청당 커넥션을 잠깐
    # 2개 쓰지만 즉시 commit·close 로 반납해 풀 점유는 순간적이다.
    # (테스트가 자체 엔진 세션을 넘겨도 동작하도록 SessionLocal 대신 db 의 bind 사용)
    engine = getattr(bind, "engine", bind)  # Connection 이 반환돼도 Engine 으로 환원
    seq_db = Session(bind=engine, autoflush=False)
    try:
        row = _acquire_seq_row(seq_db, "STOCK", "STK", period)
        row.last_seq += 1
        seq = row.last_seq
        seq_db.commit()  # 즉시 커밋 → 시퀀스 행 락을 바로 푼다(락 분리의 핵심)
    except BaseException:
        seq_db.rollback()
        raise
    finally:
        seq_db.close()

    return "-".join(["STK", period, str(seq).zfill(width)])


def apply_stock_delta(
    db: Session, item_id: int, delta: int, *, unit_cost: int | Decimal | None = None,
    allow_negative: bool = False,
) -> tuple[int, Decimal]:
    """재고 잔고를 원자적으로 증감한다. 잔고 행을 SELECT ... FOR UPDATE 로 잠가
    동시 입출고 경합(TOCTOU)을 막고, 결과가 음수면 StockError 를 던진다.

    재고가 늘고(delta>0) 매입단가(unit_cost)가 주어지면 이동평균 원가를 갱신한다.
    (현재고, 이동평균원가) 튜플을 돌려준다. 원가는 Decimal 로 계산·저장한다."""
    def _select_locked():
        return db.execute(
            select(StockBalance).where(StockBalance.item_id == item_id).with_for_update()
        ).scalar_one_or_none()

    row = _select_locked()
    if row is None:
        # 동시 최초 생성(같은 품목 첫 입출고) 경합은 PK 충돌을 세이브포인트로 흡수하고
        # 상대가 만든 잔고 행을 잠가 다시 읽는다.
        savepoint = db.begin_nested()
        try:
            row = StockBalance(item_id=item_id, on_hand=0, avg_cost=Decimal("0"))
            db.add(row)
            db.flush()
            savepoint.commit()
        except IntegrityError:
            savepoint.rollback()
            row = _select_locked()
            if row is None:
                raise

    new_on_hand = row.on_hand + delta
    if new_on_hand < 0 and not allow_negative:
        raise StockError(f"재고가 부족합니다 (현재고: {row.on_hand})")

    # 이동평균 원가: 입고(delta>0)는 가중평균으로 재계산, 입고 취소/삭제(delta<0,
    # unit_cost 지정)는 같은 식으로 해당 입고분을 가중평균에서 제거(역분개)한다.
    if unit_cost is not None and delta != 0:
        cost = Decimal(unit_cost)
        if new_on_hand > 0:
            reversed_avg = (row.on_hand * row.avg_cost + delta * cost) / new_on_hand
            if reversed_avg < 0:
                # 역분개 결과가 음수면(이미 출고돼 섞인 입고를 되돌리는 등) 평균이 오염된다.
                # 조용히 0으로 깎지 않고 오류로 막아 원장 정합을 지킨다.
                raise StockError("이 작업은 이동평균 원가를 음수로 만들어 재고평가를 손상시킵니다")
            row.avg_cost = reversed_avg
        elif new_on_hand == 0 and delta < 0:
            row.avg_cost = Decimal("0")

    row.on_hand = new_on_hand
    db.flush()
    return new_on_hand, row.avg_cost


def _signed_delta(movement_type: str, quantity: int) -> int:
    """이동 유형에 따른 잔고 증감량. OUT은 차감, IN은 가산, ADJUST는 부호 있는 그대로."""
    return -quantity if movement_type == "OUT" else quantity


def post_movement(
    db: Session,
    *,
    item_id: int,
    movement_type: str,
    quantity: int,
    unit_price: int = 0,
    partner_id: int | None = None,
    note: str = "",
    ref_type: str = "MANUAL",
    ref_line_id: int | None = None,
) -> StockMovement:
    """입출고 1건을 잔고·원가 갱신과 함께 기록한다(단일 진입점).
    잔고 증감 → 채번 → 이동 insert 순. 재고 부족 시 StockError.

    원가: IN은 매입단가로 이동평균을 갱신하고 이동원가=매입단가,
    OUT/ADJUST는 평균 불변이며 이동원가=현재 이동평균원가(COGS 계산용)."""
    unit_cost = unit_price if movement_type == "IN" else None
    _, avg_cost = apply_stock_delta(
        db, item_id, _signed_delta(movement_type, quantity), unit_cost=unit_cost
    )
    # IN=매입단가를 이동원가로, OUT/ADJUST=출고 시점 이동평균원가를 이동원가로(COGS용).
    movement_cost = Decimal(unit_price) if movement_type == "IN" else avg_cost

    # 부가세: 매입(IN)·매출(OUT)만, 과세 품목일 때 공급가액(수량×단가)에 부과.
    # 이동 시점 품목 과세구분으로 스냅샷(이후 품목 구분이 바뀌어도 과거 청구액 불변).
    item = db.get(Item, item_id)
    taxable = movement_type in ("IN", "OUT") and item is not None and item.tax_type == "taxable"
    tax_amount = Decimal(compute_tax(unit_price * quantity, taxable))

    # 채번은 재고 검증(apply_stock_delta) '이후'에 한다 — 재고 부족이면 채번 전에
    # StockError 로 실패해 불필요한 결번을 만들지 않는다. (MySQL 은 락 분리 채번)
    movement_no = generate_movement_no(db)
    movement = StockMovement(
        movement_no=movement_no,
        item_id=item_id,
        movement_type=movement_type,
        quantity=quantity,
        unit_price=unit_price,
        cost=movement_cost,
        tax_amount=tax_amount,
        partner_id=partner_id,
        note=note,
        ref_type=ref_type,
        ref_line_id=ref_line_id,
    )
    db.add(movement)
    db.flush()
    return movement


def post_return(
    db: Session,
    *,
    item_id: int,
    kind: str,            # "purchase"(매입반품) / "sales"(매출반품)
    quantity: int,        # 양수(반품 수량)
    unit_price: int,
    partner_id: int | None = None,
    ref_type: str,        # "PRET" / "SRET"
    ref_line_id: int | None = None,
    note: str = "",
) -> StockMovement:
    """반품을 '음수 수량의 역이동'으로 기록한다. 저장 수량이 음수라, 부호 있는 합계로
    집계하는 AP/AR·매출·통계·리포트가 쿼리 변경 없이 자동으로 차감된다.

    - purchase(매입반품): IN 유형 · 재고 감소(공급처로 반출) · AP 차감. 재고 부족 시 StockError.
    - sales(매출반품): OUT 유형 · 재고 증가(고객이 반품) · AR 차감.
    이동평균 원가는 재계산하지 않는다(반품은 새 매입 원가층을 만들지 않는다)."""
    if quantity <= 0:
        raise StockError("반품 수량은 양수여야 합니다")
    if kind == "purchase":
        movement_type, on_hand_delta = "IN", -quantity   # 재고 감소
    else:  # sales
        movement_type, on_hand_delta = "OUT", quantity    # 재고 증가
    # unit_cost=None → 이동평균 불변(수량만 증감). 매입반품이 재고보다 크면 음수재고로 막힌다.
    _, avg_cost = apply_stock_delta(db, item_id, on_hand_delta, unit_cost=None)

    stored_qty = -quantity   # 원거래의 역(음수)
    item = db.get(Item, item_id)
    taxable = item is not None and item.tax_type == "taxable"
    tax_amount = Decimal(compute_tax(unit_price * stored_qty, taxable))   # 음수 세액
    # 매출반품 원가는 현재 이동평균(원 COGS 근사), 매입반품은 매입단가. (음수 수량과 곱해 집계 차감)
    cost = avg_cost if kind == "sales" else Decimal(unit_price)

    # 채번은 재고 검증(apply_stock_delta) 이후 — 실패 시 결번을 만들지 않는다.
    movement = StockMovement(
        movement_no=generate_movement_no(db),
        item_id=item_id, movement_type=movement_type, quantity=stored_qty,
        unit_price=unit_price, cost=cost, tax_amount=tax_amount,
        partner_id=partner_id, note=note, ref_type=ref_type, ref_line_id=ref_line_id,
    )
    db.add(movement)
    db.flush()
    return movement


def record_audit(
    db: Session,
    user,
    action: str,
    entity: str,
    entity_id,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    """변경 이력 기록. commit 은 호출하는 쪽에서 한다."""
    db.add(
        AuditLog(
            user_id=getattr(user, "id", None),
            username=getattr(user, "username", ""),
            action=action,
            entity=entity,
            entity_id=str(entity_id),
            before=json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
            after=json.dumps(after, ensure_ascii=False, default=str) if after is not None else None,
        )
    )
