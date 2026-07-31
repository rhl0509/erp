"""인증 디벨롭(2026-07): 비밀번호 정책 · 강제 변경 게이트 · 세션 무효화 ·
가입 승인/거절 · 비밀번호 재설정(메일/임시비번) · 2단계 인증 · 프로필.

기존 test_api.py 가 '로그인이 되는가'를 본다면 여기서는 '로그인·가입의 규칙이 지켜지는가'를 본다.
"""
import pyotp
import pytest
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import AuditLog, User

from conftest import clear_must_change   # tests/ 는 패키지가 아니라 pytest 가 sys.path 에 넣는다

GOOD_PW = "Namu2026Erp"          # 정책 통과(10자↑, 영문+숫자, 아이디 미포함)
NEW_PW = "Sonamu2026Erp"


def _ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text)
    return r.json() if r.content else None


def _roles(client, admin):
    return {r["name"]: r["id"] for r in _ok(client.get("/api/roles", headers=admin))}


def _register(client, username="newbie", password=GOOD_PW, **extra):
    body = {"username": username, "password": password, **extra}
    return client.post("/api/auth/register", json=body)


def _login(client, username, password, otp=None):
    data = {"username": username, "password": password}
    if otp is not None:
        data["otp"] = otp
    r = client.post("/api/auth/login", data=data)
    client.cookies.clear()   # 헤더 인증만 검사(쿠키 인증은 test_api 에서 별도 확인)
    return r


# ---------- 비밀번호 정책 ----------
@pytest.mark.parametrize("pw,why", [
    ("Ab1", "너무 짧음"),
    ("abcdefghijkl", "숫자 없음"),
    ("123456789012", "영문 없음"),
    ("admin1234", "흔한 비밀번호"),
    ("newbie12345", "아이디 포함"),
    ("Namu 2026 Erp", "공백 포함"),
])
def test_register_rejects_weak_password(client, pw, why):
    r = _register(client, password=pw)
    assert r.status_code == 422, why
    assert "password" in r.json()["fields"]     # 화면이 입력칸 옆에 붙일 수 있는 형식


def test_password_policy_endpoint(client):
    body = _ok(client.get("/api/auth/password-policy"))
    assert body["min_length"] == settings.password_min_length
    assert str(settings.password_min_length) in body["text"]


def test_admin_created_user_also_obeys_policy(client, admin):
    r = client.post("/api/users", headers=admin,
                    json={"username": "weak", "password": "short1"})
    assert r.status_code == 422
    assert "password" in r.json()["fields"]


# ---------- 아이디/이메일 중복 ----------
def test_check_username_availability(client):
    assert _ok(client.get("/api/auth/check-username", params={"username": "admin"}))["available"] is False
    assert _ok(client.get("/api/auth/check-username", params={"username": "nobody"}))["available"] is True


def test_duplicate_email_rejected(client):
    _ok(_register(client, "user_a", email="dup@example.com"), 201)
    r = _register(client, "user_b", email="DUP@example.com")   # 대소문자 무시(정규화)
    assert r.status_code == 409
    assert "email" in r.json()["fields"]


def test_invalid_email_rejected(client):
    r = _register(client, "bademail", email="not-an-email")
    assert r.status_code == 422
    assert "email" in r.json()["fields"]


# ---------- 가입 → 승인 / 거절 ----------
def test_pending_user_cannot_login_until_approved(client, admin):
    user = _ok(_register(client, "pending1", department="영업팀", signup_reason="입사"), 201)
    assert user["status"] == "pending"
    assert user["department"] == "영업팀"

    r = _login(client, "pending1", GOOD_PW)
    assert r.status_code == 403
    assert "승인" in r.json()["detail"]

    # 승인 = is_active + 역할 부여
    staff = _roles(client, admin)["staff"]
    _ok(client.put(f"/api/users/{user['id']}", headers=admin,
                   json={"is_active": True, "role_ids": [staff]}))
    token = _ok(_login(client, "pending1", GOOD_PW))["access_token"]
    me = _ok(client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}))
    assert me["status"] == "active" and "partner:read" in me["permissions"]


