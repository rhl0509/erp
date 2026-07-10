import jwt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import User
from .security import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    cred_exc = HTTPException(
        status_code=401,
        detail="인증에 실패했습니다",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        user_id = int(payload.get("sub"))
    except (jwt.PyJWTError, TypeError, ValueError):
        # PyJWTError: 서명/만료 등 토큰 자체 오류, TypeError/ValueError: sub 누락·비정상
        raise cred_exc

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise cred_exc
    return user


def user_permissions(user: User) -> set[str]:
    perms: set[str] = set()
    for role in user.roles:
        for perm in role.permissions:
            perms.add(perm.code)
    return perms


def require_permission(code: str):
    """특정 권한 코드를 요구하는 의존성. '*' 권한은 모든 것을 통과시킨다."""

    def checker(user: User = Depends(get_current_user)) -> User:
        perms = user_permissions(user)
        if "*" in perms or code in perms:
            return user
        raise HTTPException(status_code=403, detail=f"권한이 없습니다: {code}")

    return checker
