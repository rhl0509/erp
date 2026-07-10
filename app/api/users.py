from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, Role
from ..schemas import UserOut, UserCreate, UserUpdate, RoleOut, Page
from ..deps import require_permission
from ..security import hash_password
from ..services import record_audit, paginate

router = APIRouter(prefix="/api", tags=["users"])


@router.get("/users", response_model=Page[UserOut])
def list_users(
    status: str | None = Query(None, description="pending(승인대기)/active(활성) 필터"),
    page: int = Query(1, ge=1, description="페이지 번호(1부터)"),
    page_size: int = Query(20, ge=1, le=100, description="페이지당 건수(최대 100)"),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("user:read")),
):
    if status is not None and status not in ("pending", "active"):
        raise HTTPException(status_code=400, detail="status는 pending 또는 active 여야 합니다")
    # 승인 대기(비활성)를 위로 보이도록 is_active 오름차순 → 비활성 먼저
    stmt = select(User).order_by(User.is_active.asc(), User.id)
    if status == "pending":
        stmt = stmt.where(User.is_active.is_(False))
    elif status == "active":
        stmt = stmt.where(User.is_active.is_(True))
    return paginate(db, stmt, page, page_size)


@router.get("/users/pending/count")
def pending_users_count(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("user:read")),
):
    """승인 대기(비활성) 회원 수. 관리자 알림 뱃지용."""
    count = db.execute(
        select(func.count()).select_from(User).where(User.is_active.is_(False))
    ).scalar_one()
    return {"count": int(count)}


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("user:write")),
):
    exists = db.execute(
        select(User).where(User.username == payload.username)
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="이미 존재하는 아이디입니다")

    user = User(
        username=payload.username,
        full_name=payload.full_name,
        email=payload.email,
        hashed_password=hash_password(payload.password),
    )
    if payload.role_ids:
        roles = db.execute(
            select(Role).where(Role.id.in_(payload.role_ids))
        ).scalars().all()
        user.roles = list(roles)

    db.add(user)
    db.flush()
    record_audit(db, actor, "CREATE", "user", user.id, after={"username": user.username})
    db.commit()
    db.refresh(user)
    return user


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("user:write")),
):
    """회원 수정. 승인은 is_active=true + role_ids 부여로 처리한다."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="회원을 찾을 수 없습니다")

    data = payload.model_dump(exclude_unset=True)
    before = {"is_active": user.is_active, "roles": [r.name for r in user.roles]}
    if "full_name" in data and data["full_name"] is not None:
        user.full_name = data["full_name"]
    if "email" in data and data["email"] is not None:
        user.email = data["email"]
    if "is_active" in data and data["is_active"] is not None:
        user.is_active = data["is_active"]
    if "role_ids" in data and data["role_ids"] is not None:
        roles = db.execute(
            select(Role).where(Role.id.in_(data["role_ids"]))
        ).scalars().all()
        user.roles = list(roles)

    after = {"is_active": user.is_active, "roles": [r.name for r in user.roles]}
    record_audit(db, actor, "UPDATE", "user", user.id, before=before, after=after)
    db.commit()
    db.refresh(user)
    return user


@router.get("/roles", response_model=list[RoleOut])
def list_roles(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("user:read")),
):
    return db.execute(select(Role).order_by(Role.id)).scalars().all()
