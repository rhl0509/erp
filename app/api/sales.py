from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import SalesOrder, SalesOrderLine, Partner, Item, Payment, StockMovement, User
from ..schemas import (
    SalesOrderCreate, SalesOrderUpdate, SalesOrderOut, SalesOrderLineOut,
    ShipRequest, ReturnRequest, Page,
)
from ..deps import require_permission
from ..timeutil import today_str
from ..services import (
    generate_code, record_audit, paginate, post_movement, post_return, compute_tax, StockError,
)

router = APIRouter(prefix="/api/sales-orders", tags=["sales"])


def _today() -> str:
    """주문일 기본값 — 사업장 시간대 기준(app/timeutil)."""
    return today_str()


def _line_out(l: SalesOrderLine) -> SalesOrderLineOut:
    supply = l.qty * l.unit_price
    return SalesOrderLineOut(
        id=l.id, item_id=l.item_id, item_code=l.item.code, item_name=l.item.name,
        qty=l.qty, unit_price=l.unit_price, amount=supply,
        tax_amount=compute_tax(supply, l.item.tax_type == "taxable"),
        shipped_qty=l.shipped_qty, remaining_qty=l.qty - l.shipped_qty,
        returned_qty=l.returned_qty, returnable_qty=l.shipped_qty - l.returned_qty,
    )


def _collected_for(db: Session, so_id: int) -> int:
    """이 수주에 귀속된 수금(AR) 합계."""
    return int(db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.ref_type == "SO", Payment.ref_id == so_id, Payment.kind == "AR")
    ).scalar_one())


def _collected_map(db: Session, so_ids: list[int]) -> dict[int, int]:
    """여러 수주의 수금(AR) 합계를 한 번의 쿼리로 조회한다(목록 N+1 방지)."""
    if not so_ids:
        return {}
    rows = db.execute(
        select(Payment.ref_id, func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.ref_type == "SO", Payment.kind == "AR", Payment.ref_id.in_(so_ids))
        .group_by(Payment.ref_id)
    ).all()
    return {int(rid): int(amt) for rid, amt in rows}


def _so_out(db: Session, so: SalesOrder, paid: int | None = None) -> SalesOrderOut:
    paid = _collected_for(db, so.id) if paid is None else paid
    # 공급가액/세액/합계(주문 기준)와 청구액(출고 실적, VAT 포함)을 계산한다.
    supply = tax = billed = 0
    for l in so.lines:
        taxable = l.item.tax_type == "taxable"
        supply += l.qty * l.unit_price
        tax += compute_tax(l.qty * l.unit_price, taxable)
        # 청구액은 순 출고(출고 − 반품) 기준 → 반품이 미수를 자동 차감한다.
        b_supply = (l.shipped_qty - l.returned_qty) * l.unit_price
        billed += b_supply + compute_tax(b_supply, taxable)
    return SalesOrderOut(
        id=so.id, so_no=so.so_no, partner_id=so.partner_id,
        partner_name=so.partner.name if so.partner else "",
        status=so.status, order_date=so.order_date, note=so.note,
        total_amount=supply, tax_amount=tax, grand_total=supply + tax,
        billed_amount=billed, paid_amount=paid, outstanding=billed - paid,
        created_at=so.created_at, lines=[_line_out(l) for l in so.lines],
    )


def _get_so(db: Session, so_id: int, *, lock: bool = False) -> SalesOrder:
    """수주서를 조회한다. lock=True 면 헤더 행을 SELECT ... FOR UPDATE 로 잠가
    동시 출고/확정/취소를 직렬화한다. 엔진이 READ COMMITTED 라서, 락을 얻은 뒤
    읽는 명세(shipped_qty)는 최신 커밋본이 보장된다(stale read 없음)."""
    stmt = select(SalesOrder).where(SalesOrder.id == so_id)
    if lock:
        stmt = stmt.with_for_update()
    so = db.execute(stmt).scalar_one_or_none()
    if not so:
        raise HTTPException(status_code=404, detail="수주서를 찾을 수 없습니다")
    return so


def _build_lines(db: Session, so: SalesOrder, line_inputs) -> int:
    total = 0
    for ln in line_inputs:
        if not db.get(Item, ln.item_id):
            raise HTTPException(status_code=404, detail=f"품목을 찾을 수 없습니다 (item_id={ln.item_id})")
        so.lines.append(SalesOrderLine(item_id=ln.item_id, qty=ln.qty, unit_price=ln.unit_price))
        total += ln.qty * ln.unit_price
    return total


@router.get("", response_model=Page[SalesOrderOut])
def list_sales_orders(
    q: str | None = Query(None, description="수주번호 검색"),
    status: str | None = Query(None, description="draft/confirmed/partial/shipped/cancelled"),
    partner_id: int | None = Query(None, description="고객으로 필터"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("sales:read")),
):
    stmt = select(SalesOrder).order_by(SalesOrder.id.desc())
    if q:
        stmt = stmt.where(SalesOrder.so_no.like(f"%{q}%"))
    if status:
        stmt = stmt.where(SalesOrder.status == status)
    if partner_id:
        stmt = stmt.where(SalesOrder.partner_id == partner_id)
    result = paginate(db, stmt, page, page_size)
    paid_map = _collected_map(db, [s.id for s in result["items"]])
    result["items"] = [_so_out(db, s, paid=paid_map.get(s.id, 0)) for s in result["items"]]
    return result


