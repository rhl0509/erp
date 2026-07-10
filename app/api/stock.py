from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, or_, func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import StockMovement, StockBalance, Item, Partner, User, Warehouse
from ..schemas import (
    StockMovementCreate, StockMovementOut, StockLevelOut, Page,
    StockTransferCreate, StockTransferOut,
)
from ..deps import require_permission
from ..services import (
    record_audit, paginate, post_movement, post_transfer,
    apply_stock_delta, _signed_delta, StockError,
)

router = APIRouter(prefix="/api/stock", tags=["stock"])


def _on_hand_sq(warehouse_id: int | None = None):
    """품목별 현재고(잔고 캐시 합산)를 읽는 상관 스칼라 서브쿼리.
    잔고가 창고별 행이므로 SUM 으로 합산한다(미지정 시 전 창고, 행 없으면 0)."""
    sq = (
        select(func.coalesce(func.sum(StockBalance.on_hand), 0))
        .where(StockBalance.item_id == Item.id)
        .correlate(Item)
    )
    if warehouse_id is not None:
        sq = sq.where(StockBalance.warehouse_id == warehouse_id)
    return sq.scalar_subquery()


def _to_out(m: StockMovement) -> StockMovementOut:
    """파생 필드(품목/거래처/창고 이름)를 포함한 응답 스키마로 변환한다."""
    return StockMovementOut(
        id=m.id,
        movement_no=m.movement_no,
        item_id=m.item_id,
        item_code=m.item.code,
        item_name=m.item.name,
        movement_type=m.movement_type,
        quantity=m.quantity,
        partner_id=m.partner_id,
        partner_name=m.partner.name if m.partner else "",
        warehouse_id=m.warehouse_id,
        warehouse_name=m.warehouse.name if m.warehouse else "",
        unit_price=m.unit_price,
        tax_amount=int(m.tax_amount or 0),
        note=m.note,
        created_at=m.created_at,
    )


@router.get("/movements", response_model=Page[StockMovementOut])
def list_movements(
    q: str | None = Query(None, description="번호 또는 품목 이름/코드 검색"),
    movement_type: str | None = Query(None, description="IN/OUT/ADJUST/TRANSFER 로 필터"),
    item_id: int | None = Query(None, description="특정 품목만 조회"),
    warehouse_id: int | None = Query(None, description="특정 창고만 조회"),
    page: int = Query(1, ge=1, description="페이지 번호(1부터)"),
    page_size: int = Query(20, ge=1, le=100, description="페이지당 건수(최대 100)"),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("stock:read")),
):
    stmt = select(StockMovement).order_by(StockMovement.id.desc())
    if q:
        stmt = stmt.join(Item, StockMovement.item_id == Item.id).where(
            or_(
                StockMovement.movement_no.like(f"%{q}%"),
                Item.name.like(f"%{q}%"),
                Item.code.like(f"%{q}%"),
            )
        )
    if movement_type:
        stmt = stmt.where(StockMovement.movement_type == movement_type)
    if item_id:
        stmt = stmt.where(StockMovement.item_id == item_id)
    if warehouse_id:
        stmt = stmt.where(StockMovement.warehouse_id == warehouse_id)
    result = paginate(db, stmt, page, page_size)
    result["items"] = [_to_out(m) for m in result["items"]]
    return result


@router.post("/movements", response_model=StockMovementOut, status_code=201)
def create_movement(
    payload: StockMovementCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("stock:write")),
):
    item = db.get(Item, payload.item_id)
    if not item:
        raise HTTPException(status_code=404, detail="품목을 찾을 수 없습니다")
    if payload.partner_id is not None and not db.get(Partner, payload.partner_id):
        raise HTTPException(status_code=404, detail="거래처를 찾을 수 없습니다")
    if payload.warehouse_id is not None and not db.get(Warehouse, payload.warehouse_id):
        raise HTTPException(status_code=404, detail="창고를 찾을 수 없습니다")

    # 잔고 갱신 + 채번 + 이동 기록을 단일 진입점(post_movement)에서 처리.
    # 재고 부족은 잔고 행 잠금으로 원자적으로 판정된다(동시 출고 경합 방지).
    # warehouse_id 미지정이면 기본창고로 기록된다(기존 화면·호출 하위호환).
    try:
        movement = post_movement(
            db,
            item_id=payload.item_id,
            movement_type=payload.movement_type,
            quantity=payload.quantity,
            unit_price=payload.unit_price,
            partner_id=payload.partner_id,
            note=payload.note,
            ref_type="MANUAL",
            warehouse_id=payload.warehouse_id,
        )
    except StockError as e:
        raise HTTPException(status_code=400, detail=str(e))

    record_audit(db, user, "CREATE", "stock", movement.id, after=payload.model_dump())
    db.commit()
    db.refresh(movement)
    return _to_out(movement)


