"""관측성(Observability) 유틸: request_id 전파 + JSON 구조적 로깅 + 인메모리 메트릭.

새 pip 의존성 없이 표준 라이브러리(logging/contextvars/uuid/threading)만 쓴다.
민감정보(요청/응답 본문, Authorization 헤더, 비밀번호, JWT 토큰, 쿠키)는
어떤 로그에도 남기지 않는다 — 여기서는 애초에 그런 값을 읽지도 않는다.
"""
import json
import logging
import sys
import threading
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# ---------- request_id 전파 (contextvar) ----------
# 요청마다 상관관계 ID를 contextvar 에 실어, 어느 레이어(라우터/서비스/예외 핸들러)의
# 로그든 같은 요청의 로그끼리 request_id 로 묶을 수 있게 한다. asyncio 태스크별로
# 독립된 컨텍스트라 동시 요청끼리 섞이지 않는다. 요청 밖(스크립트/테스트 셋업)은 "-".
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    """현재 요청의 상관관계 ID. 요청 컨텍스트 밖에서는 '-'."""
    return request_id_var.get()


# ---------- JSON 구조적 로깅 ----------
# 한 줄 JSON 포맷. 수집기(CloudWatch/Loki 등)가 파싱하기 쉽고, request_id 로
# 같은 요청의 access/error 로그를 상관지을 수 있다.
logger = logging.getLogger("erp")

# access 로그에 허용하는 추가 필드 화이트리스트. 이 외의 extra 는 무시해
# 실수로 본문·헤더 같은 민감정보가 로그에 흘러들지 않게 한다.
_EXTRA_FIELDS = ("method", "path", "status", "duration_ms", "client_ip")


class JsonFormatter(logging.Formatter):
    """로그 레코드를 한 줄 JSON 으로 직렬화한다.

    request_id 는 포맷터가 contextvar 에서 자동 주입하므로, 로그를 남기는 쪽은
    request_id 를 신경 쓸 필요가 없다(서비스 계층 어디서 찍어도 상관관계 유지).
    """

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "request_id": request_id_var.get(),
            "msg": record.getMessage(),
        }
        for key in _EXTRA_FIELDS:
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        if record.exc_info:
            # 스택트레이스 포함. 예외 메시지에 본문/토큰을 넣지 않는 것은
            # 예외를 던지는 쪽의 규약이며, 여기서는 추가 컨텍스트를 싣지 않는다.
            entry["stack"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging() -> None:
    """'erp' 로거에 JSON 핸들러를 1회만 붙인다(재임포트/테스트 재실행에 안전)."""
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False  # 루트 로거로 중복 출력 방지


# ---------- 최소 인메모리 메트릭 ----------
# 프로세스 로컬 카운터. gunicorn 등 멀티워커 배포에서는 워커별 값이라 합산이 안 되는
# 참고용 지표다 — 그래서 Prometheus 클라이언트 같은 라이브러리를 붙이는 과설계를
# 하지 않고, 단일 프로세스(uvicorn) 운영 기준의 최소 지표만 유지한다.
_metrics_lock = threading.Lock()
_requests_total = 0
_requests_by_class: dict[str, int] = {}


def record_request_metric(status: int) -> None:
    """요청 1건을 상태코드대(2xx/4xx/5xx...)별로 집계한다."""
    global _requests_total
    klass = f"{status // 100}xx"
    with _metrics_lock:
        _requests_total += 1
        _requests_by_class[klass] = _requests_by_class.get(klass, 0) + 1


def render_metrics() -> str:
    """GET /metrics 용 text/plain 렌더링."""
    with _metrics_lock:
        total = _requests_total
        by_class = dict(_requests_by_class)
    lines = [f"http_requests_total {total}"]
    for klass in sorted(by_class):
        lines.append(f'http_requests{{class="{klass}"}} {by_class[klass]}')
    return "\n".join(lines) + "\n"


# ---------- 미들웨어 ----------
class RequestContextMiddleware(BaseHTTPMiddleware):
    """요청마다 (1) request_id 부여/전파, (2) access 로그 1줄, (3) 메트릭 집계.

    - 들어온 X-Request-ID 헤더가 있으면 그대로 쓰고(게이트웨이/클라이언트 발급 ID
      존중), 없으면 UUID4 를 생성한다. 응답 헤더 X-Request-ID 로 되돌려준다.
    - access 로그에는 path 만 남기고 쿼리스트링은 제외한다(토큰류가 쿼리에 실릴
      가능성에 대한 방어). 본문/헤더/쿠키는 절대 로깅하지 않는다.
    - 미처리 예외는 여기서 스택과 함께 error 로그를 남기고 다시 던진다.
      500 응답 본문은 app.main 의 Exception 핸들러가 만든다.
    """

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request_id_var.set(rid)

        start = time.perf_counter()
        method = request.method
        path = request.url.path  # 쿼리스트링 제외(민감정보 방어)
        client_ip = request.client.host if request.client else "-"

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            record_request_metric(500)
            # 스택 포함 error 로그(민감정보 없음) + 500 access 로그. 예외는 다시 던져
            # app.main 의 Exception 핸들러가 {detail, code, request_id} 응답을 만든다.
            logger.error("unhandled exception", exc_info=True)
            logger.info(
                "access",
                extra={"method": method, "path": path, "status": 500,
                       "duration_ms": duration_ms, "client_ip": client_ip},
            )
            raise

        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        record_request_metric(response.status_code)
        logger.info(
            "access",
            extra={"method": method, "path": path, "status": response.status_code,
                   "duration_ms": duration_ms, "client_ip": client_ip},
        )
        response.headers["X-Request-ID"] = rid
        return response
