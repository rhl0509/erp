from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Session

from .config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,   # 끊긴 커넥션 자동 감지
    pool_recycle=3600,
    echo=False,
)

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
