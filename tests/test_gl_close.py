"""기간 마감(period close) — 월 단위 회계기간.

마감이 실제로 '숫자를 얼려' 주는가를 본다: 마감선(가장 최근 마감월) 이하에는 어떤 경로로도
기표가 들어가지 않아야 하고(수기 전표·과거 날짜 결제·원천 삭제), 마감 대체분개로 손익이
이월이익잉여금으로 넘어가야 하며, 마감 해제로 되돌릴 수 있어야 한다.

마감은 **끝난 달만** 할 수 있다(진행 중인 달을 마감하면 그 달의 남은 영업이 전부 막힌다).
그래서 테스트는 지난 달에 손익을 만들고(수기 전표) 그 달을 마감한다 — 재고이동은 항상
'오늘' 날짜라 지난 달로 만들 수 없기 때문이다.
"""
from datetime import timedelta

from app.timeutil import today as business_today   # 서버와 같은 '오늘'(사업장 시간대)

TODAY = business_today()
THIS_MONTH = TODAY.strftime("%Y-%m")

LAST_MONTH_END = TODAY.replace(day=1) - timedelta(days=1)   # 지난 달 말일
LAST_MONTH = LAST_MONTH_END.strftime("%Y-%m")
LAST_MONTH_DAY = LAST_MONTH_END.replace(day=15).isoformat()


def _ok(r, code=200):
    assert r.status_code == code, f"{r.status_code} {r.text}"
    return r.json() if r.content else None


def _err(r, status, code=None):
    assert r.status_code == status, f"{r.status_code} {r.text}"
    body = r.json()
    if code:
        assert body["code"] == code, body
    return body


def _partner(client, admin, name, ptype):
    return _ok(client.post("/api/partners", headers=admin,
                           json={"name": name, "partner_type": ptype}), 201)["id"]


def _periods(client, admin):
    return _ok(client.get("/api/gl/periods", headers=admin))


def _close(client, admin, period=LAST_MONTH):
    return client.post(f"/api/gl/periods/{period}/close", headers=admin)


def _close_through(client, admin, target=LAST_MONTH):
    """target 까지 오래된 달부터 순서대로 마감한다(마감은 순차만 허용되므로).
    전표가 없는 달은 목록(next_closable)에 안 잡히므로 target 은 마지막에 직접 마감한다."""
    while True:
        nxt = _periods(client, admin)["next_closable"]
        if not nxt or nxt >= target:
            break
        _ok(_close(client, admin, nxt))
    _ok(_close(client, admin, target))


def _manual(client, admin, entry_date, lines=None, amount=1000):
    body = {
        "entry_date": entry_date,
        "description": "테스트 수기 전표",
        "lines": lines or [
            {"account_code": "1110", "debit": amount},
            {"account_code": "3110", "credit": amount},
        ],
    }
    return client.post("/api/gl/manual", headers=admin, json=body)


def _last_month_pnl(client, admin, revenue=10000, expense=6000):
    """지난 달에 손익을 만든다(매출 4110 / 매출원가 5110)."""
    _ok(_manual(client, admin, LAST_MONTH_DAY, lines=[
        {"account_code": "1120", "debit": revenue},
        {"account_code": "4110", "credit": revenue},
    ]), 201)
    _ok(_manual(client, admin, LAST_MONTH_DAY, lines=[
        {"account_code": "5110", "debit": expense},
        {"account_code": "1130", "credit": expense},
    ]), 201)


# ---------- 목록 ----------
def test_period_list_and_next_closable(client, admin):
    _last_month_pnl(client, admin)
    body = _periods(client, admin)
    assert body["next_closable"] == LAST_MONTH        # 진행 중인 이번 달은 대상이 아니다

    last = next(p for p in body["periods"] if p["period"] == LAST_MONTH)
    assert last["status"] == "open" and last["net_income"] == 4000.0
    assert any(p["period"] == THIS_MONTH for p in body["periods"])


