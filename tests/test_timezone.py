"""사업장 시간대(BUSINESS_TZ) — 회계 날짜의 단일 소스.

문제: `created_at` 은 DB 시각(SQLite/MySQL 기본 UTC)인데 회계에서 '며칠 거래인가'는
사업장(KST) 기준이다. 그대로 쓰면 8/1 08:00 KST 출고가 7/31 로 기표되어(UTC 로는 7/31 23:00)
전월 매출이 되고, 7월이 마감돼 있으면 8월 거래가 거부된다.

여기서는 변환 규칙 자체(app/timeutil)와, 전표일자가 그 규칙을 따르는지를 본다.
"""
from datetime import datetime

import pytest

from app.config import settings
from app.timeutil import business_date_of, today_str, to_db_naive, current_period


def _ok(r, code=200):
    assert r.status_code == code, f"{r.status_code} {r.text}"
    return r.json() if r.content else None


# ---------- 변환 규칙 ----------
def test_db_utc_timestamp_maps_to_business_date():
    """UTC 로 저장된 7/31 23:00 은 사업장(KST)에서는 8/1 이다 — 8월 거래로 기표돼야 한다."""
    assert settings.business_tz == "Asia/Seoul"
    assert settings.db_tz == "UTC"
    assert business_date_of(datetime(2026, 7, 31, 23, 0, 0)) == "2026-08-01"
    assert business_date_of(datetime(2026, 7, 31, 14, 59, 0)) == "2026-07-31"  # 23:59 KST
    assert business_date_of(None) == today_str()


def test_business_month_boundary_is_not_off_by_one():
    """월 경계: KST 8/1 00:30 == UTC 7/31 15:30. 사업장 기준 달은 8월이어야 한다."""
    assert business_date_of(datetime(2026, 7, 31, 15, 30, 0))[:7] == "2026-08"


def test_to_db_naive_round_trip():
    """사업장 시각 → DB 시각 환산(통계·리포트의 기간 경계가 이 변환을 쓴다)."""
    kst_month_start = datetime(2026, 8, 1, 0, 0, 0)     # naive → 사업장 시각으로 해석
    assert to_db_naive(kst_month_start) == datetime(2026, 7, 31, 15, 0, 0)   # UTC


def test_tz_config_fallback_does_not_crash(monkeypatch):
    """잘못된 시간대 이름으로 기동이 죽지 않는다(UTC 폴백)."""
    monkeypatch.setattr(settings, "business_tz", "Not/AZone")
    assert business_date_of(datetime(2026, 7, 31, 23, 0, 0)) == "2026-07-31"


# ---------- 전표일자 ----------
def test_movement_entry_date_uses_business_today(client, admin):
    """재고이동 전표의 전표일자 = 사업장 기준 오늘(DB 시각의 날짜가 아니라)."""
    sup = _ok(client.post("/api/partners", headers=admin,
                          json={"name": "TZ공급", "partner_type": "supplier"}), 201)["id"]
    iid = _ok(client.post("/api/items", headers=admin,
                          json={"name": "TZ품목", "unit": "EA"}), 201)["id"]
    mv = _ok(client.post("/api/stock/movements", headers=admin, json={
        "item_id": iid, "movement_type": "IN", "quantity": 1,
        "unit_price": 1000, "partner_id": sup,
    }), 201)

    entry = _ok(client.get("/api/gl/journal", headers=admin,
                           params={"source_type": "MOVEMENT", "source_id": mv["id"]}))["items"][0]
    assert entry["entry_date"] == today_str()
    assert entry["entry_date"][:7] == current_period()
