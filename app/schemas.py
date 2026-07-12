from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field, ConfigDict, model_validator

T = TypeVar("T")


# ---------- 공통 응답 ----------
class Page(BaseModel, Generic[T]):
    """목록 응답 공통 형식. 화면 페이지네이션에 바로 쓸 수 있다."""
    items: list[T]
    total: int      # 전체 건수
    page: int       # 현재 페이지(1부터)
    page_size: int  # 페이지당 건수
    pages: int      # 전체 페이지 수


class ErrorOut(BaseModel):
    """일관된 오류 응답 형식."""
    detail: str                       # 사람이 읽는 메시지(한글)
    code: str = "error"               # 클라이언트 분기용 코드
    fields: dict[str, str] | None = None  # 입력값 오류 시 필드별 메시지


# ---------- 인증 ----------
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------- 권한 / 역할 / 사용자 ----------
class PermissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    description: str = ""


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str = ""
    permissions: list[PermissionOut] = []


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    full_name: str = ""
    email: str = ""
    is_active: bool
    roles: list[RoleOut] = []


class MeOut(UserOut):
    """현재 로그인 사용자. 화면에서 버튼 노출 제어용 권한 목록 포함."""
    permissions: list[str] = []


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=50)
    password: str = Field(min_length=8)
    full_name: str = ""
    email: str = ""
    role_ids: list[int] = []


class RegisterCreate(BaseModel):
    """공개 회원가입 입력. 역할·활성 여부는 지정 불가(비활성·무권한으로 생성)."""
    username: str = Field(min_length=2, max_length=50)
    password: str = Field(min_length=8)
    full_name: str = ""
    email: str = ""


class UserUpdate(BaseModel):
    """관리자용 회원 수정(승인=역할 부여 + is_active=true)."""
    full_name: str | None = None
    email: str | None = None
    is_active: bool | None = None
    role_ids: list[int] | None = None


class PasswordChange(BaseModel):
    """본인 비밀번호 변경. 현재 비밀번호 확인 후 교체."""
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


