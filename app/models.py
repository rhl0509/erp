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
    # 이메일은 유니크(비번 재설정 대상 식별). 미입력은 빈 문자열이 아니라 NULL 로 둔다
    # — 빈 문자열은 여러 행이 가질 수 없기 때문(UNIQUE 는 NULL 중복만 허용).
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, default=None)
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # 가입 신청 정보(승인 판단용)
    department: Mapped[str] = mapped_column(String(100), default="")
    signup_reason: Mapped[str] = mapped_column(String(255), default="")
    # 거절: is_active=False 상태에서 rejected_at 이 있으면 '거절', 없으면 '승인 대기'
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reject_reason: Mapped[str] = mapped_column(String(255), default="")

    # 세션·비밀번호 수명
    # token_version: 발급 토큰에 심는 값. 비번 변경·재설정·임시비번 발급·비활성화 시
    # 증가시키면 그 이전에 발급된 JWT 는 전부 무효가 된다(무상태 JWT 의 강제 로그아웃).
    token_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 2단계 인증(TOTP). secret 은 설정 시작 시 저장하고, 코드 검증에 성공해야 enabled.
    totp_secret: Mapped[str] = mapped_column(String(64), default="")
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    roles: Mapped[list["Role"]] = relationship(
        secondary=user_roles, back_populates="users", lazy="selectin"
    )

    @property
    def status(self) -> str:
        """pending(승인 대기) / active(활성) / rejected(거절) — is_active + rejected_at 파생."""
        if self.is_active:
            return "active"
        return "rejected" if self.rejected_at else "pending"


class AccountingPeriod(Base, TimestampMixin):
    """회계기간(월 단위). 마감하면 그 달에 속하는 기표가 전면 차단된다.

    - period: 'YYYY-MM'. 행은 필요할 때 지연 생성한다(ensure_periods).
    - 마감 시 손익계정을 3110 이월이익잉여금으로 대체하는 CLOSING 전표를 만든다
      (JournalEntry.source_type='CLOSING', source_id=이 행의 id — UNIQUE 로 기간당 1건).
    - 마감 해제는 최신 마감 기간부터 역순으로만 가능하고, CLOSING 전표를 지운다.
    """

    __tablename__ = "accounting_periods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    period: Mapped[str] = mapped_column(String(7), unique=True, index=True)  # YYYY-MM
    status: Mapped[str] = mapped_column(String(10), default="open", server_default="open")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    closed_by_name: Mapped[str] = mapped_column(String(50), default="")
    # 마감 시점의 그 달 당기순이익(스냅샷 — 목록에서 재계산 없이 보여주기 위함)
    net_income: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=Decimal("0"), server_default="0"
    )


class PasswordResetToken(Base):
    """비밀번호 재설정 토큰. 원문은 메일로만 나가고 DB 에는 sha256 해시만 남긴다
    (DB 유출 시에도 토큰을 재사용할 수 없게). 사용 즉시 used_at 을 찍어 1회용으로 만든다."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


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


# ---------- 마스터: 창고 ----------
class Warehouse(Base, TimestampMixin):
    """재고를 보관하는 물리/논리 창고. 모든 재고 이동·잔고는 창고 차원을 갖는다.
    is_default=True 창고는 API가 창고를 지정하지 않은 기존(하위호환) 호출의 대상이 된다."""
    __tablename__ = "warehouses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
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
    # 이동이 발생한 창고. TRANSFER(창고간 이전)는 창고당 1행씩 페어(출고 음수/입고 양수)로 기록한다.
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), index=True)
    movement_type: Mapped[str] = mapped_column(String(10))  # IN / OUT / ADJUST / TRANSFER
    quantity: Mapped[int] = mapped_column(Integer)          # IN/OUT은 양수, ADJUST/TRANSFER는 부호 있는 증감량
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
    warehouse: Mapped["Warehouse"] = relationship(lazy="selectin")


# ---------- 재고: 품목·창고별 현재고 캐시(잔고) ----------
class StockBalance(Base):
    """모든 입출고가 이 잔고를 증분 갱신한다. 현재고를 매번 이동 합산하지 않고
    O(1) 조회하기 위한 캐시(원천은 stock_movements).
    창고별 이동평균 원가를 위해 PK는 (item_id, warehouse_id) 복합키다 —
    같은 품목이라도 창고마다 on_hand·avg_cost 를 독립적으로 가진다."""
    __tablename__ = "stock_balances"

    item_id: Mapped[int] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    warehouse_id: Mapped[int] = mapped_column(
        ForeignKey("warehouses.id"), primary_key=True
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


# ---------- 회계: 총계정원장(GL) ----------
# 설계: docs/gl-design.md — 전기형(B). 모든 전표는 원천 행(StockMovement/Payment)의
# 결정적 함수라 언제든 rebuild(app/gl.py)로 재생성 가능하다.
class Account(Base, TimestampMixin):
    """계정과목 마스터. 4자리 코드, 첫 자리=대분류. 시드 12계정 고정(§3) —
    사용자 편집 UI는 범위 밖이라 코드로만 관리한다."""
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    # asset(자산)/liability(부채)/equity(자본)/revenue(수익)/expense(비용)
    account_type: Mapped[str] = mapped_column(String(10))
    # 정상잔액 방향: debit(차변) / credit(대변) — 원장·시산표 잔액 부호 표시용
    normal_side: Mapped[str] = mapped_column(String(6))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class JournalEntry(Base, TimestampMixin):
    """분개 전표(헤더). (source_type, source_id) UNIQUE 로 원천 1행=전표 1건
    멱등성을 보장한다. 원천 삭제 시 전표 동반 삭제(§9-4), 자동 전기는 항상
    posted 단일 상태(draft 워크플로 범위 밖)."""
    __tablename__ = "journal_entries"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", name="uq_journal_entries_source"),
        Index("ix_journal_entries_entry_date", "entry_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)  # JV-YYYYMM-####
    entry_date: Mapped[str] = mapped_column(String(10), default="")   # YYYY-MM-DD (기존 관행)
    description: Mapped[str] = mapped_column(String(255), default="")
    source_type: Mapped[str] = mapped_column(String(10))   # MOVEMENT / PAYMENT / MANUAL
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # MANUAL 이면 NULL
    status: Mapped[str] = mapped_column(String(10), default="posted", server_default="posted")

    lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="entry", lazy="selectin",
        cascade="all, delete-orphan", order_by="JournalLine.line_no",
    )


class JournalLine(Base):
    """분개 라인. 차/대 분리 컬럼(표준 관행), 금액은 NUMERIC(18,4) —
    COGS(qty×이동평균)가 Decimal 이라 StockMovement.cost 정밀도 정책과 맞춘다.
    차대 합 일치(Σdebit==Σcredit)·0원 라인 금지는 서비스(app/gl.py)가 강제한다."""
    __tablename__ = "journal_lines"
    __table_args__ = (
        Index("ix_journal_lines_account_entry", "account_id", "entry_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    debit: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"), server_default="0")
    credit: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"), server_default="0")
    # AR/AP 라인의 보조원장 차원(거래처별 잔액·aging 대사용)
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"), nullable=True)
    memo: Mapped[str] = mapped_column(String(255), default="")

    entry: Mapped["JournalEntry"] = relationship(back_populates="lines")
    account: Mapped["Account"] = relationship(lazy="selectin")


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
