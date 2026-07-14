"""사업장 시간대(business timezone) — 날짜의 단일 소스.

문제: 회계에서 '며칠에 일어난 거래인가'는 사업장 기준이다. 그런데 코드에는 세 종류의
시각이 섞여 있었다.
  - `created_at` 컬럼: **DB 서버 시각**(func.now(); MySQL 은 세션 time_zone, SQLite 는 UTC)
  - `datetime.now()`: **앱 프로세스의 로컬 시각**(컨테이너면 대개 UTC)
  - `datetime.now(timezone.utc)`: UTC

DB 가 UTC 이고 사업장이 KST 면 2026-08-01 08:00 KST 의 출고는 `created_at=2026-07-31 23:00`
→ 전표일자 2026-07-31 → **7월 매출로 기표**되고, 7월이 이미 마감돼 있으면 8월 거래가
거부된다. 월 경계마다 하루가 인접 월로 새는 셈이다.

해결: 날짜(전표일자·발행일·채번 기간·이번 달)는 전부 이 모듈에서만 만든다.
  - BUSINESS_TZ: 사업장 시간대(기본 Asia/Seoul, `.env` 의 BUSINESS_TZ)
  - DB_TZ: DB 가 naive 타임스탬프를 어느 시간대로 쓰는지(기본 UTC, `.env` 의 DB_TZ)
DB 를 KST 로 운영한다면 DB_TZ=Asia/Seoul 로 두면 변환이 항등이 된다.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .config import settings


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:   # 잘못된 시간대 이름으로 기동이 죽지 않게(UTC 폴백 + 동작은 계속)
        return ZoneInfo("UTC")


def business_tz() -> ZoneInfo:
    return _tz(settings.business_tz)


def db_tz() -> ZoneInfo:
    return _tz(settings.db_tz)


def now_business() -> datetime:
    """사업장 시간대의 현재 시각(aware)."""
    return datetime.now(business_tz())


def today() -> date:
    """사업장 기준 오늘."""
    return now_business().date()


def today_str() -> str:
    """사업장 기준 오늘 'YYYY-MM-DD' — 전표일자·발행일의 기본값."""
    return today().isoformat()


def current_period() -> str:
    """사업장 기준 이번 달 'YYYY-MM' — 마감 가능 판정·채번 기간."""
    return now_business().strftime("%Y-%m")


def seq_period() -> str:
    """채번용 'YYYYMM'(사업장 기준). 번호의 달과 전표일자의 달이 갈라지지 않게 한다."""
    return now_business().strftime("%Y%m")


def business_date_of(value: datetime | None) -> str:
    """DB 에서 읽은 naive 타임스탬프(created_at 등)를 사업장 기준 날짜로 옮긴다.

    naive 값은 DB_TZ 로 해석하고, 이미 tz 가 붙어 있으면 그대로 존중한다.
    값이 없으면(플러시 전) 지금 시각으로 본다 — 전기는 원천 생성과 같은 트랜잭션에서
    일어나므로 둘의 차이는 밀리초다.
    """
    if value is None:
        return today_str()
    aware = value.replace(tzinfo=db_tz()) if value.tzinfo is None else value
    return aware.astimezone(business_tz()).date().isoformat()


def to_db_naive(value: datetime) -> datetime:
    """사업장 시각(aware) → DB 컬럼과 비교 가능한 naive 시각(DB_TZ 기준).
    통계·리포트가 created_at 을 기간으로 자를 때 쓴다."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=business_tz())
    return value.astimezone(db_tz()).replace(tzinfo=None)


def utc_naive_now() -> datetime:
    """감사·세션 타임스탬프처럼 '기록 시각'용(사업장 날짜와 무관). UTC naive."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
