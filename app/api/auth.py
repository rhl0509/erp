import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import User
from ..security import (
    verify_password, create_access_token, hash_password, SESSION_COOKIE_NAME,
)
from ..schemas import Token, MeOut, RegisterCreate, UserOut, PasswordChange
from ..deps import get_current_user, user_permissions
from ..services import record_audit

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str) -> None:
    """로그인 시 httpOnly 세션 쿠키를 심는다(Next 앱이 localStorage 없이 인증).
    same-origin 프록시라 samesite=lax 로 충분하며, HTTPS 배포 시 secure 로 켠다.
    max_age 는 토큰 만료와 맞춘다."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite=settings.cookie_samesite,
        secure=settings.cookie_secure,
        max_age=settings.access_token_expire_minutes * 60,
        path="/",
    )


class _RateLimiter:
    """관찰 창 안의 이벤트 수로 제한하는 간단한 인메모리 슬라이딩 윈도우 제한기.

    주의: 프로세스 로컬이라 다중 워커/인스턴스에서는 워커별로 계산된다. 강한 보장이
    필요하면 Redis 등 공유 저장소 기반으로 교체할 것. 여기서는 자동화된 무차별
    대입·대량 가입의 비용을 크게 올리는 1차 방어선으로 둔다."""

    def __init__(self, max_events: int, window_sec: float):
        self.max = max_events
        self.window = window_sec
        self._events: dict[str, list[float]] = defaultdict(list)

    def _recent(self, key: str) -> list[float]:
        now = time.monotonic()
        recent = [t for t in self._events.get(key, []) if now - t < self.window]
        self._events[key] = recent
        return recent

    def allowed(self, key: str) -> bool:
        return len(self._recent(key)) < self.max

    def hit(self, key: str) -> None:
        self._events[key].append(time.monotonic())

    def clear(self, key: str) -> None:
        self._events.pop(key, None)


# 로그인 실패 5회/5분 초과 시 잠금, 성공하면 해제. 회원가입은 IP당 10회/시간.
_login_limiter = _RateLimiter(max_events=5, window_sec=300.0)
_register_limiter = _RateLimiter(max_events=10, window_sec=3600.0)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


@router.post("/register", response_model=UserOut, status_code=201)
def register(payload: RegisterCreate, request: Request, db: Session = Depends(get_db)):
    """공개 회원가입. 비활성(is_active=False)·무권한으로 생성되며 관리자 승인 후 로그인 가능."""
    ip = _client_ip(request)
    if not _register_limiter.allowed(ip):
        raise HTTPException(status_code=429, detail="가입 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.")
    _register_limiter.hit(ip)

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
        is_active=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(
    request: Request,
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    key = f"{_client_ip(request)}:{form.username.lower()}"
    if not _login_limiter.allowed(key):
        raise HTTPException(
            status_code=429, detail="로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요."
        )

    user = db.execute(
        select(User).where(User.username == form.username)
    ).scalar_one_or_none()

    if not user or not verify_password(form.password, user.hashed_password):
        _login_limiter.hit(key)   # 실패만 카운트(성공하면 아래에서 해제)
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="비활성화된 계정입니다")

    _login_limiter.clear(key)
    token = create_access_token(user.id)
    # 쿠키(Next 앱)와 응답 본문 토큰(헤더 방식 클라이언트) 둘 다 제공 — 하위호환.
    _set_session_cookie(response, token)
    return Token(access_token=token)


@router.post("/logout", status_code=204)
def logout(response: Response):
    """세션 쿠키를 제거한다. JWT 는 무상태라 서버측 무효화는 없고, 쿠키 클라이언트의
    로그아웃은 쿠키 삭제로 이뤄진다(헤더 방식 클라이언트는 토큰 폐기로 로그아웃)."""
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


@router.put("/me/password", status_code=204)
def change_my_password(
    payload: PasswordChange,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """본인 비밀번호 변경. 현재 비밀번호가 맞아야 하고, 기존과 동일하면 거부한다."""
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="현재 비밀번호가 올바르지 않습니다")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=400, detail="새 비밀번호가 기존과 동일합니다")
    user.hashed_password = hash_password(payload.new_password)
    record_audit(db, user, "UPDATE", "user", user.id, after={"password_changed": True})
    db.commit()


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user)):
    perms = sorted(user_permissions(user))
    return MeOut(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        email=user.email,
        is_active=user.is_active,
        roles=user.roles,
        permissions=perms,
    )
