from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import PurchaseOrder, PurchaseOrderLine, Partner, Item, Payment, User
from ..schemas import (
    PurchaseOrderCreate, PurchaseOrderUpdate, PurchaseOrderOut, PurchaseOrderLineOut,
    ReceiveRequest, ReturnRequest, Page,
)
from ..deps import require_permission
from ..services import (
    generate_code, record_audit, paginate, post_movement, post_return, compute_tax, StockError,
)

router = APIRouter(prefix="/api/purchase-orders", tags=["purchase"])


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _line_out(l: PurchaseOrderLine) -> PurchaseOrderLineOut:
    supply = l.qty * l.unit_price
    return PurchaseOrderLineOut(
        id=l.id, item_id=l.item_id, item_code=l.item.code, item_name=l.item.name,
        qty=l.qty, unit_price=l.unit_price, amount=supply,
        tax_amount=compute_tax(supply, l.item.tax_type == "taxable"),
        received_qty=l.received_qty, remaining_qty=l.qty - l.received_qty,
        returned_qty=l.returned_qty, returnable_qty=l.received_qty - l.returned_qty,
    )


def _paid_for(db: Session, po_id: int) -> int:
    """이 발주에 귀속된 지급(AP) 합계."""
    return int(db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.ref_type == "PO", Payment.ref_id == po_id, Payment.kind == "AP")
    ).scalar_one())


def _paid_map(db: Session, po_ids: list[int]) -> dict[int, int]:
    """여러 발주의 지급(AP) 합계를 한 번의 쿼리로 조회한다(목록 N+1 방지)."""
    if not po_ids:
        return {}
    rows = db.execute(
        select(Payment.ref_id, func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.ref_type == "PO", Payment.kind == "AP", Payment.ref_id.in_(po_ids))
        .group_by(Payment.ref_id)
    ).all()
    return {int(rid): int(amt) for rid, amt in rows}


def _po_out(db: Session, po: PurchaseOrder, paid: int | None = None) -> PurchaseOrderOut:
    paid = _paid_for(db, po.id) if paid is None else paid
    # 공급가액/세액/합계(주문 기준)와 청구액(입고 실적, VAT 포함)을 계산한다.
    # 미지급은 청구액 기준으로 산출해 거래처 잔액(/partners/{id}/balance)과 정의를 맞춘다.
    supply = tax = billed = 0
    for l in po.lines:
        taxable = l.item.tax_type == "taxable"
        supply += l.qty * l.unit_price
        tax += compute_tax(l.qty * l.unit_price, taxable)
        # 청구액은 순 입고(입고 − 반품) 기준 → 반품이 미지급을 자동 차감한다.
        b_supply = (l.received_qty - l.returned_qty) * l.unit_price
        billed += b_supply + compute_tax(b_supply, taxable)
    return PurchaseOrderOut(
        id=po.id, po_no=po.po_no, partner_id=po.partner_id,
        partner_name=po.partner.name if po.partner else "",
        status=po.status, order_date=po.order_date, note=po.note,
        total_amount=supply, tax_amount=tax, grand_total=supply + tax,
        billed_amount=billed, paid_amount=paid, outstanding=billed - paid,
        created_at=po.created_at, lines=[_line_out(l) for l in po.lines],
    )


def _get_po(db: Session, po_id: int, *, lock: bool = False) -> PurchaseOrder:
    """발주서를 조회한다. lock=True 면 헤더 행을 SELECT ... FOR UPDATE 로 잠가
    동시 입고/확정/취소를 직렬화한다. 엔진이 READ COMMITTED 라서, 락을 얻은 뒤
    읽는 명세(received_qty)는 최신 커밋본이 보장된다(stale read 없음)."""
    stmt = select(PurchaseOrder).where(PurchaseOrder.id == po_id)
    if lock:
        stmt = stmt.with_for_update()
    po = db.execute(stmt).scalar_one_or_none()
    if not po:
        raise HTTPException(status_code=404, detail="발주서를 찾을 수 없습니다")
    return po


def _build_lines(db: Session, po: PurchaseOrder, line_inputs) -> int:
    """명세를 (재)구성하고 총액을 돌려준다. 존재하지 않는 품목은 404."""
    total = 0
    for ln in line_inputs:
        if not db.get(Item, ln.item_id):
            raise HTTPException(status_code=404, detail=f"품목을 찾을 수 없습니다 (item_id={ln.item_id})")
        po.lines.append(PurchaseOrderLine(item_id=ln.item_id, qty=ln.qty, unit_price=ln.unit_price))
        total += ln.qty * ln.unit_price
    return total