@router.post("", response_model=SalesOrderOut, status_code=201)
def create_sales_order(
    payload: SalesOrderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("sales:write")),
):
    if not db.get(Partner, payload.partner_id):
        raise HTTPException(status_code=404, detail="거래처(고객)를 찾을 수 없습니다")
    so = SalesOrder(
        so_no=generate_code(db, "SO", "SO", use_period=True),
        partner_id=payload.partner_id,
        order_date=payload.order_date or _today(),
        note=payload.note,
        status="draft",
    )
    so.total_amount = _build_lines(db, so, payload.lines)
    db.add(so)
    db.flush()
    record_audit(db, user, "CREATE", "sales_order", so.id,
                 after={"so_no": so.so_no, "lines": len(so.lines), "total": so.total_amount})
    db.commit()
    db.refresh(so)
    return _so_out(db, so)


@router.get("/{so_id}", response_model=SalesOrderOut)
def get_sales_order(
    so_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("sales:read")),
):
    return _so_out(db, _get_so(db, so_id))


@router.put("/{so_id}", response_model=SalesOrderOut)
def update_sales_order(
    so_id: int,
    payload: SalesOrderUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("sales:write")),
):
    so = _get_so(db, so_id, lock=True)
    if so.status != "draft":
        raise HTTPException(status_code=400, detail="draft(작성 중) 상태의 수주만 수정할 수 있습니다")

    before = {"partner_id": so.partner_id, "order_date": so.order_date,
              "note": so.note, "lines": len(so.lines), "total": so.total_amount}
    if payload.partner_id is not None:
        if not db.get(Partner, payload.partner_id):
            raise HTTPException(status_code=404, detail="거래처(고객)를 찾을 수 없습니다")
        so.partner_id = payload.partner_id
    if payload.order_date is not None:
        so.order_date = payload.order_date
    if payload.note is not None:
        so.note = payload.note
    if payload.lines is not None:
        so.lines.clear()
        db.flush()
        so.total_amount = _build_lines(db, so, payload.lines)

    record_audit(db, user, "UPDATE", "sales_order", so.id, before=before,
                 after={"partner_id": so.partner_id, "order_date": so.order_date,
                        "note": so.note, "lines": len(so.lines), "total": so.total_amount})
    db.commit()
    db.refresh(so)
    return _so_out(db, so)


@router.post("/{so_id}/confirm", response_model=SalesOrderOut)
def confirm_sales_order(
    so_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("sales:write")),
):
    so = _get_so(db, so_id, lock=True)
    if so.status != "draft":
        raise HTTPException(status_code=400, detail="draft 상태의 수주만 확정할 수 있습니다")
    if not so.lines:
        raise HTTPException(status_code=400, detail="명세가 없는 수주는 확정할 수 없습니다")
    before = {"status": so.status}
    so.status = "confirmed"
    record_audit(db, user, "UPDATE", "sales_order", so.id,
                 before=before, after={"status": "confirmed"})
    db.commit()
    db.refresh(so)
    return _so_out(db, so)


