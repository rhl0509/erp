from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import TaxInvoice, TaxInvoiceLine, PurchaseOrder, SalesOrder, User
from ..schemas import TaxInvoiceCreate, TaxInvoiceOut, Page
from ..deps import require_permission
from ..services import generate_code, record_audit, paginate, compute_tax

router = APIRouter(prefix="/api/tax-invoices", tags=["tax-invoices"])


def _get(db: Session, inv_id: int) -> TaxInvoice:
    inv = db.get(TaxInvoice, inv_id)
    if not inv:
        raise HTTPException(status_code=404, detail="세금계산서를 찾을 수 없습니다")
    return inv


@router.get("", response_model=Page[TaxInvoiceOut])
def list_invoices(
    kind: str | None = Query(None, description="sales(매출)/purchase(매입)"),
    partner_id: int | None = Query(None, description="거래처로 필터"),
    ref_type: str | None = Query(None, description="PO/SO 원천 문서"),
    ref_id: int | None = Query(None, description="원천 문서 id"),
    status: str | None = Query(None, description="issued/cancelled"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("invoice:read")),
):
    stmt = select(TaxInvoice).order_by(TaxInvoice.id.desc())
    if kind:
        stmt = stmt.where(TaxInvoice.kind == kind)
    if partner_id:
        stmt = stmt.where(TaxInvoice.partner_id == partner_id)
    if ref_type:
        stmt = stmt.where(TaxInvoice.ref_type == ref_type)
    if ref_id:
        stmt = stmt.where(TaxInvoice.ref_id == ref_id)
    if status:
        stmt = stmt.where(TaxInvoice.status == status)
    return paginate(db, stmt, page, page_size)


@router.post("", response_model=TaxInvoiceOut, status_code=201)
def issue_invoice(
    payload: TaxInvoiceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("invoice:write")),
):
    """발주(PO)/수주(SO)에서 세금계산서를 발행한다. 발행 시점의 거래처·품목·금액을
    스냅샷하며, 문서 하나당 유효한(취소되지 않은) 계산서는 1건만 허용한다."""
    if payload.ref_type == "PO":
        doc = db.get(PurchaseOrder, payload.ref_id)
        kind, doc_label = "purchase", "발주서"
    else:  # SO
        doc = db.get(SalesOrder, payload.ref_id)
        kind, doc_label = "sales", "수주서"
    if not doc:
        raise HTTPException(status_code=404, detail=f"{doc_label}를 찾을 수 없습니다")
    if doc.status in ("draft", "cancelled"):
        raise HTTPException(status_code=400, detail="작성 중(draft)이거나 취소된 문서로는 발행할 수 없습니다")

    # 중복 발행 차단: 같은 문서에 유효한 계산서가 이미 있으면 거부.
    dup = db.execute(
        select(TaxInvoice.id).where(
            TaxInvoice.ref_type == payload.ref_type,
            TaxInvoice.ref_id == payload.ref_id,
            TaxInvoice.status == "issued",
        ).limit(1)
    ).first()
    if dup:
        raise HTTPException(status_code=409, detail="이미 발행된 세금계산서가 있습니다")

    partner = doc.partner
    inv = TaxInvoice(
        invoice_no=generate_code(db, "TAXINV", "TXI", use_period=True),
        kind=kind, ref_type=payload.ref_type, ref_id=doc.id,
        partner_id=partner.id, partner_name=partner.name,
        partner_business_no=partner.business_no,
        issue_date=payload.issue_date or datetime.now().strftime("%Y-%m-%d"),
        status="issued", note=payload.note,
    )
    supply_total = tax_total = 0
    for l in doc.lines:
        supply = l.qty * l.unit_price
        tax = compute_tax(supply, l.item.tax_type == "taxable")
        inv.lines.append(TaxInvoiceLine(
            item_name=l.item.name, spec=l.item.spec, qty=l.qty,
            unit_price=l.unit_price, supply_amount=supply, tax_amount=tax,
        ))
        supply_total += supply
        tax_total += tax
    inv.supply_amount = supply_total
    inv.tax_amount = tax_total
    inv.total_amount = supply_total + tax_total

    db.add(inv)
    db.flush()
    record_audit(db, user, "CREATE", "tax_invoice", inv.id,
                 after={"invoice_no": inv.invoice_no, "kind": kind, "total": inv.total_amount})
    db.commit()
    db.refresh(inv)
    return inv


@router.get("/{inv_id}", response_model=TaxInvoiceOut)
def get_invoice(
    inv_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("invoice:read")),
):
    return _get(db, inv_id)


@router.post("/{inv_id}/cancel", response_model=TaxInvoiceOut)
def cancel_invoice(
    inv_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("invoice:write")),
):
    inv = _get(db, inv_id)
    if inv.status == "cancelled":
        raise HTTPException(status_code=400, detail="이미 취소된 세금계산서입니다")
    before = {"status": inv.status}
    inv.status = "cancelled"
    record_audit(db, user, "UPDATE", "tax_invoice", inv.id,
                 before=before, after={"status": "cancelled"})
    db.commit()
    db.refresh(inv)
    return inv
