from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, or_, func, case
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Partner, StockMovement, Payment, PurchaseOrder, SalesOrder, User
from ..schemas import (
    PartnerCreate, PartnerUpdate, PartnerOut, PartnerTxnOut, PartnerLedgerSummary,
    PartnerBalanceOut, Page,
)
from ..deps import require_permission
from ..services import generate_code, record_audit, paginate

router = APIRouter(prefix="/api/partners", tags=["partners"])


@router.get("", response_model=Page[PartnerOut])
def list_partners(
    q: str | None = Query(None, description="이름 또는 코드 검색"),
    partner_type: str | None = Query(None, description="customer/supplier/both 로 필터"),
    page: int = Query(1, ge=1, description="페이지 번호(1부터)"),
    page_size: int = Query(20, ge=1, le=100, description="페이지당 건수(최대 100)"),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("partner:read")),
):
    stmt = select(Partner).order_by(Partner.id.desc())
    if q:
        stmt = stmt.where(or_(Partner.name.like(f"%{q}%"), Partner.code.like(f"%{q}%")))
    if partner_type:
        stmt = stmt.where(Partner.partner_type == partner_type)
    return paginate(db, stmt, page, page_size)


@router.post("", response_model=PartnerOut, status_code=201)
def create_partner(
    payload: PartnerCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("partner:write")),
):
    prefix = "VEND" if payload.partner_type == "supplier" else "CUST"
    code = generate_code(db, "PARTNER", prefix)
    partner = Partner(code=code, **payload.model_dump())
    db.add(partner)
    db.flush()
    record_audit(db, user, "CREATE", "partner", partner.id, after=payload.model_dump())
    db.commit()
    db.refresh(partner)
    return partner


@router.get("/{partner_id}", response_model=PartnerOut)
def get_partner(
    partner_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("partner:read")),
):
    partner = db.get(Partner, partner_id)
    if not partner:
        raise HTTPException(status_code=404, detail="거래처를 찾을 수 없습니다")
    return partner


@router.put("/{partner_id}", response_model=PartnerOut)
def update_partner(
    partner_id: int,
    payload: PartnerUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("partner:write")),
):
    partner = db.get(Partner, partner_id)
    if not partner:
        raise HTTPException(status_code=404, detail="거래처를 찾을 수 없습니다")

    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="수정할 항목이 없습니다")
    before = {k: getattr(partner, k) for k in data.keys()}
    for k, v in data.items():
        setattr(partner, k, v)
    record_audit(db, user, "UPDATE", "partner", partner.id, before=before, after=data)
    db.commit()
    db.refresh(partner)
    return partner


@router.delete("/{partner_id}", status_code=204)
def delete_partner(
    partner_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("partner:write")),
):
    partner = db.get(Partner, partner_id)
    if not partner:
        raise HTTPException(status_code=404, detail="거래처를 찾을 수 없습니다")
    # 참조 데이터가 있으면 FK 오류(500) 대신 409로 안내한다.
    referenced = (
        db.execute(select(StockMovement.id)
                   .where(StockMovement.partner_id == partner_id).limit(1)).first()
        or db.execute(select(PurchaseOrder.id)
                      .where(PurchaseOrder.partner_id == partner_id).limit(1)).first()
        or db.execute(select(SalesOrder.id)
                      .where(SalesOrder.partner_id == partner_id).limit(1)).first()
        or db.execute(select(Payment.id)
                      .where(Payment.partner_id == partner_id).limit(1)).first()
    )
    if referenced:
        raise HTTPException(
            status_code=409,
            detail="입출고·발주·수주·결제 이력이 있는 거래처는 삭제할 수 없습니다. 대신 비활성화하세요.",
        )
    before = {"code": partner.code, "name": partner.name}
    db.delete(partner)
    record_audit(db, user, "DELETE", "partner", partner_id, before=before)
    db.commit()


# ---------- 거래처별 매입/매출 내역 (입출고 이력에서 파생) ----------
def _txn_to_out(m: StockMovement) -> PartnerTxnOut:
    """입출고 이력을 매입/매출 내역 응답 스키마로 변환한다. (IN=매입, OUT=매출)"""
    return PartnerTxnOut(
        id=m.id,
        movement_no=m.movement_no,
        created_at=m.created_at,
        item_id=m.item_id,
        item_code=m.item.code,
        item_name=m.item.name,
        kind="purchase" if m.movement_type == "IN" else "sales",
        quantity=m.quantity,
        unit_price=m.unit_price,
        amount=m.quantity * m.unit_price,
    )


