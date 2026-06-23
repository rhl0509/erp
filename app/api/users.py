from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, Role
from ..schemas import UserOut, UserCreate, RoleOut, Page
from ..deps import require_permission
from ..security import hash_password
from ..services import record_audit, paginate

router = APIRouter(prefix="/api", tags=["users"])


@router.get("/users", response_model=Page[UserOut])
def list_users(
    page: int = Query(1, ge=1, description="페이지 번호(1부터)"),
    page_size: int = Query(20, ge=1, le=100, description="페이지당 건수(최대 100)"),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("user:read")),
):
    return paginate(db, select(User).order_by(User.id), page, page_size)


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


@router.get("/roles", response_model=list[RoleOut])
def list_roles(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("user:read")),
):
    return db.execute(select(Role).order_by(Role.id)).scalars().all()
