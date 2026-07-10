from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Session

from .config import settings

# MySQL 기본 격리수준(REPEATABLE READ)에서는 트랜잭션 첫 읽기에 고정된 스냅샷 때문에
# FOR UPDATE 로 락을 잡아 직렬화해도 자식 행(received_qty 등)을 과거 값으로 읽는다.
# READ COMMITTED 로 두면 각 문(statement)이 최신 커밋본을 읽어, 락(직렬화)+최신읽기로
# 입고/출고/결제의 lost update 가 정확히 막힌다. (SQLite 는 이 옵션을 지원하지 않아 제외)
_engine_kwargs = dict(pool_pre_ping=True, pool_recycle=3600, echo=False)
if not settings.database_url.startswith("sqlite"):
    _engine_kwargs["isolation_level"] = "READ COMMITTED"

engine = create_engine(settings.database_url, **_engine_kwargs)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 의존성: 요청당 세션 1개를 열고 닫는다."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