def test_cannot_close_current_or_future_period(client, admin):
    """진행 중인 달을 마감하면 그 달의 남은 영업이 전부 막힌다 — 아예 못 하게 한다."""
    r = _close(client, admin, THIS_MONTH)
    assert r.status_code == 400 and "끝나지 않은" in r.json()["detail"]
    assert _close(client, admin, "2099-12").status_code == 400


# ---------- 마감 대체분개 ----------
def test_close_transfers_pnl_to_retained_earnings(client, admin):
    _last_month_pnl(client, admin)
    _close_through(client, admin)
    last = next(p for p in _periods(client, admin)["periods"] if p["period"] == LAST_MONTH)
    assert last["status"] == "closed" and last["net_income"] == 4000.0

    entry = _ok(client.get(f"/api/gl/journal/{last['closing_entry_id']}", headers=admin))
    assert entry["source_type"] == "CLOSING"
    assert entry["entry_date"] == LAST_MONTH_END.isoformat()   # 전표일자 = 그 달 말일
    by_code = {l["account_code"]: (l["debit"], l["credit"]) for l in entry["lines"]}
    assert by_code["4110"] == (10000.0, 0.0)    # 수익 상계
    assert by_code["5110"] == (0.0, 6000.0)     # 비용 상계
    assert by_code["3110"] == (0.0, 4000.0)     # 차액 = 당기순이익 → 자본
    assert sum(l["debit"] for l in entry["lines"]) == sum(l["credit"] for l in entry["lines"])

    tb = _ok(client.get("/api/gl/trial-balance", headers=admin))
    by_acc = {r["account_code"]: r["balance"] for r in tb["rows"]}
    assert by_acc["4110"] == 0.0 and by_acc["5110"] == 0.0   # 손익계정이 0 으로 닫혔다
    assert by_acc["3110"] == 4000.0
    assert tb["balanced"] is True

    bs = _ok(client.get("/api/gl/balance-sheet", headers=admin))
    assert bs["net_income"] == 0.0        # 미마감 손익 없음
    assert bs["total_equity"] == 4000.0   # 이익이 자본으로 넘어갔다
    assert bs["balanced"] is True


def test_close_with_loss_debits_retained_earnings(client, admin):
    """손실이면 3110 을 차변으로 — 부호가 뒤집혀야 한다."""
    _last_month_pnl(client, admin, revenue=3000, expense=8000)
    _close_through(client, admin)
    last = next(p for p in _periods(client, admin)["periods"] if p["period"] == LAST_MONTH)
    assert last["net_income"] == -5000.0

    entry = _ok(client.get(f"/api/gl/journal/{last['closing_entry_id']}", headers=admin))
    by_code = {l["account_code"]: (l["debit"], l["credit"]) for l in entry["lines"]}
    assert by_code["3110"] == (5000.0, 0.0)   # 손실 → 자본 감소(차변)


def test_income_statement_keeps_closed_month_result(client, admin):
    """마감했다고 그 달 매출이 0 이 되면 안 된다(손익계산서는 마감 전표 제외)."""
    _last_month_pnl(client, admin)
    before = _ok(client.get("/api/gl/income-statement", headers=admin))
    _close_through(client, admin)
    after = _ok(client.get("/api/gl/income-statement", headers=admin))
    assert after["total_revenue"] == before["total_revenue"] == 10000.0
    assert after["total_expense"] == before["total_expense"] == 6000.0
    assert after["net_income"] == 4000.0


def test_close_with_no_activity_creates_no_entry(client, admin):
    """손익 활동이 없는 달은 전표 없이 기간만 닫는다(0원 라인 금지)."""
    _ok(_manual(client, admin, LAST_MONTH_DAY), 201)   # 손익 아닌 전표(1110/3110)
    result = _ok(_close(client, admin))
    assert result["net_income"] == 0.0 and result["closing_entry_id"] is None


# ---------- 마감 후 기표 차단 ----------
def test_closed_period_blocks_manual_entry(client, admin):
    _last_month_pnl(client, admin)
    _close_through(client, admin)
    body = _err(_manual(client, admin, LAST_MONTH_DAY), 400, "period_closed")
    assert LAST_MONTH in body["detail"]


