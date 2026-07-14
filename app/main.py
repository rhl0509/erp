from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings
from .database import Base, engine
from .observability import (
    RequestContextMiddleware, setup_logging, get_request_id, render_metrics,
)
from . import models  # noqa: F401  (모델 등록을 위해 import 필요)
from .api import (
    auth, users, partners, items, stock, stats, reports, audit,
    purchase, sales, costing, payments, invoices, warehouses, gl,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # SQLite(로컬 개발)에서는 편의를 위해 기동 시 테이블을 생성한다.
    # MySQL 등 운영 DB에서는 스키마를 Alembic 마이그레이션으로만 관리한다:
    #   alembic upgrade head
    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="ERP Foundation API",
    version="0.2.0",
    description="ERP 공통 기반 + 거래처/품목 마스터. 웹 UI 는 Next.js 앱(frontend/)에서 제공하며, 이 서버는 API(/api)·문서(/docs)를 담당한다.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# request_id 부여 + JSON access 로그 + 메트릭. CORS 보다 나중에 add_middleware 해
# 스택의 바깥쪽에 놓는다(모든 실제 요청이 이 미들웨어를 통과).
setup_logging()
app.add_middleware(RequestContextMiddleware)


# ---------- 일관된 오류 응답 ----------
# 모든 오류를 {detail, code, request_id, fields?} 형식으로 통일해 클라이언트가 다루기
# 쉽게 한다. request_id 는 지원 문의 시 서버 로그와 상관지을 수 있는 키다.
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    code_map = {400: "bad_request", 401: "unauthorized", 403: "forbidden",
                404: "not_found", 409: "conflict", 422: "validation_error",
                429: "too_many_requests"}
    content = {"detail": exc.detail, "code": code_map.get(exc.status_code, "error"),
               "request_id": get_request_id()}
    # FieldError(app.errors)는 필드별 메시지를 함께 싣는다 — Pydantic 422 와 같은 형식이라
    # 화면이 입력칸 옆에 그대로 붙일 수 있다.
    fields = getattr(exc, "fields", None)
    if fields:
        content["fields"] = fields
    return JSONResponse(
        status_code=exc.status_code,
        content=content,
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # 필드별 한글 메시지로 변환해 어디가 잘못됐는지 바로 알 수 있게 한다.
    fields: dict[str, str] = {}
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", []) if p not in ("body", "query", "path")]
        key = ".".join(loc) or "요청"
        fields[key] = _friendly_message(err)
    return JSONResponse(
        status_code=422,
        content={"detail": "입력값을 다시 확인해 주세요.", "code": "validation_error",
                 "fields": fields, "request_id": get_request_id()},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # 미처리 예외 → 500 응답 통일. 스택은 RequestContextMiddleware 가 이미
    # (본문/토큰 등 민감정보 없이) JSON error 로그로 남겼다. 클라이언트에는 내부
    # 정보를 숨기고 request_id 만 노출한다. 이 핸들러는 미들웨어 스택의 최외곽
    # (ServerErrorMiddleware)에서 실행돼 X-Request-ID 응답 헤더를 직접 붙인다.
    rid = get_request_id()
    return JSONResponse(
        status_code=500,
        content={"detail": "서버 내부 오류가 발생했습니다.", "code": "internal_error",
                 "request_id": rid},
        headers={"X-Request-ID": rid},
    )


def _friendly_message(err: dict) -> str:
    t = err.get("type", "")
    ctx = err.get("ctx", {}) or {}
    if t == "missing":
        return "필수 항목입니다."
    if t.startswith("string_too_short"):
        return f"최소 {ctx.get('min_length', '')}자 이상 입력해 주세요."
    if t.startswith("string_too_long"):
        return f"최대 {ctx.get('max_length', '')}자까지 입력할 수 있습니다."
    if t in ("int_parsing", "int_type"):
        return "숫자만 입력해 주세요."
    if t.startswith("greater_than") or t.startswith("less_than"):
        return "허용 범위를 벗어났습니다."
    return err.get("msg", "올바르지 않은 값입니다.")


# ---------- API 라우터 ----------
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(partners.router)
app.include_router(items.router)
app.include_router(stock.router)
app.include_router(warehouses.router)
app.include_router(purchase.router)
app.include_router(sales.router)
app.include_router(costing.router)
app.include_router(payments.router)
app.include_router(invoices.router)
app.include_router(gl.router)
app.include_router(stats.router)
app.include_router(reports.router)
app.include_router(audit.router)


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}


@app.get("/metrics", tags=["system"], response_class=PlainTextResponse)
def metrics():
    """프로세스 인메모리 요청 카운터(text/plain). 멀티워커에서는 워커별 값이라
    참고용 — 자세한 이유는 app.observability 주석 참고."""
    return render_metrics()


# ---------- 루트 안내 ----------
# 웹 UI 는 Next.js 앱(frontend/)이 담당한다(레거시 단일 HTML 콘솔은 2026-07 제거).
# 이 서버는 API 전용이므로 루트는 진입 안내만 반환한다.
@app.get("/", include_in_schema=False)
def index():
    return JSONResponse({
        "service": "ERP Foundation API",
        "detail": "웹 UI 는 Next.js 앱(frontend/)에서 제공합니다. API 문서는 /docs 를 이용하세요.",
        "docs": "/docs",
    })
