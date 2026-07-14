import jwt
from fastapi import Cookie, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import User
from .security import decode_token, SESSION_COOKIE_NAME

# auto_error=False: 헤더가 없어도 401을 던지지 않고 쿠키로 대체할 수 있게 한다.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def get_current_user(
    header_token: str | None = Depends(oauth2_scheme),
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: Session = Depends(get_db),
) -> User:
    """인증 토큰을 Authorization 헤더 우선, 없으면 httpOnly 세션 쿠키에서 읽는다.
    헤더 방식(curl·API 클라이언트)과 쿠키 방식(Next 앱)을 모두 수용.

    토큰의 tv(token_version)가 현재 사용자 값과 다르면 거부한다 — 비밀번호 변경·재설정·
    임시비번 발급 이전에 발급된 토큰을 즉시 무효화하는 장치(JWT 는 무상태라 서버가
    폐기할 수 없으므로 버전으로 대신한다)."""
    cred_exc = HTTPException(
        status_code=401,
        detail="인증에 실패했습니다",
        headers={"WWW-Authenticate": "Bearer"},
    )
    token = header_token or session_cookie
    if not token:
        raise cred_exc
    try:
        payload = decode_token(token)
        user_id = int(payload.get("sub"))
        token_version = int(payload.get("tv", 1))
    except (jwt.PyJWTError, TypeError, ValueError):
        # PyJWTError: 서명/만료 등 토큰 자체 오류, TypeError/ValueError: sub 누락·비정상
        raise cred_exc

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise cred_exc
    if token_version != user.token_version:
        raise HTTPException(
            status_code=401,
            detail="세션이 만료되었습니다. 다시 로그인해 주세요",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def user_permissions(user: User) -> set[str]:
    perms: set[str] = set()
    for role in user.roles:
        for perm in role.permissions:
            perms.add(perm.code)
    return perms


def require_password_ok(user: User = Depends(get_current_user)) -> User:
    """비밀번호 강제 변경 게이트. must_change_password 인 사용자는 업무 API 를 쓸 수 없고
    비밀번호를 바꿔야 한다(임시비번 발급·정책 미달 계정). /api/auth/* 의 본인 관리
    엔드포인트는 get_current_user 만 쓰므로 이 게이트를 타지 않아 변경이 가능하다."""
    if user.must_change_password:
        raise HTTPException(
            status_code=403,
            detail="비밀번호를 변경해야 계속 사용할 수 있습니다",
            headers={"X-Password-Change-Required": "1"},
        )
    return user


def require_permission(code: str):
    """특정 권한 코드를 요구하는 의존성. '*' 권한은 모든 것을 통과시킨다.
    모든 업무 라우터가 이 의존성을 거치므로 비밀번호 강제 변경 게이트도 여기에 둔다."""

    def checker(user: User = Depends(require_password_ok)) -> User:
        perms = user_permissions(user)
        if "*" in perms or code in perms:
            return user
        raise HTTPException(status_code=403, detail=f"권한이 없습니다: {code}")

    return checker