@router.post("/transfer", response_model=StockTransferOut, status_code=201)
def create_transfer(
    payload: StockTransferCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("stock:write")),
):
    """창고간 이전. TRANSFER 페어 2행(출고 음수/입고 양수)을 기록하고
    출고창고 이동평균 원가로 입고창고 평균을 갱신한다(원가 보존).
    매입/매출·AP/AR 통계에는 잡히지 않는다."""
    if not db.get(Item, payload.item_id):
        raise HTTPException(status_code=404, detail="품목을 찾을 수 없습니다")
    for wid in (payload.from_warehouse_id, payload.to_warehouse_id):
        if not db.get(Warehouse, wid):
            raise HTTPException(status_code=404, detail=f"창고를 찾을 수 없습니다 (id={wid})")

    try:
        out_mv, in_mv = post_transfer(
            db,
            item_id=payload.item_id,
            from_warehouse_id=payload.from_warehouse_id,
            to_warehouse_id=payload.to_warehouse_id,
            quantity=payload.quantity,
            note=payload.note,
        )
    except StockError as e:
        raise HTTPException(status_code=400, detail=str(e))

    record_audit(db, user, "CREATE", "stock_transfer", out_mv.id, after=payload.model_dump())
    db.commit()
    db.refresh(out_mv)
    db.refresh(in_mv)
    return StockTransferOut(
        item_id=payload.item_id,
        from_warehouse_id=payload.from_warehouse_id,
        to_warehouse_id=payload.to_warehouse_id,
        quantity=payload.quantity,
        cost=float(out_mv.cost),
        out_movement_no=out_mv.movement_no,
        in_movement_no=in_mv.movement_no,
    )


@router.get("/movements/{movement_id}", response_model=StockMovementOut)
def get_movement(
    movement_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("stock:read")),
):
    movement = db.get(StockMovement, movement_id)
    if not movement:
        raise HTTPException(status_code=404, detail="입출고 내역을 찾을 수 없습니다")
    return _to_out(movement)


@router.delete("/movements/{movement_id}", status_code=204)
def delete_movement(
    movement_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("stock:write")),
):
    movement = db.get(StockMovement, movement_id)
    if not movement:
        raise HTTPException(status_code=404, detail="입출고 내역을 찾을 수 없습니다")
    # 문서(발주/수주)에서 생성된 이동은 원장 정합성을 위해 직접 삭제를 막는다.
    if movement.ref_type != "MANUAL":
        raise HTTPException(status_code=400, detail="문서에서 생성된 입출고는 직접 삭제할 수 없습니다")

    # IN 삭제는 이동평균을 역분개하는데, 그 사이 다른 입출고가 이미 이 원가를 섞어
    # 처리했다면 역분개가 평균·COGS를 오염시킨다. 해당 품목에 이 이동보다 나중의
    # 입출고가 있으면 삭제를 막는다(가장 최근 IN 만 안전하게 되돌릴 수 있다).
    if movement.movement_type == "IN":
        has_later = db.execute(
            select(func.count()).select_from(StockMovement).where(
                StockMovement.item_id == movement.item_id,
                StockMovement.id > movement.id,
            )
        ).scalar_one()
        if has_later:
            raise HTTPException(
                status_code=400,
                detail="이후 입출고 이력이 있어 이 입고는 삭제할 수 없습니다(이동평균 원가 정합)",
            )

    before = {
        "movement_no": movement.movement_no,
        "item_id": movement.item_id,
        "movement_type": movement.movement_type,
        "quantity": movement.quantity,
    }
    # 잔고 원복: 원래 증감의 반대를 '해당 창고'에 적용. 삭제로 재고가 음수가 되거나
    # 이동평균이 음수로 오염되면 막는다. IN 삭제 시엔 매입단가를 넘겨 평균에서 제거.
    try:
        apply_stock_delta(
            db, movement.item_id, -_signed_delta(movement.movement_type, movement.quantity),
            warehouse_id=movement.warehouse_id,
            unit_cost=movement.unit_price if movement.movement_type == "IN" else None,
        )
    except StockError as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.delete(movement)
    record_audit(db, user, "DELETE", "stock", movement_id, before=before)
    db.commit()


