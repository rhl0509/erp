from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Warehouse, User
from ..schemas import WarehouseCreate, WarehouseUpdate, WarehouseOut, Page
from ..deps import require_permission
from ..services import generate_code, record_audit, paginate

# 권한은 기존 stock:read / stock:write 를 재사용한다 — 창고는 재고 차원의 마스터라
# 재고 권한과 생명주기가 같고, 신규 권한 시딩(기존 역할 갱신) 복잡도를 피한다.
router = APIRouter(prefix="/api/warehouses", tags=["warehouses"])


def _get_warehouse(db: Session, warehouse_id: int) -> Warehouse:
    wh = db.get(Warehouse, warehouse_id)
    if not wh:
        raise HTTPException(status_code=404, detail="창고를 찾을 수 없습니다")
    return wh


@router.get("", response_model=Page[WarehouseOut])
def list_warehouses(
    q: str | None = Query(None, description="창고 이름 또는 코드 검색"),
    active_only: bool = Query(False, description="활성 창고만 조회"),
    page: int = Query(1, ge=1, description="페이지 번호(1부터)"),
    page_size: int = Query(20, ge=1, le=100, description="페이지당 건수(최대 100)"),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("stock:read")),
):
    # 기본창고가 항상 목록 위에 오도록 정렬(선택 UI 에서 기본값 노출용)
    stmt = select(Warehouse).order_by(Warehouse.is_default.desc(), Warehouse.id.asc())
    if q:
        stmt = stmt.where(or_(Warehouse.name.like(f"%{q}%"), Warehouse.code.like(f"%{q}%")))
    if active_only:
        stmt = stmt.where(Warehouse.is_active.is_(True))
    return paginate(db, stmt, page, page_size)


@router.post("", response_model=WarehouseOut, status_code=201)
def create_warehouse(
    payload: WarehouseCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("stock:write")),
):
    code = payload.code.strip()
    if code:
        dup = db.execute(select(Warehouse).where(Warehouse.code == code)).scalar_one_or_none()
        if dup:
            raise HTTPException(status_code=409, detail=f"이미 존재하는 창고 코드입니다: {code}")
    else:
        code = generate_code(db, "WAREHOUSE", "WH")

    # 신규 창고는 기본창고가 될 수 없다(기본 변경은 update 의 is_default 로만 —
    # '기본창고 정확히 1개' 불변식을 한 경로에서만 다루기 위함).
    wh = Warehouse(code=code, name=payload.name, is_default=False, is_active=payload.is_active)
    db.add(wh)
    db.flush()
    record_audit(db, user, "CREATE", "warehouse", wh.id,
                 after={"code": wh.code, "name": wh.name})
    db.commit()
    db.refresh(wh)
    return wh


@router.put("/{warehouse_id}", response_model=WarehouseOut)
def update_warehouse(
    warehouse_id: int,
    payload: WarehouseUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("stock:write")),
):
    wh = _get_warehouse(db, warehouse_id)
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="수정할 항목이 없습니다")

    # 기본창고 불변식: 항상 정확히 1개. 해제는 불가(다른 창고를 기본으로 지정해서 교체),
    # 지정 시 기존 기본창고를 내린다. 기본창고는 비활성화할 수 없다.
    if data.get("is_default") is False and wh.is_default:
        raise HTTPException(status_code=400,
                            detail="기본창고 해제는 다른 창고를 기본창고로 지정해서 교체하세요")
    if data.get("is_active") is False and (wh.is_default or data.get("is_default")):
        raise HTTPException(status_code=400, detail="기본창고는 비활성화할 수 없습니다")
    if data.get("is_default") and not data.get("is_active", wh.is_active):
        raise HTTPException(status_code=400, detail="비활성 창고는 기본창고로 지정할 수 없습니다")

    new_code = data.get("code")
    if new_code and new_code != wh.code:
        dup = db.execute(select(Warehouse).where(Warehouse.code == new_code)).scalar_one_or_none()
        if dup:
            raise HTTPException(status_code=409, detail=f"이미 존재하는 창고 코드입니다: {new_code}")

    before = {"code": wh.code, "name": wh.name,
              "is_default": wh.is_default, "is_active": wh.is_active}
    if data.get("is_default") and not wh.is_default:
        # 기존 기본창고를 내린다(교체). 동시 교체 경합은 저빈도 관리 작업이라 락 생략.
        for other in db.execute(
            select(Warehouse).where(Warehouse.is_default.is_(True))
        ).scalars().all():
            other.is_default = False
    for field, value in data.items():
        setattr(wh, field, value)
    db.flush()
    record_audit(db, user, "UPDATE", "warehouse", wh.id, before=before,
                 after={"code": wh.code, "name": wh.name,
                        "is_default": wh.is_default, "is_active": wh.is_active})
    db.commit()
    db.refresh(wh)
    return wh
