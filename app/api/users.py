from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..database import get_db
from ..errors import FieldError
from ..models import User, Role
from ..schemas import (
    UserOut, UserCreate, UserUpdate, UserReject, TempPasswordOut, RoleOut, Page,
)
from ..deps import require_permission
from ..security import hash_password, validate_password, generate_password
from ..services import record_audit, paginate

router = APIRouter(prefix="/api", tags=["users"])

_STATUSES = ("pending", "active", "rejected")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _email_taken(db: Session, email: str, exclude_user_id: int | None = None) -> bool:
    if not email:
        return False
    stmt = select(User).where(User.email == email)
    if exclude_user_id is not None:
        stmt = stmt.where(User.id != exclude_user_id)
    return db.execute(stmt).scalar_one_or_none() is not None


@router.get("/users", response_model=Page[UserOut])
def list_users(
    status: str | None = Query(None, description="pending(승인대기)/active(활성)/rejected(거절) 필터"),
    page: int = Query(1, ge=1, description="페이지 번호(1부터)"),
    page_size: int = Query(20, ge=1, le=100, description="페이지당 건수(최대 100)"),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("user:read")),
):
    if status is not None and status not in _STATUSES:
        raise HTTPException(status_code=400, detail="status는 pending / active / rejected 여야 합니다")
    # 승인 대기(비활성)를 위로 보이도록 is_active 오름차순 → 비활성 먼저
    stmt = select(User).order_by(User.is_active.asc(), User.id)
    if status == "pending":
        stmt = stmt.where(User.is_active.is_(False), User.rejected_at.is_(None))
    elif status == "active":
        stmt = stmt.where(User.is_active.is_(True))
    elif status == "rejected":
        stmt = stmt.where(User.rejected_at.is_not(None))
    return paginate(db, stmt, page, page_size)


@router.get("/users/pending/count")
def pending_users_count(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("user:read")),
):
    """승인 대기 회원 수. 관리자 알림 뱃지용 — 거절된 계정은 세지 않는다."""
    count = db.execute(
        select(func.count()).select_from(User)
        .where(User.is_active.is_(False), User.rejected_at.is_(None))
    ).scalar_one()
    return {"count": int(count)}


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("user:write")),
):
    try:
        validate_password(payload.password, payload.username)
    except ValueError as exc:
        raise FieldError("password", str(exc))

    exists = db.execute(
        select(User).where(User.username == payload.username)
    ).scalar_one_or_none()
    if exists:
        raise FieldError("username", "이미 존재하는 아이디입니다", status_code=409)
    if _email_taken(db, payload.email):
        raise FieldError("email", "이미 사용 중인 이메일입니다", status_code=409)

    user = User(
        username=payload.username,
        full_name=payload.full_name,
        email=payload.email or None,
        hashed_password=hash_password(payload.password),
        password_changed_at=_now(),
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
    """회원 수정. 승인은 is_active=true + role_ids 부여로 처리한다(거절 이력은 승인 시 해제).
    비활성화하면 token_version 을 올려 그 사용자의 기존 세션을 즉시 끊는다."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="회원을 찾을 수 없습니다")

    data = payload.model_dump(exclude_unset=True)
    before = {"is_active": user.is_active, "roles": [r.name for r in user.roles]}
    if data.get("email"):
        if _email_taken(db, data["email"], exclude_user_id=user.id):
            raise FieldError("email", "이미 사용 중인 이메일입니다", status_code=409)

    if "full_name" in data and data["full_name"] is not None:
        user.full_name = data["full_name"]
    if "email" in data and data["email"] is not None:
        user.email = data["email"] or None
    if "is_active" in data and data["is_active"] is not None:
        was_active = user.is_active
        user.is_active = data["is_active"]
        if user.is_active:
            # 승인(또는 재활성화) — 거절 이력을 지운다.
            user.rejected_at = None
            user.reject_reason = ""
        elif was_active:
            # 비활성화 = 즉시 강제 로그아웃(발급된 토큰 무효화)
            user.token_version += 1
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


@router.post("/users/{user_id}/reject", response_model=UserOut)
def reject_user(
    user_id: int,
    payload: UserReject,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("user:write")),
):
    """가입 거절. 계정을 지우지 않고 거절로 표시한다(같은 아이디 재가입 방지 + 이력 보존).
    거절된 계정은 승인 대기 목록·배지에서 빠지고, 로그인 시 사유가 안내된다."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="회원을 찾을 수 없습니다")
    if user.is_active:
        raise HTTPException(status_code=400, detail="이미 승인된 계정입니다. 거절하려면 먼저 비활성화하세요")

    user.rejected_at = _now()
    user.reject_reason = payload.reason
    record_audit(db, actor, "UPDATE", "user", user.id,
                 before={"status": "pending"},
                 after={"status": "rejected", "reason": payload.reason})
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/temp-password", response_model=TempPasswordOut)
def issue_temp_password(
    user_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("user:write")),
):
    """임시 비밀번호 발급(비밀번호 분실 구제 — SMTP 미설정 환경의 재설정 경로).

    평문은 이 응답에서 딱 한 번 나오고 DB 에는 해시만 남는다. 발급 즉시 기존 세션을
    모두 끊고(token_version) 다음 로그인에서 비밀번호 변경을 강제한다(must_change_password).
    """
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="회원을 찾을 수 없습니다")

    temp = generate_password()
    user.hashed_password = hash_password(temp)
    user.token_version += 1
    user.must_change_password = True
    user.password_changed_at = _now()
    record_audit(db, actor, "UPDATE", "user", user.id, after={"temp_password_issued": True})
    db.commit()
    return TempPasswordOut(username=user.username, temp_password=temp)


@router.post("/users/{user_id}/2fa/disable", response_model=UserOut)
def admin_disable_totp(
    user_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("user:write")),
):
    """관리자의 2FA 해제 — 인증 앱을 잃어 로그인 자체가 불가능해진 계정의 구제 경로."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="회원을 찾을 수 없습니다")
    if not user.totp_enabled:
        raise HTTPException(status_code=400, detail="2단계 인증이 켜져 있지 않습니다")

    user.totp_enabled = False
    user.totp_secret = ""
    record_audit(db, actor, "UPDATE", "user", user.id,
                 after={"totp_enabled": False, "by": "admin"})
    db.commit()
    db.refresh(user)
    return user


@router.get("/roles", response_model=list[RoleOut])
def list_roles(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("user:read")),
):
    return db.execute(select(Role).order_by(Role.id)).scalars().all()