def test_reject_user(client, admin):
    user = _ok(_register(client, "reject1"), 201)
    assert _ok(client.get("/api/users/pending/count", headers=admin))["count"] == 1

    rejected = _ok(client.post(f"/api/users/{user['id']}/reject", headers=admin,
                               json={"reason": "부서 확인 불가"}))
    assert rejected["status"] == "rejected"
    # 거절된 계정은 승인 대기 배지에서 빠진다
    assert _ok(client.get("/api/users/pending/count", headers=admin))["count"] == 0
    assert _ok(client.get("/api/users", headers=admin, params={"status": "rejected"}))["total"] == 1

    r = _login(client, "reject1", GOOD_PW)
    assert r.status_code == 403
    assert "거절" in r.json()["detail"] and "부서 확인 불가" in r.json()["detail"]


# ---------- 비밀번호 강제 변경 게이트 ----------
def test_seeded_admin_must_change_weak_password(client):
    """시드 admin(admin1234)은 정책 미달이라 로그인은 되지만 업무 API 가 막힌다."""
    body = _ok(_login(client, "admin", "admin1234"))
    assert body["must_change_password"] is True
    h = {"Authorization": f"Bearer {body['access_token']}"}

    assert _ok(client.get("/api/auth/me", headers=h))["must_change_password"] is True
    assert client.get("/api/partners", headers=h).status_code == 403   # 업무 API 차단

    _ok(client.put("/api/auth/me/password", headers=h,
                   json={"current_password": "admin1234", "new_password": NEW_PW}), 204)

    # 변경 후 새 토큰으로는 업무 API 가 열린다
    h2 = {"Authorization": f"Bearer {_ok(_login(client, 'admin', NEW_PW))['access_token']}"}
    assert client.get("/api/partners", headers=h2).status_code == 200


def test_password_change_invalidates_existing_sessions(client):
    """비밀번호를 바꾸면 그 전에 발급된 토큰(다른 기기 세션)은 즉시 무효가 된다."""
    clear_must_change()
    old = f"Bearer {_ok(_login(client, 'admin', 'admin1234'))['access_token']}"
    old_h = {"Authorization": old}
    assert client.get("/api/auth/me", headers=old_h).status_code == 200

    _ok(client.put("/api/auth/me/password", headers=old_h,
                   json={"current_password": "admin1234", "new_password": NEW_PW}), 204)
    assert client.get("/api/auth/me", headers=old_h).status_code == 401   # 옛 토큰 폐기


def test_password_change_rejects_weak_and_same(client):
    clear_must_change()
    h = {"Authorization": f"Bearer {_ok(_login(client, 'admin', 'admin1234'))['access_token']}"}
    r = client.put("/api/auth/me/password", headers=h,
                   json={"current_password": "admin1234", "new_password": "admin1234"})
    assert r.status_code == 400
    r = client.put("/api/auth/me/password", headers=h,
                   json={"current_password": "admin1234", "new_password": "weak"})
    assert r.status_code == 422 and "new_password" in r.json()["fields"]


def test_deactivating_user_kills_session(client, admin):
    user = _ok(_register(client, "victim"), 201)
    staff = _roles(client, admin)["staff"]
    _ok(client.put(f"/api/users/{user['id']}", headers=admin,
                   json={"is_active": True, "role_ids": [staff]}))
    h = {"Authorization": f"Bearer {_ok(_login(client, 'victim', GOOD_PW))['access_token']}"}
    assert client.get("/api/partners", headers=h).status_code == 200

    _ok(client.put(f"/api/users/{user['id']}", headers=admin, json={"is_active": False}))
    assert client.get("/api/partners", headers=h).status_code == 401


def test_refresh_extends_session(client, admin):
    body = _ok(client.post("/api/auth/refresh", headers=admin))
    assert body["access_token"]
    h = {"Authorization": f"Bearer {body['access_token']}"}
    assert client.get("/api/partners", headers=h).status_code == 200


