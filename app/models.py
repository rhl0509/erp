from datetime import datetime

from decimal import Decimal

from sqlalchemy import (
    String, Integer, BigInteger, Numeric, ForeignKey, Table, Column,
    DateTime, Text, Boolean, UniqueConstraint, Index, func,
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
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


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
    # 여신한도(0=무제한). 매출 출고 시 미수 잔액+이번 출고가 한도를 넘으면 막는다.
    credit_limit: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
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
    # 부가세 구분: taxable(과세 10%) / exempt(면세). 단가는 공급가액(세금 별도) 기준.
    tax_type: Mapped[str] = mapped_column(String(10), default="taxable", server_default="taxable")
    purchase_price: Mapped[int] = mapped_column(Integer, default=0)
    sales_price: Mapped[int] = mapped_column(Integer, default=0)
    safety_stock: Mapped[int] = mapped_column(Integer, default=0)  # 0=임계값 미설정(알림 제외)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ---------- 재고: 입출고 이력 ----------
class StockMovement(Base, TimestampMixin):
    __tablename__ = "stock_movements"
    # 통계/리포트가 기간·거래처로 자주 필터·정렬하므로 인덱스를 둔다.
    __table_args__ = (
        Index("ix_stock_movements_created_at", "created_at"),
        Index("ix_stock_movements_partner_id", "partner_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    movement_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    movement_type: Mapped[str] = mapped_column(String(10))  # IN / OUT / ADJUST
    quantity: Mapped[int] = mapped_column(Integer)          # IN/OUT은 양수, ADJUST는 부호 있는 증감량
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"), nullable=True)
    unit_price: Mapped[int] = mapped_column(Integer, default=0)
    # 원가: IN=매입단가, OUT/ADJUST=출고 시점 이동평균 원가(COGS 계산용).
    # 회계 정밀도를 위해 DECIMAL(18,4). (단정도 FLOAT는 큰 금액에서 원 단위 오차)
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"), server_default="0")
    # 부가세액(원 단위). 이동 시점 품목 과세구분으로 스냅샷. AP/AR(청구)은 공급가액+세액.
    # (재고평가·COGS 는 공급가액=cost 기준이므로 세액 불포함)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"), server_default="0")
    note: Mapped[str] = mapped_column(String(255), default="")
    # 이동 출처 추적: MANUAL(수기) / PO(발주 입고) / SO(수주 출고)
    ref_type: Mapped[str] = mapped_column(String(10), server_default="MANUAL", default="MANUAL")
    ref_line_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    item: Mapped["Item"] = relationship(lazy="selectin")
    partner: Mapped["Partner | None"] = relationship(lazy="selectin")


# ---------- 재고: 품목별 현재고 캐시(잔고) ----------
class StockBalance(Base):
    """모든 입출고가 이 잔고를 증분 갱신한다. 현재고를 매번 이동 합산하지 않고
    O(1) 조회하기 위한 캐시(원천은 stock_movements)."""
    __tablename__ = "stock_balances"

    item_id: Mapped[int] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    on_hand: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # 이동평균 원가(가중평균 매입단가). 재고평가액 = on_hand * avg_cost
    # DECIMAL(18,4)로 저장해 반복 갱신에도 원 단위 오차가 누적되지 않게 한다.
    avg_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"), server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# ---------- 거래 문서: 발주(구매) ----------
# 상태: draft(작성) → confirmed(확정) → partial(부분입고) → received(입고완료) → closed / cancelled
class PurchaseOrder(Base, TimestampMixin):
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    po_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"))  # 공급처
    status: Mapped[str] = mapped_column(String(15), default="draft", server_default="draft")
    order_date: Mapped[str] = mapped_column(String(10), default="")     # YYYY-MM-DD
    note: Mapped[str] = mapped_column(String(255), default="")
    total_amount: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    partner: Mapped["Partner"] = relationship(lazy="selectin")
    lines: Mapped[list["PurchaseOrderLine"]] = relationship(
        back_populates="order", lazy="selectin",
        cascade="all, delete-orphan", order_by="PurchaseOrderLine.id",
    )


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[int] = mapped_column(Integer, default=0)
    received_qty: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    returned_qty: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # 매입반품 누적

    order: Mapped["PurchaseOrder"] = relationship(back_populates="lines")
    item: Mapped["Item"] = relationship(lazy="selectin")


# ---------- 거래 문서: 수주(판매) ----------
# 상태: draft → confirmed → partial(부분출고) → shipped(출고완료) → closed / cancelled
class SalesOrder(Base, TimestampMixin):
    __tablename__ = "sales_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    so_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"))  # 고객
    status: Mapped[str] = mapped_column(String(15), default="draft", server_default="draft")
    order_date: Mapped[str] = mapped_column(String(10), default="")
    note: Mapped[str] = mapped_column(String(255), default="")
    total_amount: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    partner: Mapped["Partner"] = relationship(lazy="selectin")
    lines: Mapped[list["SalesOrderLine"]] = relationship(
        back_populates="order", lazy="selectin",
        cascade="all, delete-orphan", order_by="SalesOrderLine.id",
    )


class SalesOrderLine(Base):
    __tablename__ = "sales_order_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    so_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[int] = mapped_column(Integer, default=0)
    shipped_qty: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    returned_qty: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # 매출반품 누적

    order: Mapped["SalesOrder"] = relationship(back_populates="lines")
    item: Mapped["Item"] = relationship(lazy="selectin")


# ---------- 세금계산서 (매입/매출) ----------
# 발행 시점의 거래 내용을 스냅샷한 세무 문서. 발주(PO)→매입, 수주(SO)→매출.
# 원장 성격이라 발행 후에는 원본을 바꾸지 않고 취소(cancelled)만 한다.
class TaxInvoice(Base, TimestampMixin):
    __tablename__ = "tax_invoices"
    __table_args__ = (Index("ix_tax_invoices_partner_id", "partner_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(10))        # sales(매출) / purchase(매입)
    ref_type: Mapped[str] = mapped_column(String(2), default="")   # PO / SO (원천 문서)
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"))  # 거래 상대
    partner_name: Mapped[str] = mapped_column(String(100), default="")       # 스냅샷
    partner_business_no: Mapped[str] = mapped_column(String(20), default="")  # 스냅샷
    issue_date: Mapped[str] = mapped_column(String(10), default="")   # YYYY-MM-DD
    supply_amount: Mapped[int] = mapped_column(Integer, default=0)     # 공급가액 합계
    tax_amount: Mapped[int] = mapped_column(Integer, default=0)        # 세액 합계
    total_amount: Mapped[int] = mapped_column(Integer, default=0)      # 합계금액(공급+세액)
    status: Mapped[str] = mapped_column(String(10), default="issued", server_default="issued")  # issued/cancelled
    note: Mapped[str] = mapped_column(String(255), default="")

    partner: Mapped["Partner"] = relationship(lazy="selectin")
    lines: Mapped[list["TaxInvoiceLine"]] = relationship(
        back_populates="invoice", lazy="selectin",
        cascade="all, delete-orphan", order_by="TaxInvoiceLine.id",
    )


class TaxInvoiceLine(Base):
    __tablename__ = "tax_invoice_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("tax_invoices.id", ondelete="CASCADE"), index=True)
    item_name: Mapped[str] = mapped_column(String(100), default="")   # 스냅샷(품목 변경·삭제와 무관)
    spec: Mapped[str] = mapped_column(String(100), default="")
    qty: Mapped[int] = mapped_column(Integer, default=0)
    unit_price: Mapped[int] = mapped_column(Integer, default=0)
    supply_amount: Mapped[int] = mapped_column(Integer, default=0)
    tax_amount: Mapped[int] = mapped_column(Integer, default=0)

    invoice: Mapped["TaxInvoice"] = relationship(back_populates="lines")


# ---------- 회계: 결제/수금 (미지급·미수 상계) ----------
class Payment(Base, TimestampMixin):
    """AP=공급처에 지급(미지급 차감), AR=고객에게서 수금(미수 차감).
    청구액(매입/매출)은 stock_movements 에서 파생하고, 이 표는 결제 실적만 기록한다."""
    __tablename__ = "payments"
    __table_args__ = (Index("ix_payments_partner_id", "partner_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payment_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"))
    kind: Mapped[str] = mapped_column(String(2))       # AP(지급) / AR(수금)
    amount: Mapped[int] = mapped_column(Integer)
    pay_date: Mapped[str] = mapped_column(String(10), default="")   # YYYY-MM-DD
    method: Mapped[str] = mapped_column(String(20), default="")     # 계좌이체/현금/카드/기타
    note: Mapped[str] = mapped_column(String(255), default="")
    # 특정 문서에 귀속(선택): ""=거래처 일반, PO=발주, SO=수주
    ref_type: Mapped[str] = mapped_column(String(2), default="", server_default="")
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    partner: Mapped["Partner"] = relationship(lazy="selectin")
