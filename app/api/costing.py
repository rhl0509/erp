from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, or_, func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Item, StockBalance, StockMovement, User
from ..schemas import StockValuationOut, ValuationSummary, MarginSummary, Page
from ..deps import require_permission
from ..services import paginate

router = APIRouter(prefix="/api/costing", tags=["costing"])


@router.get("/valuation", response_model=Page[StockValuationOut])
def stock_valuation(
    q: str | None = Query(None, description="품목 이름 또는 코드 검색"),
    in_stock_only: bool = Query(True, description="현재고 > 0 품목만"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("stock:read")),
):
    """품목별 재고평가액(현재고 x 이동평균원가)."""
    stmt = (
        select(Item)
        .join(StockBalance, StockBalance.item_id == Item.id)
        .order_by(Item.id.desc())
    )
    if in_stock_only:
        stmt = stmt.where(StockBalance.on_hand > 0)
    if q:
        stmt = stmt.where(or_(Item.name.like(f"%{q}%"), Item.code.like(f"%{q}%")))

    result = paginate(db, stmt, page, page_size)
    ids = [it.id for it in result["items"]]
    bal: dict[int, tuple[int, float]] = {}
    if ids:
        rows = db.execute(
            select(StockBalance.item_id, StockBalance.on_hand, StockBalance.avg_cost)
            .where(StockBalance.item_id.in_(ids))
        ).all()
        bal = {iid: (int(oh), float(ac)) for iid, oh, ac in rows}
    result["items"] = [_valuation_out(it, *bal.get(it.id, (0, 0.0))) for it in result["items"]]
    return result


def _valuation_out(it: Item, on_hand: int, avg_cost: float) -> StockValuationOut:
    return StockValuationOut(
        item_id=it.id, item_code=it.code, item_name=it.name, unit=it.unit,
        on_hand=on_hand, avg_cost=round(avg_cost, 2), value=round(on_hand * avg_cost),
    )


@router.get("/valuation/summary", response_model=ValuationSummary)
def valuation_summary(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("stock:read")),
):
    """전체 재고평가액과 재고 보유 품목 수."""
    total_value = db.execute(
        select(func.coalesce(func.sum(StockBalance.on_hand * StockBalance.avg_cost), 0))
    ).scalar_one()
    item_count = db.execute(
        select(func.count()).select_from(StockBalance).where(StockBalance.on_hand > 0)
    ).scalar_one()
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
