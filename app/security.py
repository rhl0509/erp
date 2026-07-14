import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .config import settings

# 인증 세션 쿠키 이름 — 로그인 시 설정(auth.py), 인증 시 헤더 대안으로 읽음(deps.py).
SESSION_COOKIE_NAME = "erp_session"

# 사전 공격에 즉시 뚫리는 흔한 비밀번호. 길이·복잡도만으로는 못 거르는 부류를 막는다.
# (전체 유출 목록을 다 담을 수는 없으므로 '자주 쓰이는 것'만 — 방어의 마지막 층이 아니라 첫 층)
_COMMON_PASSWORDS = {
    "password", "passw0rd", "password1", "password123", "qwerty123", "1q2w3e4r",
    "12345678", "123456789", "1234567890", "abcd1234", "qwer1234", "asdf1234",
    "admin1234", "administrator", "letmein123", "welcome123", "iloveyou123",
    "erp123456", "company123", "manager123", "test1234", "changeme123",
}


def password_policy_text() -> str:
    """정책을 사람이 읽는 한 줄로. 서버 오류 메시지와 화면 안내가 갈라지지 않도록 여기서 만든다."""
    return (
        f"{settings.password_min_length}자 이상이며 영문과 숫자를 모두 포함해야 합니다"
        " (아이디 포함·흔한 비밀번호 불가)"
    )


def validate_password(password: str, username: str = "") -> None:
    """비밀번호 정책 검증. 위반하면 ValueError(사용자에게 그대로 보여줄 한글 메시지).

    규칙: 길이 하한 · 영문+숫자 · 공백 금지 · 아이디 포함 금지 · 흔한 비밀번호 금지.
    (대소문자·특수문자 강제는 넣지 않는다 — 사용자가 규칙을 우회하는 예측 가능한 변형을
    만들게 되어 실질 강도가 오르지 않는다. 길이와 블록리스트가 더 효과적이다.)
    """
    if len(password) < settings.password_min_length:
        raise ValueError(f"비밀번호는 {settings.password_min_length}자 이상이어야 합니다")
    if re.search(r"\s", password):
        raise ValueError("비밀번호에 공백을 포함할 수 없습니다")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValueError("비밀번호는 영문과 숫자를 모두 포함해야 합니다")
    low = password.lower()
    if low in _COMMON_PASSWORDS:
        raise ValueError("너무 흔한 비밀번호입니다. 다른 값을 사용하세요")
    if username and len(username) >= 3 and username.lower() in low:
        raise ValueError("비밀번호에 아이디를 포함할 수 없습니다")


def password_strength(password: str) -> int:
    """0~4 강도 점수. 화면 강도 표시와 서버 판단이 어긋나지 않게 서버에도 같은 함수를 둔다."""
    if not password:
        return 0
    score = 0
    if len(password) >= settings.password_min_length:
        score += 1
    if len(password) >= 14:
        score += 1
    if re.search(r"[A-Za-z]", password) and re.search(r"\d", password):
        score += 1
    if re.search(r"[^A-Za-z0-9]", password):
        score += 1
    return min(score, 4)


def generate_password(length: int = 14) -> str:
    """정책을 통과하는 임시 비밀번호 생성(관리자 발급용). 영문+숫자를 반드시 섞는다."""
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"  # 혼동되는 l,o,I,O 제외
    digits = "23456789"
    while True:
        raw = "".join(secrets.choice(alphabet + digits) for _ in range(length))
        try:
            validate_password(raw)
            return raw
        except ValueError:
            continue


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def create_access_token(
    subject: int | str, token_version: int = 1, expires_minutes: int | None = None
) -> str:
    """액세스 토큰. token_version(tv)을 함께 심어 서버가 세션을 무효화할 수 있게 한다
    — 비번 변경·재설정·강제 로그아웃 시 User.token_version 을 올리면 옛 토큰은 거부된다."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.access_token_expire_minutes
    )
    payload = {"sub": str(subject), "tv": int(token_version), "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


# ---------- 비밀번호 재설정 토큰 ----------
def new_reset_token() -> tuple[str, str]:
    """(원문, sha256 해시). 원문은 메일 링크로만 나가고 DB 에는 해시만 저장한다."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_reset_token(raw)


def hash_reset_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
