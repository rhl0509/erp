"""Alembic 마이그레이션 환경.

DB URL 과 모델 메타데이터를 앱 설정에서 직접 읽어오므로,
alembic.ini 에 접속정보를 중복 기입할 필요가 없다.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# 앱의 설정/메타데이터 재사용
from app.config import settings
from app.database import Base
from app import models  # noqa: F401  (모델 등록: 테이블 자동 인식에 필요)

config = context.config

# .env 등에서 읽은 실제 접속 URL 을 주입
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """오프라인(SQL 스크립트만 출력) 모드."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """온라인(실 DB 연결) 모드."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # SQLite 에서도 ALTER 가 필요한 변경을 처리할 수 있게 배치 모드 사용
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