# ---------- 임시 비밀번호(관리자 발급) ----------
def test_admin_temp_password_flow(client, admin):
    user = _ok(_register(client, "forgetful"), 201)
    staff = _roles(client, admin)["staff"]
    _ok(client.put(f"/api/users/{user['id']}", headers=admin,
                   json={"is_active": True, "role_ids": [staff]}))
    old_h = {"Authorization": f"Bearer {_ok(_login(client, 'forgetful', GOOD_PW))['access_token']}"}

    issued = _ok(client.post(f"/api/users/{user['id']}/temp-password", headers=admin))
    temp = issued["temp_password"]

    assert client.get("/api/partners", headers=old_h).status_code == 401   # 기존 세션 폐기
    assert _login(client, "forgetful", GOOD_PW).status_code == 401         # 옛 비번 폐기

    body = _ok(_login(client, "forgetful", temp))
    assert body["must_change_password"] is True
    h = {"Authorization": f"Bearer {body['access_token']}"}
    assert client.get("/api/partners", headers=h).status_code == 403       # 변경 전엔 업무 차단

    _ok(client.put("/api/auth/me/password", headers=h,
                   json={"current_password": temp, "new_password": NEW_PW}), 204)
    h2 = {"Authorization": f"Bearer {_ok(_login(client, 'forgetful', NEW_PW))['access_token']}"}
    assert client.get("/api/partners", headers=h2).status_code == 200


# ---------- 비밀번호 찾기 / 재설정 ----------
def test_forgot_password_without_smtp_points_to_admin(client):
    """SMTP 미설정이면 메일을 보내지 않고 관리자 발급 경로를 안내한다."""
    body = _ok(client.post("/api/auth/forgot-password", json={"identifier": "admin"}))
    assert body["delivery"] == "admin"


def test_forgot_password_does_not_leak_account_existence(client, monkeypatch):
    """계정이 있든 없든 응답이 같아야 한다(계정 열거 방지)."""
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr("app.api.auth.send_mail", lambda *a, **k: True)

    a = _ok(client.post("/api/auth/forgot-password", json={"identifier": "admin"}))
    b = _ok(client.post("/api/auth/forgot-password", json={"identifier": "ghost"}))
    assert a == b and a["delivery"] == "email"


def test_reset_password_with_emailed_link(client, admin, monkeypatch):
    sent: dict = {}
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr("app.api.auth.send_mail",
                        lambda to, subject, body: sent.update(to=to, body=body) or True)

    user = _ok(_register(client, "lostpw", email="lost@example.com"), 201)
    staff = _roles(client, admin)["staff"]
    _ok(client.put(f"/api/users/{user['id']}", headers=admin,
                   json={"is_active": True, "role_ids": [staff]}))

    _ok(client.post("/api/auth/forgot-password", json={"identifier": "lost@example.com"}))
    assert sent["to"] == "lost@example.com"
    token = sent["body"].split("token=")[1].split()[0]

    _ok(client.post("/api/auth/reset-password",
                    json={"token": token, "new_password": NEW_PW}), 204)
    assert _ok(_login(client, "lostpw", NEW_PW))["must_change_password"] is False
    assert _login(client, "lostpw", GOOD_PW).status_code == 401     # 옛 비번 폐기

    # 1회용 — 같은 토큰 재사용 불가
    r = client.post("/api/auth/reset-password", json={"token": token, "new_password": "Another2026Pw"})
    assert r.status_code == 400


def test_reset_password_rejects_bogus_token(client):
    r = client.post("/api/auth/reset-password",
                    json={"token": "not-a-real-token-value", "new_password": NEW_PW})
    assert r.status_code == 400


# ---------- 2단계 인증(TOTP) ----------
def _enable_2fa(client, headers) -> str:
    setup = _ok(client.post("/api/auth/2fa/setup", headers=headers))
    assert setup["otpauth_uri"].startswith("otpauth://totp/") and "<svg" in setup["qr_svg"]
    secret = setup["secret"]
    _ok(client.post("/api/auth/2fa/enable", headers=headers,
                    json={"code": pyotp.TOTP(secret).now()}), 204)
    return secret


