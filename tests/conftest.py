"""pytest 공통 픽스처.

테스트는 임시 SQLite 파일 DB를 쓴다. app 임포트 전에 환경변수를 설정해야
설정 가드(JWT_SECRET)를 통과하고 엔진이 테스트 DB를 가리킨다.
각 테스트 시작 시 스키마를 초기화하고 시드해 서로 격리된다.
"""
import os
import tempfile

os.environ.setdefault("JWT_SECRET", "test-secret-long-enough-0123456789")
_DB_PATH = os.path.join(tempfile.gettempdir(), "erp_pytest.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH.replace(os.sep, "/")

import pytest
from sqlalchemy import select
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app import seed as seed_module
from app.main import app
from app.models import User


def reset_rate_limits() -> None:
    """인메모리 레이트 리미터(로그인·가입·비번찾기·중복확인)는 프로세스 전역이라
    테스트 사이에 카운터가 누적된다. 매 테스트 시작 시 비워 서로 격리한다
    (제한 동작 자체는 전용 테스트가 검증한다)."""
    from app.api import auth as auth_api

    for limiter in (auth_api._login_limiter, auth_api._register_limiter,
                    auth_api._forgot_limiter, auth_api._check_limiter):
        limiter._events.clear()


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    seed_module.run()
    reset_rate_limits()
    with TestClient(app) as c:
        yield c


def clear_must_change(username: str = "admin") -> None:
    """시드 admin 의 비밀번호(admin1234)는 새 정책에 미달이라 must_change_password 가 켜진
    채로 생성된다(운영 의도). 대부분의 테스트는 그 게이트가 아니라 업무 API 를 검사하므로
    여기서 플래그만 내려 준다 — 게이트 자체는 tests/test_auth.py 가 명시적으로 검증한다."""
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.username == username)).scalar_one()
        user.must_change_password = False
        db.commit()
    finally:
        db.close()


@pytest.fixture()
def admin(client):
    """admin 인증 헤더. 로그인이 심는 세션 쿠키는 비워, 이 픽스처를 쓰는 테스트가
    '헤더 인증'만 검사하도록 유지한다(쿠키 인증은 별도 테스트에서 명시적으로 확인)."""
    clear_must_change()
    r = client.post("/api/auth/login", data={"username": "admin", "password": "admin1234"})
    token = r.json()["access_token"]
    client.cookies.clear()
    return {"Authorization": f"Bearer {token}"}