@router.get("", response_model=Page[PurchaseOrderOut])
def list_purchase_orders(
    q: str | None = Query(None, description="발주번호 검색"),
    status: str | None = Query(None, description="draft/confirmed/partial/received/cancelled"),
    partner_id: int | None = Query(None, description="공급처로 필터"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("purchase:read")),
):
    stmt = select(PurchaseOrder).order_by(PurchaseOrder.id.desc())
    if q:
        stmt = stmt.where(PurchaseOrder.po_no.like(f"%{q}%"))
    if status:
        stmt = stmt.where(PurchaseOrder.status == status)
    if partner_id:
        stmt = stmt.where(PurchaseOrder.partner_id == partner_id)
    result = paginate(db, stmt, page, page_size)
    paid_map = _paid_map(db, [p.id for p in result["items"]])
    result["items"] = [_po_out(db, p, paid=paid_map.get(p.id, 0)) for p in result["items"]]
    return result


@router.post("", response_model=PurchaseOrderOut, status_code=201)
def create_purchase_order(
    payload: PurchaseOrderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("purchase:write")),
):
    if not db.get(Partner, payload.partner_id):
        raise HTTPException(status_code=404, detail="거래처(공급처)를 찾을 수 없습니다")
    po = PurchaseOrder(
        po_no=generate_code(db, "PO", "PO", use_period=True),
        partner_id=payload.partner_id,
        order_date=payload.order_date or _today(),
        note=payload.note,
        status="draft",
    )
    po.total_amount = _build_lines(db, po, payload.lines)
    db.add(po)
    db.flush()
    record_audit(db, user, "CREATE", "purchase_order", po.id,
                 after={"po_no": po.po_no, "lines": len(po.lines), "total": po.total_amount})
    db.commit()
    db.refresh(po)
    return _po_out(db, po)


@router.get("/{po_id}", response_model=PurchaseOrderOut)
def get_purchase_order(
    po_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("purchase:read")),
):
    return _po_out(db, _get_po(db, po_id))


@router.put("/{po_id}", response_model=PurchaseOrderOut)
def update_purchase_order(
    po_id: int,
    payload: PurchaseOrderUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("purchase:write")),
):
    po = _get_po(db, po_id, lock=True)
    if po.status != "draft":
        raise HTTPException(status_code=400, detail="draft(작성 중) 상태의 발주만 수정할 수 있습니다")

    before = {"partner_id": po.partner_id, "order_date": po.order_date,
              "note": po.note, "lines": len(po.lines), "total": po.total_amount}
    if payload.partner_id is not None:
        if not db.get(Partner, payload.partner_id):
            raise HTTPException(status_code=404, detail="거래처(공급처)를 찾을 수 없습니다")
        po.partner_id = payload.partner_id
    if payload.order_date is not None:
        po.order_date = payload.order_date
    if payload.note is not None:
        po.note = payload.note
    if payload.lines is not None:
        po.lines.clear()          # delete-orphan 으로 기존 명세 제거
        db.flush()
        po.total_amount = _build_lines(db, po, payload.lines)

    record_audit(db, user, "UPDATE", "purchase_order", po.id, before=before,
                 after={"partner_id": po.partner_id, "order_date": po.order_date,
                        "note": po.note, "lines": len(po.lines), "total": po.total_amount})
    db.commit()
    db.refresh(po)
    return _po_out(db, po)


@router.post("/{po_id}/confirm", response_model=PurchaseOrderOut)
def confirm_purchase_order(
    po_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("purchase:write")),
):
    po = _get_po(db, po_id, lock=True)
    if po.status != "draft":
        raise HTTPException(status_code=400, detail="draft 상태의 발주만 확정할 수 있습니다")
    if not po.lines:
        raise HTTPException(status_code=400, detail="명세가 없는 발주는 확정할 수 없습니다")
    before = {"status": po.status}
    po.status = "confirmed"
    record_audit(db, user, "UPDATE", "purchase_order", po.id,
                 before=before, after={"status": "confirmed"})
    db.commit()
    db.refresh(po)
    return _po_out(db, po)


