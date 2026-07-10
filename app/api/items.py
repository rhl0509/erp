from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Item, StockMovement, PurchaseOrderLine, SalesOrderLine, User
from ..schemas import ItemCreate, ItemUpdate, ItemOut, Page
from ..deps import require_permission
from ..services import generate_code, record_audit, paginate

router = APIRouter(prefix="/api/items", tags=["items"])


@router.get("", response_model=Page[ItemOut])
def list_items(
    q: str | None = Query(None, description="이름 또는 코드 검색"),
    item_type: str | None = Query(None, description="product/material/service 로 필터"),
    page: int = Query(1, ge=1, description="페이지 번호(1부터)"),
    page_size: int = Query(20, ge=1, le=100, description="페이지당 건수(최대 100)"),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("item:read")),
):
    stmt = select(Item).order_by(Item.id.desc())
    if q:
        stmt = stmt.where(or_(Item.name.like(f"%{q}%"), Item.code.like(f"%{q}%")))
    if item_type:
        stmt = stmt.where(Item.item_type == item_type)
    return paginate(db, stmt, page, page_size)


@router.post("", response_model=ItemOut, status_code=201)
def create_item(
    payload: ItemCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("item:write")),
):
    code = generate_code(db, "ITEM", "ITEM")
    item = Item(code=code, **payload.model_dump())
    db.add(item)
    db.flush()
    record_audit(db, user, "CREATE", "item", item.id, after=payload.model_dump())
    db.commit()
    db.refresh(item)
    return item


@router.get("/{item_id}", response_model=ItemOut)
def get_item(
    item_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("item:read")),
):
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="품목을 찾을 수 없습니다")
    return item


@router.put("/{item_id}", response_model=ItemOut)
def update_item(
    item_id: int,
    payload: ItemUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("item:write")),
):
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="품목을 찾을 수 없습니다")

    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="수정할 항목이 없습니다")
    before = {k: getattr(item, k) for k in data.keys()}
    for k, v in data.items():
        setattr(item, k, v)
    record_audit(db, user, "UPDATE", "item", item.id, before=before, after=data)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{item_id}", status_code=204)
def delete_item(
    item_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("item:write")),
):
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="품목을 찾을 수 없습니다")
    # 참조 데이터가 있으면 FK 오류(500) 대신 409로 안내한다.
    referenced = (
        db.execute(select(StockMovement.id)
                   .where(StockMovement.item_id == item_id).limit(1)).first()
        or db.execute(select(PurchaseOrderLine.id)
                      .where(PurchaseOrderLine.item_id == item_id).limit(1)).first()
        or db.execute(select(SalesOrderLine.id)
                      .where(SalesOrderLine.item_id == item_id).limit(1)).first()
    )
    if referenced:
        raise HTTPException(
            status_code=409,
            detail="입출고·발주·수주 이력이 있는 품목은 삭제할 수 없습니다. 대신 비활성화하세요.",
        )
    before = {"code": item.code, "name": item.name}
    db.delete(item)
    record_audit(db, user, "DELETE", "item", item_id, before=before)
    db.commit()