@router.get("/levels", response_model=Page[StockLevelOut])
def list_stock_levels(
    q: str | None = Query(None, description="품목 이름 또는 코드 검색"),
    low_only: bool = Query(False, description="현재고 0 이하 품목만 조회"),
    warehouse_id: int | None = Query(None, description="특정 창고만 조회(미지정 시 전 창고 합산)"),
    page: int = Query(1, ge=1, description="페이지 번호(1부터)"),
    page_size: int = Query(20, ge=1, le=100, description="페이지당 건수(최대 100)"),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("stock:read")),
):
    stmt = select(Item).order_by(Item.id.desc())
    if q:
        stmt = stmt.where(or_(Item.name.like(f"%{q}%"), Item.code.like(f"%{q}%")))
    if low_only:
        stmt = stmt.where(_on_hand_sq(warehouse_id) <= 0)

    result = paginate(db, stmt, page, page_size)
    result["items"] = [
        _level_out(it, on_hand)
        for it, on_hand in _with_on_hand(db, result["items"], warehouse_id)
    ]
    return result


def _level_out(it: Item, on_hand: int) -> StockLevelOut:
    """품목 + 현재고로 재고현황 응답을 만든다. shortage=안전재고 미달 부족량."""
    return StockLevelOut(
        item_id=it.id, item_code=it.code, item_name=it.name,
        unit=it.unit, item_type=it.item_type, on_hand=on_hand,
        safety_stock=it.safety_stock,
        shortage=max(it.safety_stock - on_hand, 0) if it.safety_stock > 0 else 0,
    )


def _with_on_hand(db: Session, items: list[Item], warehouse_id: int | None = None):
    """품목 목록에 잔고 캐시(현재고)를 매핑한다. 잔고가 창고별 행이므로 품목 단위로
    SUM 합산한다(warehouse_id 지정 시 해당 창고만). 잔고 행이 없으면 0."""
    ids = [it.id for it in items]
    balances: dict[int, int] = {}
    if ids:
        stmt = (
            select(StockBalance.item_id, func.sum(StockBalance.on_hand))
            .where(StockBalance.item_id.in_(ids))
            .group_by(StockBalance.item_id)
        )
        if warehouse_id is not None:
            stmt = stmt.where(StockBalance.warehouse_id == warehouse_id)
        balances = {iid: int(v) for iid, v in db.execute(stmt).all()}
    return [(it, balances.get(it.id, 0)) for it in items]


@router.get("/alerts", response_model=Page[StockLevelOut])
def list_stock_alerts(
    q: str | None = Query(None, description="품목 이름 또는 코드 검색"),
    page: int = Query(1, ge=1, description="페이지 번호(1부터)"),
    page_size: int = Query(20, ge=1, le=100, description="페이지당 건수(최대 100)"),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("stock:read")),
):
    """안전재고 미달 품목을 부족량이 큰 순으로. safety_stock=0(미설정)은 제외한다."""
    on_hand_sq = _on_hand_sq()
    stmt = (
        select(Item)
        .where(Item.safety_stock > 0, on_hand_sq < Item.safety_stock)
        .order_by((Item.safety_stock - on_hand_sq).desc(), Item.id.desc())
    )
    if q:
        stmt = stmt.where(or_(Item.name.like(f"%{q}%"), Item.code.like(f"%{q}%")))

    result = paginate(db, stmt, page, page_size)
    result["items"] = [_level_out(it, on_hand) for it, on_hand in _with_on_hand(db, result["items"])]
    return result


@router.get("/alerts/count")
def stock_alerts_count(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("stock:read")),
):
    """안전재고 미달 품목 수(네비 뱃지용)."""
    on_hand_sq = _on_hand_sq()
    count = db.execute(
        select(func.count())
        .select_from(Item)
        .where(Item.safety_stock > 0, on_hand_sq < Item.safety_stock)
    ).scalar_one()
    return {"count": int(count)}
