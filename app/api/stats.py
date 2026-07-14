from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select, func, case
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import StockMovement, Partner, Item, User
from ..schemas import (
    SalesPurchaseOverview, StatTotal, StatMoneyPair, StatMonthly, StatTopPartner,
    TransactionPartnerSubtotal, TransactionItemSubtotal,
)
from ..deps import require_permission
from ..timeutil import business_date_of, business_tz, now_business, to_db_naive

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/overview", response_model=SalesPurchaseOverview)
def get_overview(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("stock:read")),
):
    """매입/매출 통계 요약. IN=매입, OUT=매출로 집계하며 ADJUST(재고 조정)는 제외한다."""
    amount = StockMovement.quantity * StockMovement.unit_price

    # ---- 전체 누계 ----
    row = db.execute(
        select(
            func.sum(case((StockMovement.movement_type == "IN", amount), else_=0)),
            func.sum(case((StockMovement.movement_type == "OUT", amount), else_=0)),
            func.sum(case((StockMovement.movement_type == "IN", 1), else_=0)),
            func.sum(case((StockMovement.movement_type == "OUT", 1), else_=0)),
        )
    ).one()
    total = StatTotal(
        purchase_amount=int(row[0] or 0),
        sales_amount=int(row[1] or 0),
        purchase_count=int(row[2] or 0),
        sales_count=int(row[3] or 0),
    )

    # ---- 이번 달 (월 경계는 DB 함수 대신 파이썬에서 계산해 SQLite/MySQL 모두 호환) ----
    # 경계는 **사업장 시간대**의 1일 0시를 DB 시각으로 환산해 잡는다 — created_at 은 DB
    # 시각이므로, 앱 로컬 시각으로 만든 경계와 비교하면 시간대가 다를 때 하루가 어긋난다.
    now = now_business()
    month_start = to_db_naive(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    row = db.execute(
        select(
            func.sum(case((StockMovement.movement_type == "IN", amount), else_=0)),
            func.sum(case((StockMovement.movement_type == "OUT", amount), else_=0)),
        ).where(StockMovement.created_at >= month_start)
    ).one()
    this_month = StatMoneyPair(
        purchase_amount=int(row[0] or 0),
        sales_amount=int(row[1] or 0),
    )

    # ---- 최근 6개월 (현재 달 포함, 과거 -> 현재 순) ----
    # 월 키를 파이썬에서 만들고, 행을 가져와 파이썬에서 월별로 집계한다.
    month_keys: list[tuple[int, int]] = []
    y, m = now.year, now.month
    for _i in range(6):
        month_keys.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    month_keys.reverse()

    range_start = to_db_naive(
        datetime(month_keys[0][0], month_keys[0][1], 1, tzinfo=business_tz())
    )
    rows = db.execute(
        select(
            StockMovement.created_at,
            StockMovement.movement_type,
            StockMovement.quantity,
            StockMovement.unit_price,
        ).where(
            StockMovement.created_at >= range_start,
            StockMovement.movement_type.in_(("IN", "OUT")),
        )
    ).all()
    buckets: dict[str, dict[str, int]] = {}
    for created_at, movement_type, quantity, unit_price in rows:
        # 이동이 '어느 달의 실적인가'도 사업장 기준으로 판단한다(전표일자와 같은 정의).
        key = business_date_of(created_at)[:7]
        b = buckets.setdefault(key, {"purchase": 0, "sales": 0})
        b["purchase" if movement_type == "IN" else "sales"] += quantity * unit_price
    monthly = []
    for ky, km in month_keys:
        key = f"{ky}-{km:02d}"
        b = buckets.get(key, {"purchase": 0, "sales": 0})
        monthly.append(StatMonthly(
            period=key,
            purchase_amount=b["purchase"],
            sales_amount=b["sales"],
        ))

    # ---- 매출 상위 거래처 (OUT 금액 기준 상위 5곳) ----
    sales_sum = func.sum(amount)
    top_rows = db.execute(
        select(Partner.id, Partner.name, sales_sum)
        .join(StockMovement, StockMovement.partner_id == Partner.id)
        .where(StockMovement.movement_type == "OUT")
        .group_by(Partner.id, Partner.name)
        .order_by(sales_sum.desc())
        .limit(5)
    ).all()
    top_partners = [
        StatTopPartner(partner_id=pid, partner_name=name, sales_amount=int(s or 0))
        for pid, name, s in top_rows
    ]

    # ---- 거래처별 소계 (전체 누계, 거래액 큰 순) ----
    sub_rows = db.execute(
        select(
            Partner.id,
            Partner.name,
            func.sum(case((StockMovement.movement_type == "IN", amount), else_=0)),
            func.sum(case((StockMovement.movement_type == "OUT", amount), else_=0)),
            func.sum(case((StockMovement.movement_type == "IN", 1), else_=0)),
            func.sum(case((StockMovement.movement_type == "OUT", 1), else_=0)),
        )
        .join(StockMovement, StockMovement.partner_id == Partner.id)
        .where(StockMovement.movement_type.in_(("IN", "OUT")))
        .group_by(Partner.id, Partner.name)
    ).all()
    partner_subtotals = [
        TransactionPartnerSubtotal(
            partner_id=pid,
            partner_name=name,
            purchase_amount=int(pa or 0),
            sales_amount=int(sa or 0),
            purchase_count=int(pc or 0),
            sales_count=int(sc or 0),
        )
        for pid, name, pa, sa, pc, sc in sub_rows
    ]
    # 거래처 미연동(파트너 없는) 매입/매출은 "미지정"으로 합산해 포함한다
    nrow = db.execute(
        select(
            func.sum(case((StockMovement.movement_type == "IN", amount), else_=0)),
            func.sum(case((StockMovement.movement_type == "OUT", amount), else_=0)),
            func.sum(case((StockMovement.movement_type == "IN", 1), else_=0)),
            func.sum(case((StockMovement.movement_type == "OUT", 1), else_=0)),
        ).where(
            StockMovement.partner_id.is_(None),
            StockMovement.movement_type.in_(("IN", "OUT")),
        )
    ).one()
    if nrow[2] or nrow[3]:  # 매입 또는 매출 건수가 있을 때만 표시
        partner_subtotals.append(TransactionPartnerSubtotal(
            partner_id=None,
            partner_name="미지정",
            purchase_amount=int(nrow[0] or 0),
            sales_amount=int(nrow[1] or 0),
            purchase_count=int(nrow[2] or 0),
            sales_count=int(nrow[3] or 0),
        ))
    partner_subtotals.sort(key=lambda r: r.purchase_amount + r.sales_amount, reverse=True)

    # ---- 품목별 소계 (전체 누계, 거래액 큰 순) ----
    item_rows = db.execute(
        select(
            Item.id,
            Item.code,
            Item.name,
            func.sum(case((StockMovement.movement_type == "IN", amount), else_=0)),
            func.sum(case((StockMovement.movement_type == "OUT", amount), else_=0)),
            func.sum(case((StockMovement.movement_type == "IN", 1), else_=0)),
            func.sum(case((StockMovement.movement_type == "OUT", 1), else_=0)),
        )
        .join(StockMovement, StockMovement.item_id == Item.id)
        .where(StockMovement.movement_type.in_(("IN", "OUT")))
        .group_by(Item.id, Item.code, Item.name)
    ).all()
    item_subtotals = [
        TransactionItemSubtotal(
            item_id=iid,
            item_code=code,
            item_name=name,
            purchase_amount=int(pa or 0),
            sales_amount=int(sa or 0),
            purchase_count=int(pc or 0),
            sales_count=int(sc or 0),
        )
        for iid, code, name, pa, sa, pc, sc in item_rows
    ]
    item_subtotals.sort(key=lambda r: r.purchase_amount + r.sales_amount, reverse=True)

    return SalesPurchaseOverview(
        total=total,
        this_month=this_month,
        monthly=monthly,
        top_partners=top_partners,
        partner_subtotals=partner_subtotals,
        item_subtotals=item_subtotals,
    )