@router.post("/{po_id}/receive", response_model=PurchaseOrderOut)
def receive_purchase_order(
    po_id: int,
    payload: ReceiveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("purchase:write")),
):
    po = _get_po(db, po_id, lock=True)
    if po.status not in ("confirmed", "partial"):
        raise HTTPException(status_code=400, detail="확정(confirmed/partial) 상태의 발주만 입고할 수 있습니다")
    before_status = po.status

    line_map = {l.id: l for l in po.lines}
    # 먼저 전부 검증(부분 성공 방지). 같은 line_id 가 여러 번 와도 합산해서 잔여와
    # 비교한다 — 중복 전송으로 주문수량을 초과 입고하는 것을 막는다.
    requested: dict[int, int] = {}
    for entry in payload.lines:
        l = line_map.get(entry.line_id)
        if not l:
            raise HTTPException(status_code=404, detail=f"명세를 찾을 수 없습니다 (line_id={entry.line_id})")
        requested[l.id] = requested.get(l.id, 0) + entry.qty
    for line_id, req_qty in requested.items():
        l = line_map[line_id]
        remaining = l.qty - l.received_qty
        if req_qty > remaining:
            raise HTTPException(status_code=400, detail=f"입고 수량이 잔여를 초과합니다 (잔여 {remaining})")

    # 입고 반영: IN 이동 생성(잔고 증가) + received_qty 누적
    for entry in payload.lines:
        l = line_map[entry.line_id]
        post_movement(
            db, item_id=l.item_id, movement_type="IN", quantity=entry.qty,
            unit_price=l.unit_price, partner_id=po.partner_id,
            ref_type="PO", ref_line_id=l.id, note=f"발주 {po.po_no} 입고",
        )
        l.received_qty += entry.qty

    po.status = "received" if all(l.received_qty >= l.qty for l in po.lines) else "partial"
    received_now = {str(e.line_id): e.qty for e in payload.lines}
    record_audit(db, user, "UPDATE", "purchase_order", po.id,
                 before={"status": before_status},
                 after={"status": po.status, "received": received_now})
    db.commit()
    db.refresh(po)
    return _po_out(db, po)


@router.post("/{po_id}/return", response_model=PurchaseOrderOut)
def return_purchase_order(
    po_id: int,
    payload: ReturnRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("purchase:write")),
):
    """매입 반품(입고분을 공급처로 반출). 재고 감소·미지급 차감. 반품 수량은 라인별
    순 입고(입고 − 기존 반품) 이내여야 한다."""
    po = _get_po(db, po_id, lock=True)
    if po.status not in ("partial", "received"):
        raise HTTPException(status_code=400, detail="입고 이력이 있는 발주만 반품할 수 있습니다")

    line_map = {l.id: l for l in po.lines}
    requested: dict[int, int] = {}
    for entry in payload.lines:
        l = line_map.get(entry.line_id)
        if not l:
            raise HTTPException(status_code=404, detail=f"명세를 찾을 수 없습니다 (line_id={entry.line_id})")
        requested[l.id] = requested.get(l.id, 0) + entry.qty
    for line_id, req_qty in requested.items():
        l = line_map[line_id]
        returnable = l.received_qty - l.returned_qty
        if req_qty > returnable:
            raise HTTPException(status_code=400, detail=f"반품 수량이 반품 가능 수량을 초과합니다 (가능 {returnable})")

    try:
        for line_id, req_qty in requested.items():
            l = line_map[line_id]
            post_return(
                db, item_id=l.item_id, kind="purchase", quantity=req_qty,
                unit_price=l.unit_price, partner_id=po.partner_id,
                ref_type="PRET", ref_line_id=l.id, note=f"발주 {po.po_no} 매입반품",
            )
            l.returned_qty += req_qty
    except StockError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

    record_audit(db, user, "UPDATE", "purchase_order", po.id,
                 after={"returned": {str(k): v for k, v in requested.items()}})
    db.commit()
    db.refresh(po)
    return _po_out(db, po)


@router.post("/{po_id}/cancel", response_model=PurchaseOrderOut)
def cancel_purchase_order(
    po_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("purchase:write")),
):
    po = _get_po(db, po_id, lock=True)
    if po.status in ("received", "cancelled"):
        raise HTTPException(status_code=400, detail="이미 완료/취소된 발주입니다")
    if any(l.received_qty > 0 for l in po.lines):
        raise HTTPException(status_code=400, detail="입고 이력이 있어 취소할 수 없습니다")
    if _paid_for(db, po.id) > 0:
        raise HTTPException(status_code=400, detail="지급 이력이 있는 발주는 취소할 수 없습니다. 결제를 먼저 삭제하세요")
    before = {"status": po.status}
    po.status = "cancelled"
    record_audit(db, user, "UPDATE", "purchase_order", po.id,
                 before=before, after={"status": "cancelled"})
    db.commit()
    db.refresh(po)
    return _po_out(db, po)


@router.delete("/{po_id}", status_code=204)
def delete_purchase_order(
    po_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("purchase:write")),
):
    po = _get_po(db, po_id, lock=True)
    if po.status != "draft":
        raise HTTPException(status_code=400, detail="draft 상태의 발주만 삭제할 수 있습니다")
    before = {"po_no": po.po_no, "status": po.status}
    db.delete(po)
    record_audit(db, user, "DELETE", "purchase_order", po_id, before=before)
    db.commit()
