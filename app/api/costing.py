from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, or_, func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Item, StockBalance, StockMovement, User
from ..schemas import StockValuationOut, ValuationSummary, MarginSummary, Page
from ..deps import require_permission
from ..services import paginate

router = APIRouter(prefix="/api/costing", tags=["costing"])


def _balance_agg_sq(warehouse_id: int | None):
    """품목 단위로 잔고를 합산하는 서브쿼리. 잔고가 창고별 행이라 SUM 으로 접는다.
    (수량 합, 평가액 합 = Σ on_hand x 창고별 avg_cost — 창고별 평균을 존중한 정확한 평가)"""
    sq = select(
        StockBalance.item_id.label("item_id"),
        func.sum(StockBalance.on_hand).label("on_hand"),
        func.sum(StockBalance.on_hand * StockBalance.avg_cost).label("value"),
    ).group_by(StockBalance.item_id)
    if warehouse_id is not None:
        sq = sq.where(StockBalance.warehouse_id == warehouse_id)
    return sq.subquery()


@router.get("/valuation", response_model=Page[StockValuationOut])
def stock_valuation(
    q: str | None = Query(None, description="품목 이름 또는 코드 검색"),
    in_stock_only: bool = Query(True, description="현재고 > 0 품목만"),
    warehouse_id: int | None = Query(None, description="특정 창고만 평가(미지정 시 전 창고 합산)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("stock:read")),
):
    """품목별 재고평가액(현재고 x 이동평균원가). 창고 미지정 시 전 창고 합산이며,
    표시 avg_cost 는 평가액/수량의 가중평균(창고 필터 시 그 창고의 이동평균과 동일)."""
    bal_sq = _balance_agg_sq(warehouse_id)
    stmt = (
        select(Item)
        .join(bal_sq, bal_sq.c.item_id == Item.id)
        .order_by(Item.id.desc())
    )
    if in_stock_only:
        stmt = stmt.where(bal_sq.c.on_hand > 0)
    if q:
        stmt = stmt.where(or_(Item.name.like(f"%{q}%"), Item.code.like(f"%{q}%")))

    result = paginate(db, stmt, page, page_size)
    ids = [it.id for it in result["items"]]
    bal: dict[int, tuple[int, float]] = {}
    if ids:
        rows = db.execute(
            select(bal_sq.c.item_id, bal_sq.c.on_hand, bal_sq.c.value)
            .where(bal_sq.c.item_id.in_(ids))
        ).all()
        bal = {iid: (int(oh), float(v)) for iid, oh, v in rows}
    result["items"] = [_valuation_out(it, *bal.get(it.id, (0, 0.0))) for it in result["items"]]
    return result


def _valuation_out(it: Item, on_hand: int, value: float) -> StockValuationOut:
    avg_cost = (value / on_hand) if on_hand else 0.0
    return StockValuationOut(
        item_id=it.id, item_code=it.code, item_name=it.name, unit=it.unit,
        on_hand=on_hand, avg_cost=round(avg_cost, 2), value=round(value),
    )


@router.get("/valuation/summary", response_model=ValuationSummary)
def valuation_summary(
    warehouse_id: int | None = Query(None, description="특정 창고만 집계(미지정 시 전 창고)"),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("stock:read")),
):
    """전체 재고평가액과 재고 보유 품목 수(창고 필터 가능)."""
    value_stmt = select(func.coalesce(func.sum(StockBalance.on_hand * StockBalance.avg_cost), 0))
    # 창고별 행이라 '보유 품목 수'는 품목 단위 중복 제거(합산 후 >0 이 아닌, 보유 행 기준
    # DISTINCT item — 음수재고가 없으므로 두 정의는 동일하다)
    count_stmt = (
        select(func.count(func.distinct(StockBalance.item_id)))
        .where(StockBalance.on_hand > 0)
    )
    if warehouse_id is not None:
        value_stmt = value_stmt.where(StockBalance.warehouse_id == warehouse_id)
        count_stmt = count_stmt.where(StockBalance.warehouse_id == warehouse_id)
    total_value = db.execute(value_stmt).scalar_one()
    item_count = db.execute(count_stmt).scalar_one()
    return ValuationSummary(total_value=round(total_value or 0), item_count=int(item_count))


@router.get("/margin", response_model=MarginSummary)
def margin_summary(
    date_from: str | None = Query(None, description="YYYY-MM-DD (이상)"),
    date_to: str | None = Query(None, description="YYYY-MM-DD (이하)"),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("stock:read")),
):
    """기간 매출총이익 = 매출액(출고 판매단가) - 매출원가(출고 이동평균원가).

    단가 0 인 출고(파손·증정·감모 등 비매출)는 매출로 보지 않아 집계에서 제외한다.
    재고 감모는 단가 0 OUT 또는 ADJUST 로 기록하면 손익을 왜곡하지 않는다."""
    revenue_expr = func.coalesce(func.sum(StockMovement.quantity * StockMovement.unit_price), 0)
    cogs_expr = func.coalesce(func.sum(StockMovement.quantity * StockMovement.cost), 0)
    stmt = select(revenue_expr, cogs_expr, func.count()).where(
        StockMovement.movement_type == "OUT",
        StockMovement.unit_price > 0,
    )
    if date_from:
        stmt = stmt.where(func.date(StockMovement.created_at) >= date_from)
    if date_to:
        stmt = stmt.where(func.date(StockMovement.created_at) <= date_to)

    revenue, cogs, cnt = db.execute(stmt).one()
    revenue, cogs = round(revenue or 0), round(cogs or 0)
    gross = revenue - cogs
    return MarginSummary(
        revenue=revenue, cogs=cogs, gross_profit=gross,
        margin_rate=round(gross / revenue, 4) if revenue else 0.0,
        sales_count=int(cnt),
    )