def test_totp_setup_enable_and_login(client):
    clear_must_change()
    h = {"Authorization": f"Bearer {_ok(_login(client, 'admin', 'admin1234'))['access_token']}"}
    secret = _enable_2fa(client, h)
    assert _ok(client.get("/api/auth/me", headers=h))["totp_enabled"] is True

    # 코드 없이 로그인 → OTP 필요 표식
    r = _login(client, "admin", "admin1234")
    assert r.status_code == 401 and r.headers.get("X-OTP-Required") == "1"

    # 틀린 코드 → 거부
    assert _login(client, "admin", "admin1234", otp="000000").status_code == 401

    # 올바른 코드 → 통과
    assert _ok(_login(client, "admin", "admin1234", otp=pyotp.TOTP(secret).now()))["access_token"]


def test_totp_setup_records_audit_without_leaking_secret(client):
    """secret 발급도 계정 상태 변경이라 흔적이 남아야 한다 — 다만 secret 값 자체는 남기지 않는다."""
    clear_must_change()
    h = {"Authorization": f"Bearer {_ok(_login(client, 'admin', 'admin1234'))['access_token']}"}
    setup = _ok(client.post("/api/auth/2fa/setup", headers=h))

    db = SessionLocal()
    try:
        log = db.execute(
            select(AuditLog).where(AuditLog.entity == "user").order_by(AuditLog.id.desc())
        ).scalars().first()
        assert log is not None
        assert (log.action, log.username) == ("UPDATE", "admin")
        assert "totp_secret_issued" in (log.after or "")
        assert setup["secret"] not in (log.after or "")
    finally:
        db.close()


def test_totp_enable_rejects_wrong_code(client):
    clear_must_change()
    h = {"Authorization": f"Bearer {_ok(_login(client, 'admin', 'admin1234'))['access_token']}"}
    _ok(client.post("/api/auth/2fa/setup", headers=h))
    r = client.post("/api/auth/2fa/enable", headers=h, json={"code": "000000"})
    assert r.status_code == 422 and "code" in r.json()["fields"]
    assert _ok(client.get("/api/auth/me", headers=h))["totp_enabled"] is False


def test_totp_self_disable_requires_password(client):
    clear_must_change()
    h = {"Authorization": f"Bearer {_ok(_login(client, 'admin', 'admin1234'))['access_token']}"}
    _enable_2fa(client, h)

    assert client.post("/api/auth/2fa/disable", headers=h,
                       json={"password": "wrong-password"}).status_code == 400
    _ok(client.post("/api/auth/2fa/disable", headers=h, json={"password": "admin1234"}), 204)
    assert _ok(client.get("/api/auth/me", headers=h))["totp_enabled"] is False


def test_admin_can_disable_totp_for_locked_out_user(client, admin):
    """인증 앱을 잃은 사용자의 구제 경로 — 관리자가 2FA 를 푼다."""
    user = _ok(_register(client, "lockedout"), 201)
    staff = _roles(client, admin)["staff"]
    _ok(client.put(f"/api/users/{user['id']}", headers=admin,
                   json={"is_active": True, "role_ids": [staff]}))
    h = {"Authorization": f"Bearer {_ok(_login(client, 'lockedout', GOOD_PW))['access_token']}"}
    _enable_2fa(client, h)
    assert _login(client, "lockedout", GOOD_PW).status_code == 401     # OTP 없이는 못 들어감

    _ok(client.post(f"/api/users/{user['id']}/2fa/disable", headers=admin))
    assert _ok(_login(client, "lockedout", GOOD_PW))["access_token"]   # 다시 로그인 가능


# ---------- 프로필 ----------
def test_update_my_profile(client, admin):
    me = _ok(client.put("/api/auth/me", headers=admin,
                        json={"full_name": "김관리", "email": "admin@example.com",
                              "department": "경영지원"}))
    assert me["full_name"] == "김관리" and me["email"] == "admin@example.com"
    assert _ok(client.get("/api/auth/me", headers=admin))["department"] == "경영지원"


def test_profile_email_must_be_unique(client, admin):
    _ok(_register(client, "other", email="taken@example.com"), 201)
    r = client.put("/api/auth/me", headers=admin, json={"email": "taken@example.com"})
    assert r.status_code == 409 and "email" in r.json()["fields"]


def test_last_login_recorded(client, admin):
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.username == "admin")).scalar_one()
        assert user.last_login_at is not None
    finally:
        db.close()