@router.get("/{partner_id}/transactions", response_model=Page[PartnerTxnOut])
def list_partner_transactions(
    partner_id: int,
    kind: str | None = Query(None, description="purchase/sales 로 필터"),
    page: int = Query(1, ge=1, description="페이지 번호(1부터)"),
    page_size: int = Query(20, ge=1, le=100, description="페이지당 건수(최대 100)"),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("partner:read")),
):
    if not db.get(Partner, partner_id):
        raise HTTPException(status_code=404, detail="거래처를 찾을 수 없습니다")

    # ADJUST(재고 조정)는 매입/매출이 아니므로 제외
    stmt = (
        select(StockMovement)
        .where(
            StockMovement.partner_id == partner_id,
            StockMovement.movement_type.in_(("IN", "OUT")),
        )
        .order_by(StockMovement.id.desc())
    )
    if kind == "purchase":
        stmt = stmt.where(StockMovement.movement_type == "IN")
    elif kind == "sales":
        stmt = stmt.where(StockMovement.movement_type == "OUT")

    result = paginate(db, stmt, page, page_size)
    result["items"] = [_txn_to_out(m) for m in result["items"]]
    return result


@router.get("/{partner_id}/summary", response_model=PartnerLedgerSummary)
def get_partner_ledger_summary(
    partner_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("partner:read")),
):
    if not db.get(Partner, partner_id):
        raise HTTPException(status_code=404, detail="거래처를 찾을 수 없습니다")

    amount = StockMovement.quantity * StockMovement.unit_price
    row = db.execute(
        select(
            func.sum(case((StockMovement.movement_type == "IN", 1), else_=0)),
            func.sum(case((StockMovement.movement_type == "IN", amount), else_=0)),
            func.sum(case((StockMovement.movement_type == "OUT", 1), else_=0)),
            func.sum(case((StockMovement.movement_type == "OUT", amount), else_=0)),
        ).where(StockMovement.partner_id == partner_id)
    ).one()
    return PartnerLedgerSummary(
        purchase_count=int(row[0] or 0),
        purchase_amount=int(row[1] or 0),
        sales_count=int(row[2] or 0),
        sales_amount=int(row[3] or 0),
    )


@router.get("/{partner_id}/balance", response_model=PartnerBalanceOut)
def get_partner_balance(
    partner_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("payment:read")),
):
    """거래처 미지급(AP)·미수(AR) 잔액.
    청구액은 입출고 이력(IN=매입/OUT=매출)에서 파생, 결제는 payments 에서 합산한다."""
    partner = db.get(Partner, partner_id)
    if not partner:
        raise HTTPException(status_code=404, detail="거래처를 찾을 수 없습니다")

    amount = StockMovement.quantity * StockMovement.unit_price   # 공급가액
    tax = StockMovement.tax_amount                               # 부가세액(스냅샷)
    p_supply, s_supply, p_tax, s_tax = db.execute(
        select(
            func.coalesce(func.sum(case((StockMovement.movement_type == "IN", amount), else_=0)), 0),
            func.coalesce(func.sum(case((StockMovement.movement_type == "OUT", amount), else_=0)), 0),
            func.coalesce(func.sum(case((StockMovement.movement_type == "IN", tax), else_=0)), 0),
            func.coalesce(func.sum(case((StockMovement.movement_type == "OUT", tax), else_=0)), 0),
        ).where(StockMovement.partner_id == partner_id)
    ).one()

    ap_paid, ar_collected = db.execute(
        select(
            func.coalesce(func.sum(case((Payment.kind == "AP", Payment.amount), else_=0)), 0),
            func.coalesce(func.sum(case((Payment.kind == "AR", Payment.amount), else_=0)), 0),
        ).where(Payment.partner_id == partner_id)
    ).one()

    # 청구액(VAT 포함) = 공급가액 + 부가세액. 미지급/미수 = 청구액 − 결제.
    p_tax, s_tax = int(p_tax), int(s_tax)
    pa = int(p_supply) + p_tax
    sa_amt = int(s_supply) + s_tax
    apd, arc = int(ap_paid), int(ar_collected)
    ar_out = sa_amt - arc
    credit_avail = (partner.credit_limit - ar_out) if partner.credit_limit > 0 else 0
    return PartnerBalanceOut(
        partner_id=partner_id, partner_name=partner.name,
        purchase_amount=pa, purchase_tax=p_tax, ap_paid=apd, ap_outstanding=pa - apd,
        sales_amount=sa_amt, sales_tax=s_tax, ar_collected=arc, ar_outstanding=ar_out,
        credit_limit=partner.credit_limit, credit_available=credit_avail,
    )
