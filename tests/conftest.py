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
from fastapi.testclient import TestClient

from app.database import Base, engine
from app import seed as seed_module
from app.main import app


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    seed_module.run()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin(client):
    """admin 인증 헤더."""
    r = client.post("/api/auth/login", data={"username": "admin", "password": "admin1234"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
