from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Partner, User
from ..schemas import PartnerCreate, PartnerUpdate, PartnerOut, Page
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

    data = payload.model_dump()
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
    before = {"code": partner.code, "name": partner.name}
    db.delete(partner)
    record_audit(db, user, "DELETE", "partner", partner_id, before=before)
    db.commit()
