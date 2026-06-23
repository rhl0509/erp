from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings
from .database import Base, engine
from . import models  # noqa: F401  (모델 등록을 위해 import 필요)
from .api import auth, users, partners, items

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 개발 편의를 위해 기동 시 테이블 생성.
    # 운영에서는 Alembic 마이그레이션으로 대체하세요.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="ERP Foundation API",
    version="0.2.0",
    description="ERP 공통 기반 + 거래처/품목 마스터. 웹 화면은 첫 페이지(/)에서 바로 사용하세요.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- 일관된 오류 응답 ----------
# 모든 오류를 {detail, code, fields?} 형식으로 통일해 클라이언트가 다루기 쉽게 한다.
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    code_map = {400: "bad_request", 401: "unauthorized", 403: "forbidden",
                404: "not_found", 409: "conflict"}
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": code_map.get(exc.status_code, "error")},
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
        content={"detail": "입력값을 다시 확인해 주세요.", "code": "validation_error", "fields": fields},
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


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}


# ---------- 웹 화면(클라이언트용 UI) ----------
# 정적 자산과 첫 페이지를 함께 서빙한다. (API는 /api, 문서는 /docs)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse({"detail": "UI가 설치되지 않았습니다. /docs 를 이용하세요.", "code": "no_ui"})