def test_closed_period_blocks_backdated_payment(client, admin):
    """과거 날짜(마감된 달)로 결제를 넣어 마감분 숫자를 바꾸는 경로 — 차단."""
    sup = _partner(client, admin, "마감공급", "supplier")
    _close_through(client, admin)
    r = client.post("/api/payments", headers=admin, json={
        "partner_id": sup, "kind": "AP", "amount": 1000, "pay_date": LAST_MONTH_DAY,
    })
    _err(r, 400, "period_closed")


def test_current_month_operations_continue_after_close(client, admin):
    """지난 달을 마감해도 이번 달 영업(재고이동·결제·수기전표)은 계속돼야 한다."""
    sup = _partner(client, admin, "이번달공급", "supplier")
    iid = _ok(client.post("/api/items", headers=admin,
                          json={"name": "이번달품목", "unit": "EA"}), 201)["id"]
    _close_through(client, admin)

    _ok(client.post("/api/stock/movements", headers=admin, json={
        "item_id": iid, "movement_type": "IN", "quantity": 2,
        "unit_price": 5000, "partner_id": sup,
    }), 201)
    _ok(client.post("/api/payments", headers=admin, json={
        "partner_id": sup, "kind": "AP", "amount": 1000, "pay_date": TODAY.isoformat(),
    }), 201)
    _ok(_manual(client, admin, TODAY.isoformat()), 201)


def test_closed_period_blocks_source_deletion(client, admin):
    """원천(결제) 삭제로 마감분 전표를 지우는 경로 — 차단."""
    sup = _partner(client, admin, "마감공급", "supplier")
    pay = _ok(client.post("/api/payments", headers=admin, json={
        "partner_id": sup, "kind": "AP", "amount": 1000, "pay_date": LAST_MONTH_DAY,
    }), 201)
    _close_through(client, admin)
    _err(client.delete(f"/api/payments/{pay['id']}", headers=admin), 400, "period_closed")


def test_closed_period_blocks_manual_entry_deletion(client, admin):
    entry = _ok(_manual(client, admin, LAST_MONTH_DAY), 201)
    _close_through(client, admin)
    _err(client.delete(f"/api/gl/journal/{entry['id']}", headers=admin), 400, "period_closed")


def test_closing_entry_cannot_be_deleted_directly(client, admin):
    _last_month_pnl(client, admin)
    _close_through(client, admin)
    last = next(p for p in _periods(client, admin)["periods"] if p["period"] == LAST_MONTH)
    r = client.delete(f"/api/gl/journal/{last['closing_entry_id']}", headers=admin)
    assert r.status_code == 400 and "마감" in r.json()["detail"]


def test_closed_period_blocks_rebuild(client, admin):
    """재전기는 마감분 전표까지 다시 만들 수 있으므로 마감 중에는 막는다."""
    _last_month_pnl(client, admin)
    _close_through(client, admin)
    r = client.post("/api/gl/rebuild", headers=admin)
    assert r.status_code == 409 and LAST_MONTH in r.json()["detail"]


# ---------- 마감선(watermark) ----------
def test_watermark_blocks_earlier_open_months(client, admin):
    """마감된 달보다 '이전' 달에도 기표할 수 없다 — 허용하면 이미 보고한 누계
    재무상태표가 소급 변동한다."""
    _last_month_pnl(client, admin)
    _close_through(client, admin)
    # 2026-01 은 기간 행조차 없지만(마감된 적 없음) 마감선 이하라 막혀야 한다
    _err(_manual(client, admin, "2026-01-15"), 400, "period_closed")


def test_invalid_entry_date_rejected(client, admin):
    """존재하지 않는 날짜('2026-06-31')는 마감 차단에는 걸리면서 마감 합산에서는 빠져
    '마감된 달의 손익이 0 이 아닌' 상태를 만든다 — 입력 단계에서 막는다."""
    assert _manual(client, admin, "2026-06-31").status_code == 422
    assert _manual(client, admin, "2026-6-15").status_code == 422
    assert _manual(client, admin, "").status_code == 422


