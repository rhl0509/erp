from datetime import datetime

from sqlalchemy import (
    String, Integer, BigInteger, ForeignKey, Table, Column,
    DateTime, Text, Boolean, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


# ---------- RBAC 연결 테이블 ----------
user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# ---------- 인증 / 권한 ----------
class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(100), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    roles: Mapped[list["Role"]] = relationship(
        secondary=user_roles, back_populates="users", lazy="selectin"
    )


class Role(Base, TimestampMixin):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")

    users: Mapped[list["User"]] = relationship(secondary=user_roles, back_populates="roles")
    permissions: Mapped[list["Permission"]] = relationship(
        secondary=role_permissions, back_populates="roles", lazy="selectin"
    )


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(100), unique=True)  # 예: partner:read
    description: Mapped[str] = mapped_column(String(255), default="")

    roles: Mapped[list["Role"]] = relationship(
        secondary=role_permissions, back_populates="permissions"
    )


# ---------- 감사 로그 ----------
class AuditLog(Base):
    __tablename__ = "audit_logs"

    # SQLite는 BIGINT PK 자동증가를 못 하므로 sqlite에서만 INTEGER로 매핑(로컬 확인용).
    # MySQL 등 운영 DB에서는 그대로 BIGINT.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True, autoincrement=True,
    )
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    username: Mapped[str] = mapped_column(String(50), default="")
    action: Mapped[str] = mapped_column(String(20))          # CREATE / UPDATE / DELETE
    entity: Mapped[str] = mapped_column(String(50))          # partner / item / user ...
    entity_id: Mapped[str] = mapped_column(String(50), default="")
    before: Mapped[str | None] = mapped_column(Text, nullable=True)
    after: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ---------- 채번(번호 시퀀스) ----------
class NumberSequence(Base):
    __tablename__ = "number_sequences"
    __table_args__ = (UniqueConstraint("seq_key", "period", name="uq_seq_key_period"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seq_key: Mapped[str] = mapped_column(String(50))         # PARTNER / ITEM / PO ...
    prefix: Mapped[str] = mapped_column(String(20), default="")
    period: Mapped[str] = mapped_column(String(10), default="")  # 예: 202506 (기간별 채번 시)
    last_seq: Mapped[int] = mapped_column(Integer, default=0)


# ---------- 마스터: 거래처 ----------
class Partner(Base, TimestampMixin):
    __tablename__ = "partners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    partner_type: Mapped[str] = mapped_column(String(20), default="customer")  # customer/supplier/both
    business_no: Mapped[str] = mapped_column(String(20), default="")           # 사업자등록번호
    ceo_name: Mapped[str] = mapped_column(String(50), default="")
    phone: Mapped[str] = mapped_column(String(30), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    address: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ---------- 마스터: 품목 ----------
class Item(Base, TimestampMixin):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    spec: Mapped[str] = mapped_column(String(100), default="")     # 규격
    unit: Mapped[str] = mapped_column(String(20), default="EA")    # 단위
    item_type: Mapped[str] = mapped_column(String(20), default="product")  # product/material/service
    purchase_price: Mapped[int] = mapped_column(Integer, default=0)
    sales_price: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
