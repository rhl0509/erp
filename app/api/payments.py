from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Payment, Partner, PurchaseOrder, SalesOrder, User
from ..schemas import PaymentCreate, PaymentOut, Page
from ..deps import require_permission
from ..services import generate_code, record_audit, paginate, compute_tax
from ..gl import post_for_payment, delete_for_source

router = APIRouter(prefix="/api/payments", tags=["payments"])


def _out(p: Payment) -> PaymentOut:
    return PaymentOut(
        id=p.id, payment_no=p.payment_no, partner_id=p.partner_id,
        partner_name=p.partner.name if p.partner else "",
        kind=p.kind, amount=p.amount, pay_date=p.pay_date, method=p.method,
        note=p.note, ref_type=p.ref_type, ref_id=p.ref_id, created_at=p.created_at,
    )


@router.get("", response_model=Page[PaymentOut])
def list_payments(
    partner_id: int | None = Query(None, description="거래처로 필터"),
    kind: str | None = Query(None, description="AP(지급)/AR(수금)"),
    ref_type: str | None = Query(None, description="PO/SO 문서 귀속 필터"),
    ref_id: int | None = Query(None, description="문서 id 로 필터"),
    date_from: str | None = Query(None, description="pay_date 이상 (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="pay_date 이하 (YYYY-MM-DD)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("payment:read")),
):
    stmt = select(Payment).order_by(Payment.id.desc())
    if partner_id:
        stmt = stmt.where(Payment.partner_id == partner_id)
    if kind:
        stmt = stmt.where(Payment.kind == kind)
    if ref_type:
        stmt = stmt.where(Payment.ref_type == ref_type)
    if ref_id:
        stmt = stmt.where(Payment.ref_id == ref_id)
    if date_from:
        stmt = stmt.where(Payment.pay_date >= date_from)
    if date_to:
        stmt = stmt.where(Payment.pay_date <= date_to)
    result = paginate(db, stmt, page, page_size)
    result["items"] = [_out(p) for p in result["items"]]
    return result


@router.post("", response_model=PaymentOut, status_code=201)
def create_payment(
    payload: PaymentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("payment:write")),
):
    if not db.get(Partner, payload.partner_id):
        raise HTTPException(status_code=404, detail="거래처를 찾을 수 없습니다")

    # 특정 문서(발주/수주)에 귀속하는 결제면 문서·거래처·구분 정합성을 검증한다.
    if payload.ref_type:
        if payload.ref_id is None:
            raise HTTPException(status_code=400, detail="문서 id(ref_id)가 필요합니다")
        # 문서 행을 잠근다: 잔액(outstanding) 조회~INSERT 사이에 다른 결제가 끼어들어
        # 과지급을 통과시키는 경합(check-then-insert)을 직렬화로 막는다.
        if payload.ref_type == "PO":
            doc = db.execute(
                select(PurchaseOrder).where(PurchaseOrder.id == payload.ref_id).with_for_update()
            ).scalar_one_or_none()
            if not doc:
                raise HTTPException(status_code=404, detail="발주서를 찾을 수 없습니다")
            if payload.kind != "AP":
                raise HTTPException(status_code=400, detail="발주 결제는 지급(AP)이어야 합니다")
        else:  # SO
            doc = db.execute(
                select(SalesOrder).where(SalesOrder.id == payload.ref_id).with_for_update()
            ).scalar_one_or_none()
            if not doc:
                raise HTTPException(status_code=404, detail="수주서를 찾을 수 없습니다")
            if payload.kind != "AR":
                raise HTTPException(status_code=400, detail="수주 결제는 수금(AR)이어야 합니다")
        if doc.partner_id != payload.partner_id:
            raise HTTPException(status_code=400, detail="문서의 거래처와 일치하지 않습니다")
        # 미확정(draft)·취소된 문서에는 결제를 붙일 수 없다.
        if doc.status in ("draft", "cancelled"):
            raise HTTPException(
                status_code=400, detail="작성 중(draft)이거나 취소된 문서에는 결제를 등록할 수 없습니다"
            )
        # 과지급/과수금 차단: 누적 결제가 주문 합계금액(공급가액+부가세, 선지급 허용 상한)을
        # 넘으면 안 된다. (미지급/미수 잔액 자체는 납품 기준으로 문서·거래처 화면에 표시된다.)
        grand_total = sum(
            l.qty * l.unit_price + compute_tax(l.qty * l.unit_price, l.item.tax_type == "taxable")
            for l in doc.lines
        )
        already_paid = int(db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.ref_type == payload.ref_type,
                Payment.ref_id == payload.ref_id,
                Payment.kind == payload.kind,
            )
        ).scalar_one())
        payable = grand_total - already_paid
        if payload.amount > payable:
            raise HTTPException(
                status_code=400,
                detail=f"결제 금액이 주문 총액을 초과합니다 (남은 결제 가능액: {payable})",
            )

    payment = Payment(
        payment_no=generate_code(db, "PAY", "PAY", use_period=True),
        partner_id=payload.partner_id,
        kind=payload.kind,
        amount=payload.amount,
        pay_date=payload.pay_date or datetime.now().strftime("%Y-%m-%d"),
        method=payload.method,
        note=payload.note,
        ref_type=payload.ref_type,
        ref_id=payload.ref_id,
    )
    db.add(payment)
    db.flush()
    # GL 전기(같은 트랜잭션): AP 지급=외상매입금 차감, AR 수금=외상매출금 차감.
    post_for_payment(db, payment)
    record_audit(db, user, "CREATE", "payment", payment.id,
                 after={"payment_no": payment.payment_no, "kind": payment.kind, "amount": payment.amount})
    db.commit()
    db.refresh(payment)
    return _out(payment)


@router.get("/{payment_id}", response_model=PaymentOut)
def get_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("payment:read")),
):
    p = db.get(Payment, payment_id)
    if not p:
        raise HTTPException(status_code=404, detail="결제 내역을 찾을 수 없습니다")
    return _out(p)


@router.delete("/{payment_id}", status_code=204)
def delete_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("payment:write")),
):
    p = db.get(Payment, payment_id)
    if not p:
        raise HTTPException(status_code=404, detail="결제 내역을 찾을 수 없습니다")
    before = {"payment_no": p.payment_no, "kind": p.kind, "amount": p.amount}
    # 원천 삭제 시 전표 동반 삭제(같은 트랜잭션) — 삭제 이력은 AuditLog 가 담당.
    delete_for_source(db, "PAYMENT", p.id)
    db.delete(p)
    record_audit(db, user, "DELETE", "payment", payment_id, before=before)
    db.commit()