# ---------- 마감 순서 ----------
def test_must_close_oldest_period_first(client, admin):
    _ok(_manual(client, admin, "2026-01-15"), 201)   # 과거 달에 기표 → 그 달이 열린다
    r = _close(client, admin, LAST_MONTH)
    assert r.status_code == 400 and "2026-01" in r.json()["detail"]

    _ok(_close(client, admin, "2026-01"))            # 오래된 달부터면 OK
    jan = next(p for p in _periods(client, admin)["periods"] if p["period"] == "2026-01")
    assert jan["status"] == "closed"


def test_cannot_close_twice(client, admin):
    _close_through(client, admin)
    r = _close(client, admin, LAST_MONTH)
    assert r.status_code == 400 and "이미 마감" in r.json()["detail"]


# ---------- 마감 해제 ----------
def test_reopen_restores_posting(client, admin):
    _last_month_pnl(client, admin)
    _close_through(client, admin)
    last = next(p for p in _periods(client, admin)["periods"] if p["period"] == LAST_MONTH)
    _err(_manual(client, admin, LAST_MONTH_DAY), 400, "period_closed")

    reopened = _ok(client.post(f"/api/gl/periods/{LAST_MONTH}/reopen", headers=admin))
    assert reopened["status"] == "open"

    # 마감 전표가 사라지고 손익이 손익계정으로 되돌아온다
    assert client.get(f"/api/gl/journal/{last['closing_entry_id']}",
                      headers=admin).status_code == 404
    bs = _ok(client.get("/api/gl/balance-sheet", headers=admin))
    assert bs["net_income"] == 4000.0 and bs["total_equity"] == 0.0
    assert bs["balanced"] is True

    _ok(_manual(client, admin, LAST_MONTH_DAY), 201)      # 다시 기표 가능
    _ok(client.post("/api/gl/rebuild", headers=admin))    # 재전기도 다시 가능


def test_reopen_records_audit_snapshot(client, admin):
    """마감 전표는 삭제되므로, 무엇을 되돌렸는지가 감사 로그에 남아야 한다."""
    _last_month_pnl(client, admin)
    _close_through(client, admin)
    _ok(client.post(f"/api/gl/periods/{LAST_MONTH}/reopen", headers=admin))

    logs = _ok(client.get("/api/audit", headers=admin, params={"entity": "gl_period"}))
    reopen_log = next(l for l in logs["items"] if '"status": "open"' in (l["after"] or ""))
    assert "closing_entry_no" in reopen_log["before"]
    assert "4000" in reopen_log["before"]      # 되돌린 당기순이익


def test_reopen_only_in_reverse_order(client, admin):
    _ok(_manual(client, admin, "2026-01-15"), 201)
    _close_through(client, admin, LAST_MONTH)   # 2026-01 ~ 지난 달까지 순서대로 마감

    r = client.post("/api/gl/periods/2026-01/reopen", headers=admin)
    assert r.status_code == 400 and "역순" in r.json()["detail"]

    _ok(client.post(f"/api/gl/periods/{LAST_MONTH}/reopen", headers=admin))
    assert _periods(client, admin)["next_closable"] == LAST_MONTH


def test_reopen_open_period_rejected(client, admin):
    assert client.post(f"/api/gl/periods/{LAST_MONTH}/reopen",
                       headers=admin).status_code == 400


# ---------- 권한 ----------
def test_close_requires_write_permission(client, admin):
    roles = {r["name"]: r["id"] for r in _ok(client.get("/api/roles", headers=admin))}
    _ok(client.post("/api/users", headers=admin, json={
        "username": "viewer1", "password": "ViewerPw2026", "role_ids": [roles["viewer"]],
    }), 201)
    token = _ok(client.post("/api/auth/login",
                            data={"username": "viewer1", "password": "ViewerPw2026"}))["access_token"]
    client.cookies.clear()
    vh = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/gl/periods", headers=vh).status_code == 200   # 조회는 가능
    assert _close(client, vh).status_code == 403                          # 마감은 불가