# ---------- 감사 로그 ----------
class AuditLogOut(BaseModel):
    """변경 이력 1건. before/after는 저장된 JSON 문자열 그대로(화면에서 파싱)."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int | None = None
    username: str = ""
    action: str          # CREATE / UPDATE / DELETE
    entity: str          # user / partner / item / stock
    entity_id: str = ""
    before: str | None = None
    after: str | None = None
    created_at: datetime


# ---------- 거래처 ----------
class PartnerBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    partner_type: str = "customer"
    business_no: str = ""
    ceo_name: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    credit_limit: int = Field(default=0, ge=0)   # 여신한도(0=무제한)
    is_active: bool = True


class PartnerCreate(PartnerBase):
    pass


class PartnerUpdate(BaseModel):
    """부분 수정용. 보낸 필드만 반영한다(누락 필드는 기존 값 유지)."""
    name: str | None = Field(default=None, min_length=1, max_length=100)
    partner_type: str | None = None
    business_no: str | None = None
    ceo_name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    credit_limit: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class PartnerOut(PartnerBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    created_at: datetime


# ---------- 품목 ----------
class ItemBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    spec: str = ""
    unit: str = "EA"
    item_type: str = "product"
    tax_type: str = Field(default="taxable", pattern="^(taxable|exempt)$")  # 과세/면세
    purchase_price: int = 0
    sales_price: int = 0
    safety_stock: int = Field(default=0, ge=0)  # 0=임계값 미설정(알림 제외)
    is_active: bool = True


class ItemCreate(ItemBase):
    pass


class ItemUpdate(BaseModel):
    """부분 수정용. 보낸 필드만 반영한다(누락 필드는 기존 값 유지)."""
    name: str | None = Field(default=None, min_length=1, max_length=100)
    spec: str | None = None
    unit: str | None = None
    item_type: str | None = None
    tax_type: str | None = Field(default=None, pattern="^(taxable|exempt)$")
    purchase_price: int | None = None
    sales_price: int | None = None
    safety_stock: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class ItemOut(ItemBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    created_at: datetime


# ---------- 창고 ----------
class WarehouseCreate(BaseModel):
    code: str = Field(default="", max_length=30)   # 빈 값이면 서버가 채번(WH-0001)
    name: str = Field(min_length=1, max_length=100)
    is_active: bool = True


class WarehouseUpdate(BaseModel):
    """부분 수정용. 보낸 필드만 반영한다(누락 필드는 기존 값 유지)."""
    code: str | None = Field(default=None, min_length=1, max_length=30)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_default: bool | None = None
    is_active: bool | None = None


class WarehouseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    is_default: bool
    is_active: bool
    created_at: datetime


# ---------- 재고 ----------
class StockMovementCreate(BaseModel):
    item_id: int
    movement_type: str = Field(pattern="^(IN|OUT|ADJUST)$")   # 창고간 이전은 /stock/transfer 전용
    quantity: int
    partner_id: int | None = None
    warehouse_id: int | None = None            # 미지정 시 기본창고(하위호환)
    unit_price: int = Field(default=0, ge=0)   # 단가는 음수 불가(이동평균·매출 오염 방지)
    note: str = ""

    @model_validator(mode="after")
    def check_quantity(self):
        # IN/OUT은 양수 필수, ADJUST는 부호 있는 증감량(0은 불가)
        if self.quantity == 0:
            raise ValueError("수량은 0이 될 수 없습니다.")
        if self.movement_type in ("IN", "OUT") and self.quantity < 0:
            raise ValueError("입고/출고 수량은 양수여야 합니다.")
        return self


class StockMovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    movement_no: str
    item_id: int
    item_code: str
    item_name: str
    movement_type: str
    quantity: int
    partner_id: int | None = None
    partner_name: str = ""
    warehouse_id: int | None = None   # 이동이 발생한 창고
    warehouse_name: str = ""
    unit_price: int
    tax_amount: int = 0     # 부가세액(과세 품목의 IN/OUT). 공급가액 = quantity x unit_price
    note: str = ""
    created_at: datetime


class StockTransferCreate(BaseModel):
    """창고간 이전 요청. 출고창고의 이동평균 원가로 입고창고 평균을 갱신한다(원가 보존)."""
    item_id: int
    from_warehouse_id: int
    to_warehouse_id: int
    quantity: int = Field(ge=1)
    note: str = ""


class StockTransferOut(BaseModel):
    """이전 결과. TRANSFER 페어 2행(출고 음수/입고 양수)의 요약."""
    item_id: int
    from_warehouse_id: int
    to_warehouse_id: int
    quantity: int
    cost: float                 # 이전 단가 = 출고창고 이동평균 원가
    out_movement_no: str
    in_movement_no: str


class StockLevelOut(BaseModel):
    item_id: int
    item_code: str
    item_name: str
    unit: str
    item_type: str
    on_hand: int
    safety_stock: int = 0
    shortage: int = 0  # 안전재고 미달 부족량 = max(safety_stock - on_hand, 0)


# ---------- 거래처 매입/매출 내역 ----------
class PartnerTxnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    movement_no: str
    created_at: datetime
    item_id: int
    item_code: str
    item_name: str
    kind: str          # purchase(매입) / sales(매출)
    quantity: int
    unit_price: int
    amount: int        # 수량 x 단가


class PartnerLedgerSummary(BaseModel):
    purchase_count: int
    purchase_amount: int
    sales_count: int
    sales_amount: int


# ---------- 매입/매출 통계 ----------
class StatMoneyPair(BaseModel):
    purchase_amount: int   # 매입액
    sales_amount: int      # 매출액


class StatTotal(BaseModel):
    purchase_amount: int
    sales_amount: int
    purchase_count: int
    sales_count: int


class StatMonthly(BaseModel):
    period: str            # 예: "2026-07"
    purchase_amount: int
    sales_amount: int


class StatTopPartner(BaseModel):
    partner_id: int
    partner_name: str
    sales_amount: int


class SalesPurchaseOverview(BaseModel):
    total: StatTotal
    this_month: StatMoneyPair
    monthly: list[StatMonthly]
    top_partners: list[StatTopPartner]
    partner_subtotals: list["TransactionPartnerSubtotal"]  # 거래처별 소계 (전체 누계)
    item_subtotals: list["TransactionItemSubtotal"]  # 품목별 소계 (전체 누계)


# ---------- 매입/매출 내역 (전사 목록) ----------
class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    movement_no: str
    created_at: datetime
    kind: str          # purchase(매입) / sales(매출)
    partner_id: int | None = None
    partner_name: str = ""
    item_id: int
    item_code: str
    item_name: str
    quantity: int
    unit_price: int
    amount: int        # 수량 x 단가


class TransactionPeriod(BaseModel):
    period: str            # 월별="2026-07", 일별="2026-07-02"
    purchase_amount: int
    sales_amount: int
    purchase_count: int
    sales_count: int


class TransactionPartnerSubtotal(BaseModel):
    partner_id: int | None = None
    partner_name: str          # 거래처 없으면 "미지정"
    purchase_amount: int
    sales_amount: int
    purchase_count: int
    sales_count: int


class TransactionItemSubtotal(BaseModel):
    item_id: int
    item_code: str
    item_name: str
    purchase_amount: int
    sales_amount: int
    purchase_count: int
    sales_count: int


# ---------- 거래 문서: 발주(구매) / 수주(판매) ----------
class OrderLineIn(BaseModel):
    """발주/수주 명세 입력."""
    item_id: int
    qty: int = Field(ge=1)
    unit_price: int = Field(default=0, ge=0)


class DocQtyLineIn(BaseModel):
    """입고/출고 처리 시 라인별 수량."""
    line_id: int
    qty: int = Field(ge=1)


class ReceiveRequest(BaseModel):
    """발주 입고 요청(부분 입고 허용)."""
    lines: list[DocQtyLineIn] = Field(min_length=1)


class ShipRequest(BaseModel):
    """수주 출고 요청(부분 출고 허용)."""
    lines: list[DocQtyLineIn] = Field(min_length=1)


class ReturnRequest(BaseModel):
    """반품 요청. 라인별 반품 수량(입고/출고 실적 − 기존 반품 이내)."""
    lines: list[DocQtyLineIn] = Field(min_length=1)


class PurchaseOrderCreate(BaseModel):
    partner_id: int
    order_date: str = ""     # YYYY-MM-DD (미지정 시 서버가 오늘로 채움)
    note: str = ""
    lines: list[OrderLineIn] = []


class PurchaseOrderUpdate(BaseModel):
    """draft 상태에서만 수정 가능. lines 를 주면 명세 전체를 교체한다."""
    partner_id: int | None = None
    order_date: str | None = None
    note: str | None = None
    lines: list[OrderLineIn] | None = None


class PurchaseOrderLineOut(BaseModel):
    id: int
    item_id: int
    item_code: str
    item_name: str
    qty: int
    unit_price: int
    amount: int            # 공급가액 = qty x unit_price
    tax_amount: int = 0    # 부가세액(면세 품목은 0)
    received_qty: int
    remaining_qty: int     # qty - received_qty
    returned_qty: int = 0        # 매입반품 누적
    returnable_qty: int = 0      # 반품 가능 = received_qty - returned_qty


class PurchaseOrderOut(BaseModel):
    id: int
    po_no: str
    partner_id: int
    partner_name: str = ""
    status: str            # draft/confirmed/partial/received/cancelled
    order_date: str = ""
    note: str = ""
    total_amount: int           # 공급가액 합계 (Σ qty x unit_price)
    tax_amount: int = 0         # 부가세액 합계
    grand_total: int = 0        # 합계금액 = 공급가액 + 세액 (VAT 포함)
    billed_amount: int = 0      # 청구액(입고 실적, VAT 포함) — 채무 발생분
    paid_amount: int = 0        # 이 발주에 귀속된 지급(AP) 합
    outstanding: int = 0        # 미지급 = billed_amount - paid_amount (납품 기준, 거래처 잔액과 동일)
    created_at: datetime
    lines: list[PurchaseOrderLineOut] = []


class SalesOrderCreate(BaseModel):
    partner_id: int
    order_date: str = ""
    note: str = ""
    lines: list[OrderLineIn] = []


class SalesOrderUpdate(BaseModel):
    partner_id: int | None = None
    order_date: str | None = None
    note: str | None = None
    lines: list[OrderLineIn] | None = None


class SalesOrderLineOut(BaseModel):
    id: int
    item_id: int
    item_code: str
    item_name: str
    qty: int
    unit_price: int
    amount: int            # 공급가액 = qty x unit_price
    tax_amount: int = 0    # 부가세액(면세 품목은 0)
    shipped_qty: int
    remaining_qty: int     # qty - shipped_qty
    returned_qty: int = 0        # 매출반품 누적
    returnable_qty: int = 0      # 반품 가능 = shipped_qty - returned_qty


class SalesOrderOut(BaseModel):
    id: int
    so_no: str
    partner_id: int
    partner_name: str = ""
    status: str            # draft/confirmed/partial/shipped/cancelled
    order_date: str = ""
    note: str = ""
    total_amount: int           # 공급가액 합계 (Σ qty x unit_price)
    tax_amount: int = 0         # 부가세액 합계
    grand_total: int = 0        # 합계금액 = 공급가액 + 세액 (VAT 포함)
    billed_amount: int = 0      # 청구액(출고 실적, VAT 포함) — 채권 발생분
    paid_amount: int = 0        # 이 수주에 귀속된 수금(AR) 합
    outstanding: int = 0        # 미수 = billed_amount - paid_amount (납품 기준, 거래처 잔액과 동일)
    created_at: datetime
    lines: list[SalesOrderLineOut] = []


# ---------- 원가 / 재고평가 / 매출총이익 ----------
class StockValuationOut(BaseModel):
    item_id: int
    item_code: str
    item_name: str
    unit: str
    on_hand: int
    avg_cost: float        # 이동평균 원가
    value: int             # 재고평가액 = round(on_hand x avg_cost)


class ValuationSummary(BaseModel):
    total_value: int       # 전체 재고평가액
    item_count: int        # 재고 보유(on_hand>0) 품목 수


class MarginSummary(BaseModel):
    revenue: int           # 매출액 (출고 수량 x 판매단가)
    cogs: int              # 매출원가 (출고 수량 x 이동평균원가)
    gross_profit: int      # 매출총이익 = revenue - cogs
    margin_rate: float     # 매출총이익률 (gross_profit / revenue), 매출 0이면 0
    sales_count: int       # 출고 건수


# ---------- 회계: 결제/수금 (AR/AP) ----------
class PaymentCreate(BaseModel):
    partner_id: int
    kind: str = Field(pattern="^(AP|AR)$")   # AP=지급, AR=수금
    amount: int = Field(gt=0)
    pay_date: str = ""                        # YYYY-MM-DD (미지정 시 오늘)
    method: str = ""                          # 계좌이체/현금/카드/기타
    note: str = ""
    ref_type: str = Field(default="", pattern="^(|PO|SO)$")   # 문서 귀속(선택)
    ref_id: int | None = None


class PaymentOut(BaseModel):
    id: int
    payment_no: str
    partner_id: int
    partner_name: str = ""
    kind: str
    amount: int
    pay_date: str = ""
    method: str = ""
    note: str = ""
    ref_type: str = ""
    ref_id: int | None = None
    created_at: datetime


class PartnerBalanceOut(BaseModel):
    partner_id: int
    partner_name: str
    purchase_amount: int   # 매입 총액(청구, VAT 포함 = 공급가액 + 세액)
    purchase_tax: int      # 매입 부가세액
    ap_paid: int           # 지급 합
    ap_outstanding: int    # 미지급 = purchase_amount - ap_paid
    sales_amount: int      # 매출 총액(청구, VAT 포함 = 공급가액 + 세액)
    sales_tax: int         # 매출 부가세액
    ar_collected: int      # 수금 합
    ar_outstanding: int    # 미수 = sales_amount - ar_collected
    credit_limit: int      # 여신한도(0=무제한)
    credit_available: int  # 여신 가용 = credit_limit - ar_outstanding (한도 없으면 0)


# ---------- 여신 · Aging(연령분석) ----------
class AgingBucket(BaseModel):
    partner_id: int
    partner_name: str
    b_0_30: int            # 경과 0~30일
    b_31_60: int           # 31~60일
    b_61_90: int           # 61~90일
    b_over_90: int         # 90일 초과
    total: int             # 미결제 잔액 합


class AgingReport(BaseModel):
    kind: str              # AR(미수)/AP(미지급)
    as_of: str             # 기준일 YYYY-MM-DD
    buckets: list[AgingBucket] = []
    total: int = 0         # 전체 미결제 합


# ---------- 세금계산서 ----------
class TaxInvoiceCreate(BaseModel):
    """발주(PO)/수주(SO)에서 세금계산서를 발행한다."""
    ref_type: str = Field(pattern="^(PO|SO)$")   # PO=매입계산서, SO=매출계산서
    ref_id: int
    issue_date: str = ""                          # YYYY-MM-DD (미지정 시 오늘)
    note: str = ""


class TaxInvoiceLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    item_name: str
    spec: str = ""
    qty: int
    unit_price: int
    supply_amount: int
    tax_amount: int


class TaxInvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    invoice_no: str
    kind: str              # sales / purchase
    ref_type: str = ""
    ref_id: int | None = None
    partner_id: int
    partner_name: str = ""
    partner_business_no: str = ""
    issue_date: str = ""
    supply_amount: int
    tax_amount: int
    total_amount: int
    status: str
    note: str = ""
    created_at: datetime
    lines: list[TaxInvoiceLineOut] = []


# ---------- 총계정원장(GL) — docs/gl-design.md §7 ----------
class JournalLineOut(BaseModel):
    id: int
    line_no: int
    account_code: str
    account_name: str
    debit: float
    credit: float
    partner_id: int | None = None
    partner_name: str = ""
    memo: str = ""


class JournalEntryOut(BaseModel):
    id: int
    entry_no: str
    entry_date: str
    description: str = ""
    source_type: str          # MOVEMENT / PAYMENT / MANUAL — 원천 문서 딥링크용
    source_id: int | None = None
    status: str = "posted"
    created_at: datetime
    lines: list[JournalLineOut] = []


class LedgerLineOut(BaseModel):
    """계정별원장 1행(전표 라인 + 러닝밸런스)."""
    entry_id: int
    entry_no: str
    entry_date: str
    description: str = ""
    source_type: str
    source_id: int | None = None
    debit: float
    credit: float
    balance: float            # 정상잔액 방향 기준 러닝밸런스


class LedgerOut(BaseModel):
    account_code: str
    account_name: str
    account_type: str
    normal_side: str          # debit/credit — balance 부호 해석 기준
    date_from: str | None = None
    date_to: str | None = None
    opening_balance: float    # 기초잔액(date_from 이전 누계, 정상잔액 방향)
    total_debit: float        # 기간 차변 합
    total_credit: float       # 기간 대변 합
    closing_balance: float    # 기말잔액 = 기초 + 기간 증감
    lines: list[LedgerLineOut] = []


class TrialBalanceRow(BaseModel):
    account_code: str
    account_name: str
    account_type: str
    normal_side: str
    debit_total: float
    credit_total: float
    balance: float            # 정상잔액 방향 기준(차변계정=차−대, 대변계정=대−차)


class TrialBalanceOut(BaseModel):
    date_from: str | None = None
    date_to: str | None = None
    rows: list[TrialBalanceRow] = []
    total_debit: float
    total_credit: float
    balanced: bool            # Σ차변 == Σ대변


class ReconcileItem(BaseModel):
    """GL 총계 vs 기존 보조원장(원천 파생값) 대조 1항목."""
    gl: float
    expected: float
    diff: float
    ok: bool


class ReconcileTrial(BaseModel):
    debit: float
    credit: float
    ok: bool


class ReconcileOut(BaseModel):
    inventory: ReconcileItem   # 1130 vs Σ on_hand×avg_cost (valuation)
    ar: ReconcileItem          # 1120 vs 매출청구 − 수금
    ap: ReconcileItem          # 2110 vs 매입청구 − 지급
    trial: ReconcileTrial      # 시산표 차대 균형
    ok: bool


class GLRebuildOut(BaseModel):
    movement_entries: int
    payment_entries: int
    total_entries: int
    reconcile: ReconcileOut


# ---------- S6: 수기 전표(MANUAL) / 간이 재무제표 ----------
class ManualLineIn(BaseModel):
    """수기 전표 1라인. debit·credit 중 하나만 양수(0원 라인은 서버가 제거)."""
    account_code: str = Field(..., description="계정코드 (예: 3110)")
    debit: float = Field(0, ge=0)
    credit: float = Field(0, ge=0)
    partner_id: int | None = None
    memo: str = ""


class ManualEntryIn(BaseModel):
    """수기 전표 입력(기초잔액·마감·수정 분개). Σ차변 == Σ대변 이어야 한다."""
    entry_date: str = Field(..., description="YYYY-MM-DD")
    description: str = ""
    lines: list[ManualLineIn] = Field(..., min_length=2)

    @model_validator(mode="after")
    def _check_balanced(self):
        td = sum(l.debit for l in self.lines)
        tc = sum(l.credit for l in self.lines)
        if round(td, 4) != round(tc, 4):
            raise ValueError(f"차대 불일치: 차변 {td} != 대변 {tc}")
        if td == 0:
            raise ValueError("전표 금액이 0 입니다")
        for l in self.lines:
            if l.debit > 0 and l.credit > 0:
                raise ValueError(f"한 라인에 차변·대변을 동시에 기입할 수 없습니다 ({l.account_code})")
        return self


class FinancialRow(BaseModel):
    """손익계산서·재무상태표 1행(계정별 금액, 정상잔액 방향 양수)."""
    account_code: str
    account_name: str
    amount: float


class IncomeStatementOut(BaseModel):
    """간이 손익계산서(§7): 수익 − 비용 = 당기순이익. 기간 필터."""
    date_from: str | None = None
    date_to: str | None = None
    revenues: list[FinancialRow] = []
    expenses: list[FinancialRow] = []
    total_revenue: float
    total_expense: float
    net_income: float          # 수익 − 비용


class BalanceSheetOut(BaseModel):
    """간이 재무상태표(§7): 자산 = 부채 + 자본 + 당기순이익(미마감). date_to 시점 누계.
    마감 분개를 두지 않으므로 당기순이익은 손익계정에 남아 자본 아래 별도 표시한다."""
    date_to: str | None = None
    assets: list[FinancialRow] = []
    liabilities: list[FinancialRow] = []
    equity: list[FinancialRow] = []
    total_assets: float
    total_liabilities: float
    total_equity: float        # 자본계정 합(당기순이익 제외)
    net_income: float          # 손익계정 누계(수익 − 비용)
    total_liabilities_and_equity: float   # 부채 + 자본 + 당기순이익
    balanced: bool             # 자산 == 부채+자본+당기순이익
