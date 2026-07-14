import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pyotp
import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..errors import FieldError
from ..mailer import send_mail
from ..models import PasswordResetToken, User
from ..security import (
    verify_password, create_access_token, hash_password, validate_password,
    password_policy_text, new_reset_token, hash_reset_token, SESSION_COOKIE_NAME,
)
from ..schemas import (
    Token, MeOut, RegisterCreate, UserOut, PasswordChange, PasswordPolicyOut,
    ForgotPasswordIn, ForgotPasswordOut, ResetPasswordIn, UsernameCheckOut,
    ProfileUpdate, TotpSetupOut, TotpCodeIn, TotpDisableIn,
)
from ..deps import get_current_user, user_permissions
from ..services import record_audit

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _now() -> datetime:
    """DB 컬럼이 naive DateTime 이라 저장용 시각도 naive UTC 로 맞춘다."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
# 비번 재설정 요청은 IP당 5회/시간(메일 폭탄·계정 탐색 방지), 아이디 중복확인은 60회/분.
_login_limiter = _RateLimiter(max_events=5, window_sec=300.0)
_register_limiter = _RateLimiter(max_events=10, window_sec=3600.0)
_forgot_limiter = _RateLimiter(max_events=5, window_sec=3600.0)
_check_limiter = _RateLimiter(max_events=60, window_sec=60.0)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _check_password_policy(password: str, username: str, field: str = "password") -> None:
    """정책 위반을 필드 오류(422)로 바꾼다 — 화면이 입력칸 옆에 그대로 붙일 수 있게."""
    try:
        validate_password(password, username)
    except ValueError as exc:
        raise FieldError(field, str(exc))


def _email_taken(db: Session, email: str, exclude_user_id: int | None = None) -> bool:
    if not email:
        return False
    stmt = select(User).where(User.email == email)
    if exclude_user_id is not None:
        stmt = stmt.where(User.id != exclude_user_id)
    return db.execute(stmt).scalar_one_or_none() is not None


@router.get("/password-policy", response_model=PasswordPolicyOut)
def password_policy():
    """비밀번호 정책. 화면 안내·검증 문구를 서버 규칙과 한 곳에서 맞추기 위한 엔드포인트."""
    return PasswordPolicyOut(min_length=settings.password_min_length, text=password_policy_text())


@router.get("/check-username", response_model=UsernameCheckOut)
def check_username(
    request: Request,
    username: str = Query(min_length=2, max_length=50),
    db: Session = Depends(get_db),
):
    """가입 폼의 아이디 중복 확인. 존재 여부만 돌려주므로 남용되지 않게 IP 제한을 둔다."""
    ip = _client_ip(request)
    if not _check_limiter.allowed(ip):
        raise HTTPException(status_code=429, detail="요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.")
    _check_limiter.hit(ip)

    exists = db.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()
    return UsernameCheckOut(username=username, available=exists is None)


@router.post("/register", response_model=UserOut, status_code=201)
def register(payload: RegisterCreate, request: Request, db: Session = Depends(get_db)):
    """공개 회원가입. 비활성(is_active=False)·무권한으로 생성되며 관리자 승인 후 로그인 가능."""
    ip = _client_ip(request)
    if not _register_limiter.allowed(ip):
        raise HTTPException(status_code=429, detail="가입 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.")
    _register_limiter.hit(ip)

    _check_password_policy(payload.password, payload.username)

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
        email=payload.email or None,   # 미입력은 NULL(유니크 제약과 공존)
        department=payload.department,
        signup_reason=payload.signup_reason,
        hashed_password=hash_password(payload.password),
        is_active=False,
        password_changed_at=_now(),
    )
    db.add(user)
    db.flush()
    # 가입 신청도 이력으로 남긴다(승인·거절과 짝이 맞아야 추적이 된다).
    record_audit(db, None, "CREATE", "user", user.id,
                 after={"username": user.username, "status": "pending", "via": "register"})
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(
    request: Request,
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
    otp: str = Form("", description="2단계 인증(TOTP) 6자리 코드 — 2FA 사용자만"),
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
        if user.rejected_at:
            reason = f" (사유: {user.reject_reason})" if user.reject_reason else ""
            raise HTTPException(status_code=403, detail=f"가입이 거절된 계정입니다{reason}")
        raise HTTPException(status_code=403, detail="승인 대기 중인 계정입니다. 관리자 승인 후 로그인할 수 있습니다")

    # 2단계 인증: 비밀번호가 맞아도 OTP 가 없거나 틀리면 통과시키지 않는다.
    if user.totp_enabled:
        if not otp:
            # 화면이 OTP 입력 단계로 전환할 수 있도록 별도 상태코드(401 + 표식 헤더)로 구분한다.
            raise HTTPException(
                status_code=401,
                detail="2단계 인증 코드를 입력해 주세요",
                headers={"X-OTP-Required": "1"},
            )
        if not pyotp.TOTP(user.totp_secret).verify(otp.strip(), valid_window=1):
            _login_limiter.hit(key)
            raise HTTPException(
                status_code=401,
                detail="2단계 인증 코드가 올바르지 않습니다",
                headers={"X-OTP-Required": "1"},
            )

    _login_limiter.clear(key)
    user.last_login_at = _now()
    db.commit()

    token = create_access_token(user.id, user.token_version)
    # 쿠키(Next 앱)와 응답 본문 토큰(헤더 방식 클라이언트) 둘 다 제공 — 하위호환.
    _set_session_cookie(response, token)
    return Token(access_token=token, must_change_password=user.must_change_password)


@router.post("/refresh", response_model=Token)
def refresh(
    response: Response,
    user: User = Depends(get_current_user),
):
    """세션 연장. 만료 임박 시 화면이 호출해 쿠키·토큰을 새로 받는다(재로그인 없이).
    무효화된 토큰(비번 변경 등)은 get_current_user 에서 이미 걸러진다."""
    token = create_access_token(user.id, user.token_version)
    _set_session_cookie(response, token)
    return Token(access_token=token, must_change_password=user.must_change_password)


@router.post("/logout", status_code=204)
def logout(response: Response):
    """세션 쿠키를 제거한다. JWT 는 무상태라 서버측 무효화는 없고, 쿠키 클라이언트의
    로그아웃은 쿠키 삭제로 이뤄진다(헤더 방식 클라이언트는 토큰 폐기로 로그아웃).
    전 세션 강제 로그아웃이 필요하면 비밀번호 변경(token_version 증가)으로 이뤄진다."""
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


@router.put("/me", response_model=MeOut)
def update_my_profile(
    payload: ProfileUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """본인 프로필(이름·이메일·부서) 수정. 역할·활성 여부는 관리자만 바꿀 수 있다."""
    if _email_taken(db, payload.email, exclude_user_id=user.id):
        raise FieldError("email", "이미 사용 중인 이메일입니다", status_code=409)

    before = {"full_name": user.full_name, "email": user.email, "department": user.department}
    user.full_name = payload.full_name
    user.email = payload.email or None
    user.department = payload.department
    after = {"full_name": user.full_name, "email": user.email, "department": user.department}
    record_audit(db, user, "UPDATE", "user", user.id, before=before, after=after)
    db.commit()
    db.refresh(user)
    return _me_out(user)


@router.put("/me/password", status_code=204)
def change_my_password(
    payload: PasswordChange,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """본인 비밀번호 변경. 현재 비밀번호가 맞아야 하고, 기존과 동일하면 거부한다.
    변경 즉시 token_version 을 올려 **다른 기기의 기존 세션을 전부 무효화**하고,
    이 응답에는 새 토큰 쿠키를 심어 지금 쓰는 세션만 살려 둔다."""
    if not verify_password(payload.current_password, user.hashed_password):
        raise FieldError("current_password", "현재 비밀번호가 올바르지 않습니다", status_code=400)
    if payload.new_password == payload.current_password:
        raise FieldError("new_password", "새 비밀번호가 기존과 동일합니다", status_code=400)
    _check_password_policy(payload.new_password, user.username, field="new_password")

    user.hashed_password = hash_password(payload.new_password)
    user.token_version += 1
    user.must_change_password = False
    user.password_changed_at = _now()
    record_audit(db, user, "UPDATE", "user", user.id, after={"password_changed": True})
    db.commit()
    db.refresh(user)
    _set_session_cookie(response, create_access_token(user.id, user.token_version))


# ---------- 비밀번호 찾기 / 재설정 ----------
@router.post("/forgot-password", response_model=ForgotPasswordOut)
def forgot_password(
    payload: ForgotPasswordIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """비밀번호 재설정 요청(아이디 또는 이메일).

    응답은 계정 존재 여부와 무관하게 항상 같다(계정 열거 방지). 실제로 무엇이 일어나는지는
    SMTP 설정 여부로만 갈린다:
      - SMTP 설정됨  → 재설정 링크를 등록된 이메일로 발송(delivery=email)
      - SMTP 미설정  → 관리자에게 임시비밀번호 발급을 요청하도록 안내(delivery=admin)
    """
    ip = _client_ip(request)
    if not _forgot_limiter.allowed(ip):
        raise HTTPException(status_code=429, detail="요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.")
    _forgot_limiter.hit(ip)

    ident = payload.identifier.strip()
    delivery = "email" if settings.smtp_enabled else "admin"

    if settings.smtp_enabled:
        user = db.execute(
            select(User).where(
                or_(User.username == ident, User.email == ident.lower())
            )
        ).scalar_one_or_none()
        # 존재하고·활성이고·이메일이 있는 계정만 실제로 발송한다(응답은 동일).
        if user and user.is_active and user.email:
            raw, token_hash = new_reset_token()
            db.add(PasswordResetToken(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=_now() + timedelta(minutes=settings.reset_token_ttl_minutes),
            ))
            db.commit()
            link = f"{settings.app_base_url.rstrip('/')}/reset-password?token={raw}"
            send_mail(
                user.email,
                "[ERP] 비밀번호 재설정 안내",
                f"{user.full_name or user.username} 님,\n\n"
                f"아래 링크에서 비밀번호를 재설정할 수 있습니다({settings.reset_token_ttl_minutes}분간 유효).\n"
                f"{link}\n\n"
                "본인이 요청하지 않았다면 이 메일을 무시하세요. 링크는 1회만 사용할 수 있습니다.\n",
            )
        return ForgotPasswordOut(
            detail="등록된 이메일로 재설정 링크를 보냈습니다. 메일함을 확인해 주세요.",
            delivery=delivery,
        )

    return ForgotPasswordOut(
        detail="메일 발송이 설정되어 있지 않습니다. 관리자에게 임시 비밀번호 발급을 요청해 주세요.",
        delivery=delivery,
    )


@router.post("/reset-password", status_code=204)
def reset_password(payload: ResetPasswordIn, db: Session = Depends(get_db)):
    """재설정 링크의 토큰으로 비밀번호를 교체한다. 토큰은 1회용·시한부이며,
    성공 시 token_version 을 올려 기존 세션을 전부 무효화한다."""
    token_hash = hash_reset_token(payload.token)
    row = db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    ).scalar_one_or_none()
    if not row or row.used_at is not None or row.expires_at < _now():
        raise HTTPException(status_code=400, detail="링크가 만료되었거나 이미 사용되었습니다. 다시 요청해 주세요")

    user = db.get(User, row.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail="사용할 수 없는 링크입니다")

    _check_password_policy(payload.new_password, user.username, field="new_password")

    user.hashed_password = hash_password(payload.new_password)
    user.token_version += 1
    user.must_change_password = False
    user.password_changed_at = _now()
    row.used_at = _now()
    record_audit(db, user, "UPDATE", "user", user.id, after={"password_reset": True})
    db.commit()


# ---------- 2단계 인증 (TOTP) ----------
@router.post("/2fa/setup", response_model=TotpSetupOut)
def totp_setup(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """2FA 설정 시작. 새 secret 을 발급해 저장하지만 아직 활성화하지 않는다
    (인증 앱 코드로 /2fa/enable 을 통과해야 켜진다 — 등록 실패로 계정이 잠기지 않게)."""
    if user.totp_enabled:
        raise HTTPException(status_code=409, detail="이미 2단계 인증이 켜져 있습니다")

    secret = pyotp.random_base32()
    user.totp_secret = secret
    db.commit()

    uri = pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name="ERP")
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
    qr_svg = img.to_string(encoding="unicode")
    return TotpSetupOut(secret=secret, otpauth_uri=uri, qr_svg=qr_svg)


@router.post("/2fa/enable", status_code=204)
def totp_enable(
    payload: TotpCodeIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """인증 앱이 만든 코드가 맞아야 2FA 를 켠다."""
    if user.totp_enabled:
        raise HTTPException(status_code=409, detail="이미 2단계 인증이 켜져 있습니다")
    if not user.totp_secret:
        raise HTTPException(status_code=400, detail="먼저 2단계 인증 설정을 시작해 주세요")
    if not pyotp.TOTP(user.totp_secret).verify(payload.code.strip(), valid_window=1):
        raise FieldError("code", "코드가 올바르지 않습니다. 시간을 확인하고 다시 입력해 주세요")

    user.totp_enabled = True
    record_audit(db, user, "UPDATE", "user", user.id, after={"totp_enabled": True})
    db.commit()


@router.post("/2fa/disable", status_code=204)
def totp_disable(
    payload: TotpDisableIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """본인 2FA 해제 — 세션 탈취만으로 꺼지지 않도록 비밀번호를 다시 확인한다.
    (인증 앱을 잃어버려 로그인 자체가 안 되면 관리자가 /api/users/{id}/2fa/disable 로 푼다.)"""
    if not verify_password(payload.password, user.hashed_password):
        raise FieldError("password", "비밀번호가 올바르지 않습니다", status_code=400)

    user.totp_enabled = False
    user.totp_secret = ""
    record_audit(db, user, "UPDATE", "user", user.id, after={"totp_enabled": False})
    db.commit()


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user)):
    return _me_out(user)


def _me_out(user: User) -> MeOut:
    return MeOut(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        email=user.email or "",
        is_active=user.is_active,
        status=user.status,
        department=user.department,
        totp_enabled=user.totp_enabled,
        last_login_at=user.last_login_at,
        roles=user.roles,
        permissions=sorted(user_permissions(user)),
        must_change_password=user.must_change_password,
    )
