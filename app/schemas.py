from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field, ConfigDict

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
    password: str = Field(min_length=4)
    full_name: str = ""
    email: str = ""
    role_ids: list[int] = []


# ---------- 거래처 ----------
class PartnerBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    partner_type: str = "customer"
    business_no: str = ""
    ceo_name: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    is_active: bool = True


class PartnerCreate(PartnerBase):
    pass


class PartnerUpdate(PartnerBase):
    pass


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
    purchase_price: int = 0
    sales_price: int = 0
    is_active: bool = True


class ItemCreate(ItemBase):
    pass


class ItemUpdate(ItemBase):
    pass


class ItemOut(ItemBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    created_at: datetime