@router.post("/{so_id}/ship", response_model=SalesOrderOut)
def ship_sales_order(
    so_id: int,
    payload: ShipRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("sales:write")),
):
    so = _get_so(db, so_id, lock=True)
    if so.status not in ("confirmed", "partial"):
        raise HTTPException(status_code=400, detail="확정(confirmed/partial) 상태의 수주만 출고할 수 있습니다")
    before_status = so.status

    line_map = {l.id: l for l in so.lines}
    # 같은 line_id 가 여러 번 와도 합산해서 잔여와 비교한다 — 중복 전송으로
    # 주문수량을 초과 출고(없는 재고 판매·매출 이중계상)하는 것을 막는다.
    requested: dict[int, int] = {}
    for entry in payload.lines:
        l = line_map.get(entry.line_id)
        if not l:
            raise HTTPException(status_code=404, detail=f"명세를 찾을 수 없습니다 (line_id={entry.line_id})")
        requested[l.id] = requested.get(l.id, 0) + entry.qty
    for line_id, req_qty in requested.items():
        l = line_map[line_id]
        remaining = l.qty - l.shipped_qty
        if req_qty > remaining:
            raise HTTPException(status_code=400, detail=f"출고 수량이 잔여를 초과합니다 (잔여 {remaining})")

    # 여신한도 점검: 한도가 있으면 (현재 미수 + 이번 출고 청구)가 한도를 넘을 수 없다.
    if so.partner and so.partner.credit_limit > 0:
        billed = int(db.execute(
            select(func.coalesce(func.sum(
                StockMovement.quantity * StockMovement.unit_price + StockMovement.tax_amount), 0))
            .where(StockMovement.movement_type == "OUT", StockMovement.partner_id == so.partner_id)
        ).scalar_one())
        collected = int(db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.kind == "AR", Payment.partner_id == so.partner_id)
        ).scalar_one())
        current_ar = billed - collected
        ship_billing = sum(
            req_qty * line_map[lid].unit_price
            + compute_tax(req_qty * line_map[lid].unit_price, line_map[lid].item.tax_type == "taxable")
            for lid, req_qty in requested.items()
        )
        if current_ar + ship_billing > so.partner.credit_limit:
            raise HTTPException(
                status_code=400,
                detail=(f"여신한도를 초과합니다 (한도 {so.partner.credit_limit:,}, "
                        f"현재 미수 {current_ar:,}, 이번 출고 {ship_billing:,})"),
            )

    # 출고 반영: OUT 이동 생성(잔고 차감). 재고 부족 시 StockError -> 400 (트랜잭션 롤백)
    try:
        for entry in payload.lines:
            l = line_map[entry.line_id]
            post_movement(
                db, item_id=l.item_id, movement_type="OUT", quantity=entry.qty,
                unit_price=l.unit_price, partner_id=so.partner_id,
                ref_type="SO", ref_line_id=l.id, note=f"수주 {so.so_no} 출고",
            )
            l.shipped_qty += entry.qty
    except StockError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

    so.status = "shipped" if all(l.shipped_qty >= l.qty for l in so.lines) else "partial"
    shipped_now = {str(e.line_id): e.qty for e in payload.lines}
    record_audit(db, user, "UPDATE", "sales_order", so.id,
                 before={"status": before_status},
                 after={"status": so.status, "shipped": shipped_now})
    db.commit()
    db.refresh(so)
    return _so_out(db, so)


@router.post("/{so_id}/return", response_model=SalesOrderOut)
def return_sales_order(
    so_id: int,
    payload: ReturnRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("sales:write")),
):
    """매출 반품(고객이 출고분을 반품). 재고 증가·미수 차감. 반품 수량은 라인별
    순 출고(출고 − 기존 반품) 이내여야 한다."""
    so = _get_so(db, so_id, lock=True)
    if so.status not in ("partial", "shipped"):
        raise HTTPException(status_code=400, detail="출고 이력이 있는 수주만 반품할 수 있습니다")

    line_map = {l.id: l for l in so.lines}
    requested: dict[int, int] = {}
    for entry in payload.lines:
        l = line_map.get(entry.line_id)
        if not l:
            raise HTTPException(status_code=404, detail=f"명세를 찾을 수 없습니다 (line_id={entry.line_id})")
        requested[l.id] = requested.get(l.id, 0) + entry.qty
    for line_id, req_qty in requested.items():
        l = line_map[line_id]
        returnable = l.shipped_qty - l.returned_qty
        if req_qty > returnable:
            raise HTTPException(status_code=400, detail=f"반품 수량이 반품 가능 수량을 초과합니다 (가능 {returnable})")

    for line_id, req_qty in requested.items():
        l = line_map[line_id]
        post_return(
            db, item_id=l.item_id, kind="sales", quantity=req_qty,
            unit_price=l.unit_price, partner_id=so.partner_id,
            ref_type="SRET", ref_line_id=l.id, note=f"수주 {so.so_no} 매출반품",
        )
        l.returned_qty += req_qty

    record_audit(db, user, "UPDATE", "sales_order", so.id,
                 after={"returned": {str(k): v for k, v in requested.items()}})
    db.commit()
    db.refresh(so)
    return _so_out(db, so)


@router.post("/{so_id}/cancel", response_model=SalesOrderOut)
def cancel_sales_order(
    so_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("sales:write")),
):
    so = _get_so(db, so_id, lock=True)
    if so.status in ("shipped", "cancelled"):
        raise HTTPException(status_code=400, detail="이미 완료/취소된 수주입니다")
    if any(l.shipped_qty > 0 for l in so.lines):
        raise HTTPException(status_code=400, detail="출고 이력이 있어 취소할 수 없습니다")
    if _collected_for(db, so.id) > 0:
        raise HTTPException(status_code=400, detail="수금 이력이 있는 수주는 취소할 수 없습니다. 결제를 먼저 삭제하세요")
    before = {"status": so.status}
    so.status = "cancelled"
    record_audit(db, user, "UPDATE", "sales_order", so.id,
                 before=before, after={"status": "cancelled"})
    db.commit()
    db.refresh(so)
    return _so_out(db, so)


@router.delete("/{so_id}", status_code=204)
def delete_sales_order(
    so_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("sales:write")),
):
    so = _get_so(db, so_id, lock=True)
    if so.status != "draft":
        raise HTTPException(status_code=400, detail="draft 상태의 수주만 삭제할 수 있습니다")
    before = {"so_no": so.so_no, "status": so.status}
    db.delete(so)
    record_audit(db, user, "DELETE", "sales_order", so_id, before=before)
    db.commit()
