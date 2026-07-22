from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AuditLog, User
from ..schemas import Page, AuditLogOut
from ..deps import require_permission
from ..services import paginate
from .params import parse_date

router = APIRouter(prefix="/api/audit", tags=["audit"])

_ACTIONS = ("CREATE", "UPDATE", "DELETE")
_ENTITIES = ("user", "partner", "item", "stock", "purchase_order", "sales_order",
             "payment", "tax_invoice", "warehouse", "gl", "gl_period")


@router.get("", response_model=Page[AuditLogOut])
def list_audit_logs(
    action: str | None = Query(None, description="CREATE/UPDATE/DELETE 필터"),
    entity: str | None = Query(None, description="user/partner/item/stock/purchase_order/sales_order/payment 필터"),
    q: str | None = Query(None, description="행위자(아이디)·대상ID 검색"),
    date_from: str | None = Query(None, description="시작일 YYYY-MM-DD(당일 포함)"),
    date_to: str | None = Query(None, description="종료일 YYYY-MM-DD(당일 포함)"),
    page: int = Query(1, ge=1, description="페이지 번호(1부터)"),
    page_size: int = Query(20, ge=1, le=100, description="페이지당 건수(최대 100)"),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("audit:read")),
):
    if action is not None and action not in _ACTIONS:
        raise HTTPException(status_code=400, detail="action은 CREATE/UPDATE/DELETE 여야 합니다")
    if entity is not None and entity not in _ENTITIES:
        raise HTTPException(status_code=400, detail=f"entity는 {'/'.join(_ENTITIES)} 중 하나여야 합니다")

    stmt = select(AuditLog).order_by(AuditLog.id.desc())  # 최신순
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if entity:
        stmt = stmt.where(AuditLog.entity == entity)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(AuditLog.username.like(like), AuditLog.entity_id.like(like)))
    if date_from:
        stmt = stmt.where(AuditLog.created_at >= parse_date(date_from))
    if date_to:
        # 종료일 당일 포함 → 다음날 0시 미만
        stmt = stmt.where(AuditLog.created_at < parse_date(date_to) + timedelta(days=1))

    return paginate(db, stmt, page, page_size)
