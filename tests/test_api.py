"""ERP 핵심 흐름 회귀 테스트 (인증/권한, 부분수정, 재고, 발주/수주, 원가)."""


def _ok(r):
    assert r.status_code < 400, f"{r.status_code} {r.text}"
    return r.json()


# ---------- 기본 / 인증 ----------
def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_login_me_permissions(client, admin):
    me = _ok(client.get("/api/auth/me", headers=admin))
    assert me["username"] == "admin"
    assert "*" in me["permissions"]


def test_login_sets_httponly_session_cookie(client):
    """로그인은 httpOnly 세션 쿠키를 심고, 그 쿠키만으로(Authorization 헤더 없이) 인증된다."""
    r = client.post("/api/auth/login", data={"username": "admin", "password": "admin1234"})
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "")
    assert "erp_session=" in set_cookie and "HttpOnly" in set_cookie
    assert client.cookies.get("erp_session")
    # 헤더 없이 쿠키만으로 인증
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["username"] == "admin"


def test_logout_clears_session_cookie(client):
    """로그아웃은 세션 쿠키를 제거하고, 이후 쿠키 인증이 실패한다."""
    client.post("/api/auth/login", data={"username": "admin", "password": "admin1234"})
    assert client.get("/api/auth/me").status_code == 200          # 쿠키 인증 OK
    assert client.post("/api/auth/logout").status_code == 204
    client.cookies.clear()                                        # 삭제(Max-Age=0) 반영
    assert client.get("/api/auth/me").status_code == 401          # 쿠키 없음 → 미인증


def test_invalid_session_cookie_rejected(client):
    """위조·손상된 세션 쿠키는 401(서명 검증 실패)."""
    client.cookies.set("erp_session", "not-a-valid-jwt")
    assert client.get("/api/auth/me").status_code == 401


def test_requires_auth(client):
    assert client.get("/api/partners").status_code == 401


def test_rbac_viewer_cannot_write(client, admin):
    roles = _ok(client.get("/api/roles", headers=admin))
    viewer_id = next(r["id"] for r in roles if r["name"] == "viewer")
    _ok(client.post("/api/users", headers=admin,
                    json={"username": "v1", "password": "pw123456", "role_ids": [viewer_id]}))
    tok = _ok(client.post("/api/auth/login", data={"username": "v1", "password": "pw123456"}))["access_token"]
    vh = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/partners", headers=vh).status_code == 200          # 조회 가능
    assert client.post("/api/partners", headers=vh, json={"name": "x"}).status_code == 403  # 쓰기 불가


# ---------- 부분 수정 (Phase 0) ----------
def test_partner_partial_update_keeps_other_fields(client, admin):
    p = _ok(client.post("/api/partners", headers=admin,
                        json={"name": "가", "partner_type": "customer", "phone": "02-1"}))
    upd = _ok(client.put(f"/api/partners/{p['id']}", headers=admin, json={"phone": "010-9"}))
    assert upd["phone"] == "010-9"
    assert upd["is_active"] is True and upd["partner_type"] == "customer"
    # 빈 수정은 400
    assert client.put(f"/api/partners/{p['id']}", headers=admin, json={}).status_code == 400


# ---------- 재고 잔고/가드 (Phase 1) ----------
def test_stock_balance_and_out_guard(client, admin):
    iid = _ok(client.post("/api/items", headers=admin, json={"name": "품", "unit": "EA"}))["id"]
    _ok(client.post("/api/stock/movements", headers=admin,
                    json={"item_id": iid, "movement_type": "IN", "quantity": 10}))
    _ok(client.post("/api/stock/movements", headers=admin,
                    json={"item_id": iid, "movement_type": "OUT", "quantity": 4}))
    lv = _ok(client.get("/api/stock/levels", headers=admin, params={"q": "품"}))["items"][0]
    assert lv["on_hand"] == 6
    # 재고 초과 출고 차단
    assert client.post("/api/stock/movements", headers=admin,
                       json={"item_id": iid, "movement_type": "OUT", "quantity": 999}).status_code == 400


def test_document_movement_not_deletable(client, admin):
    sup = _ok(client.post("/api/partners", headers=admin, json={"name": "공급", "partner_type": "supplier"}))["id"]
    iid = _ok(client.post("/api/items", headers=admin, json={"name": "부품", "unit": "EA"}))["id"]
    po = _ok(client.post("/api/purchase-orders", headers=admin,
                         json={"partner_id": sup, "lines": [{"item_id": iid, "qty": 5, "unit_price": 100}]}))
    _ok(client.post(f"/api/purchase-orders/{po['id']}/confirm", headers=admin))
    _ok(client.post(f"/api/purchase-orders/{po['id']}/receive", headers=admin,
                    json={"lines": [{"line_id": po["lines"][0]["id"], "qty": 5}]}))
    mv = _ok(client.get("/api/stock/movements", headers=admin, params={"item_id": iid}))["items"][0]
    assert client.delete(f"/api/stock/movements/{mv['id']}", headers=admin).status_code == 400


# ---------- 발주(구매) 흐름 ----------
def test_purchase_receive_flow(client, admin):
    sup = _ok(client.post("/api/partners", headers=admin, json={"name": "공급2", "partner_type": "supplier"}))["id"]
    iid = _ok(client.post("/api/items", headers=admin, json={"name": "위젯", "unit": "EA"}))["id"]
    po = _ok(client.post("/api/purchase-orders", headers=admin,
                         json={"partner_id": sup, "lines": [{"item_id": iid, "qty": 100, "unit_price": 500}]}))
    assert po["status"] == "draft" and po["total_amount"] == 50000
    line = po["lines"][0]["id"]
    # 미확정 입고 차단
    assert client.post(f"/api/purchase-orders/{po['id']}/receive", headers=admin,
                       json={"lines": [{"line_id": line, "qty": 10}]}).status_code == 400
    _ok(client.post(f"/api/purchase-orders/{po['id']}/confirm", headers=admin))
    r = _ok(client.post(f"/api/purchase-orders/{po['id']}/receive", headers=admin,
                        json={"lines": [{"line_id": line, "qty": 40}]}))
    assert r["status"] == "partial"
    # 초과 입고 차단
    assert client.post(f"/api/purchase-orders/{po['id']}/receive", headers=admin,
                       json={"lines": [{"line_id": line, "qty": 61}]}).status_code == 400
    r = _ok(client.post(f"/api/purchase-orders/{po['id']}/receive", headers=admin,
                        json={"lines": [{"line_id": line, "qty": 60}]}))
    assert r["status"] == "received"
    lv = _ok(client.get("/api/stock/levels", headers=admin, params={"q": "위젯"}))["items"][0]
    assert lv["on_hand"] == 100


def test_purchase_receive_duplicate_line_no_overreceive(client, admin):
    """같은 line_id 를 한 요청에 중복으로 보내도 주문수량을 초과 입고할 수 없다."""
    sup = _ok(client.post("/api/partners", headers=admin, json={"name": "공급중복", "partner_type": "supplier"}))["id"]
    iid = _ok(client.post("/api/items", headers=admin, json={"name": "중복품", "unit": "EA"}))["id"]
    po = _ok(client.post("/api/purchase-orders", headers=admin,
                         json={"partner_id": sup, "lines": [{"item_id": iid, "qty": 10, "unit_price": 100}]}))
    line = po["lines"][0]["id"]
    _ok(client.post(f"/api/purchase-orders/{po['id']}/confirm", headers=admin))
    # 잔여 10 인데 10+10 을 중복 전송 -> 합산 20 > 10 이므로 400, 아무것도 입고되면 안 됨
    r = client.post(f"/api/purchase-orders/{po['id']}/receive", headers=admin,
                    json={"lines": [{"line_id": line, "qty": 10}, {"line_id": line, "qty": 10}]})
    assert r.status_code == 400
    assert _ok(client.get("/api/stock/levels", headers=admin, params={"q": "중복품"}))["items"][0]["on_hand"] == 0
    # 6+6 도 합산 12 > 10 이므로 차단
    assert client.post(f"/api/purchase-orders/{po['id']}/receive", headers=admin,
                       json={"lines": [{"line_id": line, "qty": 6}, {"line_id": line, "qty": 6}]}).status_code == 400
    # 6+4 = 10 은 정확히 잔여와 같으므로 허용, 재고 10
    r = _ok(client.post(f"/api/purchase-orders/{po['id']}/receive", headers=admin,
                        json={"lines": [{"line_id": line, "qty": 6}, {"line_id": line, "qty": 4}]}))
    assert r["status"] == "received"
    assert _ok(client.get("/api/stock/levels", headers=admin, params={"q": "중복품"}))["items"][0]["on_hand"] == 10


# ---------- 수주(판매) 흐름 + 재고부족 ----------
def test_sales_ship_and_insufficient(client, admin):
    sup = _ok(client.post("/api/partners", headers=admin, json={"name": "공급3", "partner_type": "supplier"}))["id"]
    cus = _ok(client.post("/api/partners", headers=admin, json={"name": "고객3", "partner_type": "customer"}))["id"]
    iid = _ok(client.post("/api/items", headers=admin, json={"name": "제품", "unit": "EA"}))["id"]
    # 재고 30 확보
    po = _ok(client.post("/api/purchase-orders", headers=admin,
                         json={"partner_id": sup, "lines": [{"item_id": iid, "qty": 30, "unit_price": 100}]}))
    _ok(client.post(f"/api/purchase-orders/{po['id']}/confirm", headers=admin))
    _ok(client.post(f"/api/purchase-orders/{po['id']}/receive", headers=admin,
                    json={"lines": [{"line_id": po["lines"][0]["id"], "qty": 30}]}))
    # 수주 20, 출고 20 -> shipped, 재고 10
    so = _ok(client.post("/api/sales-orders", headers=admin,
                         json={"partner_id": cus, "lines": [{"item_id": iid, "qty": 20, "unit_price": 300}]}))
    _ok(client.post(f"/api/sales-orders/{so['id']}/confirm", headers=admin))
    r = _ok(client.post(f"/api/sales-orders/{so['id']}/ship", headers=admin,
                        json={"lines": [{"line_id": so["lines"][0]["id"], "qty": 20}]}))
    assert r["status"] == "shipped"
    assert _ok(client.get("/api/stock/levels", headers=admin, params={"q": "제품"}))["items"][0]["on_hand"] == 10
    # 재고(10) 초과 수주 출고 차단
    so2 = _ok(client.post("/api/sales-orders", headers=admin,
                          json={"partner_id": cus, "lines": [{"item_id": iid, "qty": 50, "unit_price": 300}]}))
    _ok(client.post(f"/api/sales-orders/{so2['id']}/confirm", headers=admin))
    r = client.post(f"/api/sales-orders/{so2['id']}/ship", headers=admin,
                    json={"lines": [{"line_id": so2["lines"][0]["id"], "qty": 50}]})
    assert r.status_code == 400
    # 실패 후 재고 불변
    assert _ok(client.get("/api/stock/levels", headers=admin, params={"q": "제품"}))["items"][0]["on_hand"] == 10


def test_sales_ship_duplicate_line_no_overship(client, admin):
    """같은 line_id 중복 전송으로 주문수량을 초과 출고(없는 재고 판매)할 수 없다."""
    sup = _ok(client.post("/api/partners", headers=admin, json={"name": "공급중복S", "partner_type": "supplier"}))["id"]
    cus = _ok(client.post("/api/partners", headers=admin, json={"name": "고객중복S", "partner_type": "customer"}))["id"]
    iid = _ok(client.post("/api/items", headers=admin, json={"name": "출고중복품", "unit": "EA"}))["id"]
    # 재고 30 확보
    po = _ok(client.post("/api/purchase-orders", headers=admin,
                         json={"partner_id": sup, "lines": [{"item_id": iid, "qty": 30, "unit_price": 100}]}))
    _ok(client.post(f"/api/purchase-orders/{po['id']}/confirm", headers=admin))
    _ok(client.post(f"/api/purchase-orders/{po['id']}/receive", headers=admin,
                    json={"lines": [{"line_id": po["lines"][0]["id"], "qty": 30}]}))
    # 수주 10, 재고는 30 있지만 주문수량 10 을 넘는 출고는 막혀야 함
    so = _ok(client.post("/api/sales-orders", headers=admin,
                         json={"partner_id": cus, "lines": [{"item_id": iid, "qty": 10, "unit_price": 300}]}))
    line = so["lines"][0]["id"]
    _ok(client.post(f"/api/sales-orders/{so['id']}/confirm", headers=admin))
    # 10+10 중복 -> 합산 20 > 주문 10 이므로 400, 재고 30 불변
    assert client.post(f"/api/sales-orders/{so['id']}/ship", headers=admin,
                       json={"lines": [{"line_id": line, "qty": 10}, {"line_id": line, "qty": 10}]}).status_code == 400
    assert _ok(client.get("/api/stock/levels", headers=admin, params={"q": "출고중복품"}))["items"][0]["on_hand"] == 30
    # 4+6 = 10 은 허용, 재고 20, 상태 shipped
    r = _ok(client.post(f"/api/sales-orders/{so['id']}/ship", headers=admin,
                        json={"lines": [{"line_id": line, "qty": 4}, {"line_id": line, "qty": 6}]}))
    assert r["status"] == "shipped"
    assert _ok(client.get("/api/stock/levels", headers=admin, params={"q": "출고중복품"}))["items"][0]["on_hand"] == 20


# ---------- 원가 / 매출총이익 (Phase 1 #3) ----------
def test_moving_average_cost_and_margin(client, admin):
    iid = _ok(client.post("/api/items", headers=admin, json={"name": "코스트", "unit": "EA"}))["id"]
    _ok(client.post("/api/stock/movements", headers=admin,
                    json={"item_id": iid, "movement_type": "IN", "quantity": 10, "unit_price": 500}))
    _ok(client.post("/api/stock/movements", headers=admin,
                    json={"item_id": iid, "movement_type": "IN", "quantity": 10, "unit_price": 700}))
    val = _ok(client.get("/api/costing/valuation", headers=admin, params={"q": "코스트"}))["items"][0]
    assert val["avg_cost"] == 600 and val["value"] == 12000
    _ok(client.post("/api/stock/movements", headers=admin,
                    json={"item_id": iid, "movement_type": "OUT", "quantity": 5, "unit_price": 1000}))
    m = _ok(client.get("/api/costing/margin", headers=admin))
    assert m["revenue"] == 5000 and m["cogs"] == 3000 and m["gross_profit"] == 2000 and m["margin_rate"] == 0.4


# ---------- 결제/수금 + 미지급 잔액 (AR/AP) ----------
def test_payments_and_partner_balance(client, admin):
    sup = _ok(client.post("/api/partners", headers=admin, json={"name": "공급AP", "partner_type": "supplier"}))["id"]
    iid = _ok(client.post("/api/items", headers=admin, json={"name": "자재", "unit": "EA"}))["id"]
    # 매입 100@500 = 50000 (IN, 공급처 지정)
    _ok(client.post("/api/stock/movements", headers=admin,
                    json={"item_id": iid, "movement_type": "IN", "quantity": 100, "unit_price": 500, "partner_id": sup}))
    pay = _ok(client.post("/api/payments", headers=admin,
                          json={"partner_id": sup, "kind": "AP", "amount": 30000, "method": "계좌이체"}))
    assert pay["payment_no"].startswith("PAY-")
    bal = _ok(client.get(f"/api/partners/{sup}/balance", headers=admin))
    # 과세 품목 → 청구액은 VAT 포함: 공급 50000 + 세액 5000 = 55000
    assert bal["purchase_amount"] == 55000 and bal["purchase_tax"] == 5000
    assert bal["ap_paid"] == 30000
    assert bal["ap_outstanding"] == 25000
    assert bal["ar_outstanding"] == 0
    assert _ok(client.get("/api/payments", headers=admin, params={"partner_id": sup}))["total"] == 1


# ---------- 결제-문서 연결 (PO별 지급/미지급) ----------
def test_payment_linked_to_purchase_order(client, admin):
    sup = _ok(client.post("/api/partners", headers=admin, json={"name": "공급링크", "partner_type": "supplier"}))["id"]
    other = _ok(client.post("/api/partners", headers=admin, json={"name": "타사", "partner_type": "supplier"}))["id"]
    iid = _ok(client.post("/api/items", headers=admin, json={"name": "링크품", "unit": "EA"}))["id"]
    po = _ok(client.post("/api/purchase-orders", headers=admin,
                         json={"partner_id": sup, "lines": [{"item_id": iid, "qty": 10, "unit_price": 1000}]}))
    _ok(client.post(f"/api/purchase-orders/{po['id']}/confirm", headers=admin))
    _ok(client.post(f"/api/purchase-orders/{po['id']}/receive", headers=admin,
                    json={"lines": [{"line_id": po["lines"][0]["id"], "qty": 10}]}))
    # PO 에 귀속된 지급 4000. 과세라 청구액은 VAT 포함(공급 10000 + 세액 1000 = 11000)
    _ok(client.post("/api/payments", headers=admin,
                    json={"partner_id": sup, "kind": "AP", "amount": 4000, "ref_type": "PO", "ref_id": po["id"]}))
    detail = _ok(client.get(f"/api/purchase-orders/{po['id']}", headers=admin))
    assert detail["grand_total"] == 11000
    assert detail["paid_amount"] == 4000 and detail["outstanding"] == 7000
    # 발주 결제는 AP 여야 함
    assert client.post("/api/payments", headers=admin,
                       json={"partner_id": sup, "kind": "AR", "amount": 100, "ref_type": "PO", "ref_id": po["id"]}).status_code == 400
    # 문서 거래처 불일치 차단
    assert client.post("/api/payments", headers=admin,
                       json={"partner_id": other, "kind": "AP", "amount": 100, "ref_type": "PO", "ref_id": po["id"]}).status_code == 400
    # 문서로 결제 필터
    assert _ok(client.get("/api/payments", headers=admin, params={"ref_type": "PO", "ref_id": po["id"]}))["total"] == 1


# ---------- 방어 로직 회귀 (수정 검증) ----------
def test_manual_in_delete_blocked_after_later_movement(client, admin):
    """후속 입출고가 있는 수기 IN 삭제는 이동평균 오염을 막기 위해 차단된다."""
    iid = _ok(client.post("/api/items", headers=admin, json={"name": "역분개품", "unit": "EA"}))["id"]
    in1 = _ok(client.post("/api/stock/movements", headers=admin,
                          json={"item_id": iid, "movement_type": "IN", "quantity": 10, "unit_price": 100}))
    # 이후 다른 이동 발생
    _ok(client.post("/api/stock/movements", headers=admin,
                    json={"item_id": iid, "movement_type": "IN", "quantity": 10, "unit_price": 300}))
    # 첫 IN 은 이후 이동이 있어 삭제 불가(평균 오염 방지)
    assert client.delete(f"/api/stock/movements/{in1['id']}", headers=admin).status_code == 400
    # 재고·평가 불변
    val = _ok(client.get("/api/costing/valuation", headers=admin, params={"q": "역분개품"}))["items"][0]
    assert val["on_hand"] == 20 and val["avg_cost"] == 200


def test_negative_unit_price_rejected(client, admin):
    """수기 이동의 음수 단가는 422 로 거부된다(이동평균·매출 오염 방지)."""
    iid = _ok(client.post("/api/items", headers=admin, json={"name": "음수단가품", "unit": "EA"}))["id"]
    r = client.post("/api/stock/movements", headers=admin,
                    json={"item_id": iid, "movement_type": "IN", "quantity": 5, "unit_price": -100})
    assert r.status_code == 422


def test_jwt_secret_guard_rejects_placeholder():
    """.env.example 을 그대로 복사한 자리표시자 시크릿은 기동 가드에 걸린다."""
    import pytest
    from pydantic import ValidationError
    from app.config import Settings

    # 표식이 든 자리표시자 값들은 길이와 무관하게 거부
    for bad in ("please-change-this-to-a-long-random-string",
                "__REPLACE_ME__run_secrets_token_urlsafe_48",
                "short"):
        with pytest.raises(ValidationError):
            Settings(jwt_secret=bad, _env_file=None)
    # 충분히 길고 표식 없는 무작위 값은 통과
    Settings(jwt_secret="Zx9-QwErTy_bnm123-random-value-ok", _env_file=None)


def test_document_outstanding_is_delivery_based(client, admin):
    """문서 미지급(outstanding)이 주문 총액이 아니라 입고 실적(청구액) 기준으로
    산출되어 거래처 잔액과 일치한다."""
    sup = _ok(client.post("/api/partners", headers=admin, json={"name": "납품기준공급", "partner_type": "supplier"}))["id"]
    iid = _ok(client.post("/api/items", headers=admin, json={"name": "납품기준품", "unit": "EA"}))["id"]
    po = _ok(client.post("/api/purchase-orders", headers=admin,
                         json={"partner_id": sup, "lines": [{"item_id": iid, "qty": 10, "unit_price": 1000}]}))
    _ok(client.post(f"/api/purchase-orders/{po['id']}/confirm", headers=admin))
    # 10개 중 4개만 입고 → 주문공급 10000, 청구(billed, VAT 포함) 4400 = 4000 + 세액 400
    _ok(client.post(f"/api/purchase-orders/{po['id']}/receive", headers=admin,
                    json={"lines": [{"line_id": po["lines"][0]["id"], "qty": 4}]}))
    detail = _ok(client.get(f"/api/purchase-orders/{po['id']}", headers=admin))
    assert detail["total_amount"] == 10000 and detail["billed_amount"] == 4400
    assert detail["outstanding"] == 4400                       # 납품 기준·VAT 포함(주문총액 아님)
    bal = _ok(client.get(f"/api/partners/{sup}/balance", headers=admin))
    assert bal["ap_outstanding"] == detail["outstanding"]      # 거래처 잔액과 일치
    # 4400 지급 → 문서·거래처 모두 0
    _ok(client.post("/api/payments", headers=admin,
                    json={"partner_id": sup, "kind": "AP", "amount": 4400, "ref_type": "PO", "ref_id": po["id"]}))
    detail = _ok(client.get(f"/api/purchase-orders/{po['id']}", headers=admin))
    assert detail["outstanding"] == 0
    assert _ok(client.get(f"/api/partners/{sup}/balance", headers=admin))["ap_outstanding"] == 0


def test_zero_price_out_excluded_from_margin(client, admin):
    """단가 0 출고(파손·증정)는 매출총이익에 잡히지 않는다(감모가 손익 왜곡 방지)."""
    iid = _ok(client.post("/api/items", headers=admin, json={"name": "감모품", "unit": "EA"}))["id"]
    _ok(client.post("/api/stock/movements", headers=admin,
                    json={"item_id": iid, "movement_type": "IN", "quantity": 10, "unit_price": 100}))
    # 실제 판매 2개 @500
    _ok(client.post("/api/stock/movements", headers=admin,
                    json={"item_id": iid, "movement_type": "OUT", "quantity": 2, "unit_price": 500}))
    # 파손 3개 @0 — 매출로 보지 않음
    _ok(client.post("/api/stock/movements", headers=admin,
                    json={"item_id": iid, "movement_type": "OUT", "quantity": 3, "unit_price": 0}))
    m = _ok(client.get("/api/costing/margin", headers=admin))
    assert m["revenue"] == 1000 and m["cogs"] == 200
    assert m["gross_profit"] == 800 and m["sales_count"] == 1   # 0원 출고 제외


def test_login_lockout_after_repeated_failures(client):
    """같은 아이디로 실패가 누적되면 잠금(429)된다(무차별 대입 방어)."""
    for _ in range(5):
        r = client.post("/api/auth/login", data={"username": "bruteforce_target", "password": "wrong"})
        assert r.status_code == 401
    # 6번째부터는 잠금
    assert client.post("/api/auth/login",
                       data={"username": "bruteforce_target", "password": "wrong"}).status_code == 429


def test_state_transition_audit_records_before_after(client, admin):
    """발주 확정·입고 같은 상태전이가 감사 로그에 변경 전(before)/후(after)를 남긴다."""
    import json
    sup = _ok(client.post("/api/partners", headers=admin, json={"name": "감사공급", "partner_type": "supplier"}))["id"]
    iid = _ok(client.post("/api/items", headers=admin, json={"name": "감사품", "unit": "EA"}))["id"]
    po = _ok(client.post("/api/purchase-orders", headers=admin,
                         json={"partner_id": sup, "lines": [{"item_id": iid, "qty": 5, "unit_price": 100}]}))
    _ok(client.post(f"/api/purchase-orders/{po['id']}/confirm", headers=admin))
    logs = _ok(client.get("/api/audit", headers=admin,
                          params={"entity": "purchase_order", "action": "UPDATE"}))["items"]
    latest = logs[0]  # 최신순
    assert latest["before"] is not None and latest["after"] is not None
    assert json.loads(latest["before"])["status"] == "draft"
    assert json.loads(latest["after"])["status"] == "confirmed"


# ---------- 문서화된 계약 커버리지 (오류형식/페이지네이션/권한) ----------
def test_error_response_shapes(client, admin):
    """오류 응답이 문서 계약 {detail, code, (fields)} 형식을 지킨다."""
    # 401 미인증
    r = client.get("/api/partners")
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"
    # 404 not_found
    r = client.get("/api/purchase-orders/999999", headers=admin)
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    # 422 검증 오류 — 필드별 메시지 포함
    r = client.post("/api/partners", headers=admin, json={})
    body = r.json()
    assert r.status_code == 422 and body["code"] == "validation_error"
    assert isinstance(body.get("fields"), dict) and body["fields"]


def test_pagination_contract(client, admin):
    """목록이 {items,total,page,page_size,pages} 형태이고 page_size 상한은 100."""
    for _ in range(3):
        client.post("/api/partners", headers=admin, json={"name": "페이지테스트", "partner_type": "customer"})
    r = _ok(client.get("/api/partners", headers=admin, params={"page": 1, "page_size": 2}))
    assert set(r) >= {"items", "total", "page", "page_size", "pages"}
    assert r["page"] == 1 and r["page_size"] == 2 and len(r["items"]) <= 2
    assert r["pages"] == (r["total"] + 1) // 2
    # page_size 100 초과는 거부(이중 방어의 Query 검증)
    assert client.get("/api/partners", headers=admin, params={"page_size": 101}).status_code == 422


def test_payment_delete_restores_outstanding(client, admin):
    """결제 삭제 시 미지급 잔액이 원복된다(결제 DELETE 역반영)."""
    sup = _ok(client.post("/api/partners", headers=admin, json={"name": "삭제결제공급", "partner_type": "supplier"}))["id"]
    iid = _ok(client.post("/api/items", headers=admin, json={"name": "삭제결제품", "unit": "EA"}))["id"]
    _ok(client.post("/api/stock/movements", headers=admin,
                    json={"item_id": iid, "movement_type": "IN", "quantity": 100, "unit_price": 500, "partner_id": sup}))
    pay = _ok(client.post("/api/payments", headers=admin,
                          json={"partner_id": sup, "kind": "AP", "amount": 30000}))
    # 과세 품목 → 청구액 VAT 포함: 공급 50000 + 세액 5000 = 55000
    assert _ok(client.get(f"/api/partners/{sup}/balance", headers=admin))["ap_outstanding"] == 25000
    assert client.delete(f"/api/payments/{pay['id']}", headers=admin).status_code == 204
    bal = _ok(client.get(f"/api/partners/{sup}/balance", headers=admin))
    assert bal["ap_paid"] == 0 and bal["ap_outstanding"] == 55000


def test_purchase_cancel_and_delete(client, admin):
    """발주 취소(입고 전)와 draft 삭제 흐름."""
    sup = _ok(client.post("/api/partners", headers=admin, json={"name": "취소공급", "partner_type": "supplier"}))["id"]
    iid = _ok(client.post("/api/items", headers=admin, json={"name": "취소품", "unit": "EA"}))["id"]
    # 확정 후 취소
    po = _ok(client.post("/api/purchase-orders", headers=admin,
                         json={"partner_id": sup, "lines": [{"item_id": iid, "qty": 3, "unit_price": 100}]}))
    _ok(client.post(f"/api/purchase-orders/{po['id']}/confirm", headers=admin))
    assert _ok(client.post(f"/api/purchase-orders/{po['id']}/cancel", headers=admin))["status"] == "cancelled"
    # draft 삭제
    po2 = _ok(client.post("/api/purchase-orders", headers=admin,
                          json={"partner_id": sup, "lines": [{"item_id": iid, "qty": 1, "unit_price": 100}]}))
    assert client.delete(f"/api/purchase-orders/{po2['id']}", headers=admin).status_code == 204
    assert client.get(f"/api/purchase-orders/{po2['id']}", headers=admin).status_code == 404


# ---------- 부가세(VAT) — Phase 1 ----------
def test_vat_taxable_vs_exempt_and_valuation(client, admin):
    """과세 품목은 10% 세액, 면세는 0. 합계=공급가액+세액. 재고평가·COGS는 세제외."""
    sup = _ok(client.post("/api/partners", headers=admin, json={"name": "부가세공급", "partner_type": "supplier"}))["id"]
    # 과세(기본) + 면세 품목
    tx = _ok(client.post("/api/items", headers=admin, json={"name": "과세품", "unit": "EA"}))
    ex = _ok(client.post("/api/items", headers=admin, json={"name": "면세품", "unit": "EA", "tax_type": "exempt"}))
    assert tx["tax_type"] == "taxable" and ex["tax_type"] == "exempt"

    po = _ok(client.post("/api/purchase-orders", headers=admin, json={"partner_id": sup, "lines": [
        {"item_id": tx["id"], "qty": 10, "unit_price": 1000},   # 공급 10000, 세액 1000
        {"item_id": ex["id"], "qty": 5, "unit_price": 2000},    # 공급 10000, 세액 0
    ]}))
    assert po["total_amount"] == 20000 and po["tax_amount"] == 1000 and po["grand_total"] == 21000
    lines = {l["item_id"]: l for l in po["lines"]}
    assert lines[tx["id"]]["tax_amount"] == 1000 and lines[ex["id"]]["tax_amount"] == 0

    # 전량 입고 → 청구액(VAT 포함) 21000, 거래처 미지급 21000
    _ok(client.post(f"/api/purchase-orders/{po['id']}/confirm", headers=admin))
    _ok(client.post(f"/api/purchase-orders/{po['id']}/receive", headers=admin, json={"lines": [
        {"line_id": lines[tx["id"]]["id"], "qty": 10},
        {"line_id": lines[ex["id"]]["id"], "qty": 5},
    ]}))
    detail = _ok(client.get(f"/api/purchase-orders/{po['id']}", headers=admin))
    assert detail["billed_amount"] == 21000 and detail["outstanding"] == 21000
    bal = _ok(client.get(f"/api/partners/{sup}/balance", headers=admin))
    assert bal["purchase_amount"] == 21000 and bal["purchase_tax"] == 1000

    # 재고평가는 세제외(공급가액 기준): 과세품 10x1000=10000, 면세품 5x2000=10000 = 20000
    vt = _ok(client.get("/api/costing/valuation", headers=admin, params={"q": "과세품"}))["items"][0]
    ve = _ok(client.get("/api/costing/valuation", headers=admin, params={"q": "면세품"}))["items"][0]
    assert vt["value"] == 10000 and ve["value"] == 10000   # 부가세 미포함


# ---------- 세금계산서 — Phase 2 ----------
def test_tax_invoice_issue_snapshot_duplicate_cancel(client, admin):
    """발주에서 매입 세금계산서 발행: 금액·거래처·품목 스냅샷, 중복 차단, 취소 후 재발행."""
    sup = _ok(client.post("/api/partners", headers=admin,
                          json={"name": "계산서공급", "partner_type": "supplier", "business_no": "123-45-67890"}))["id"]
    it = _ok(client.post("/api/items", headers=admin, json={"name": "계산서품", "spec": "A4", "unit": "EA"}))["id"]
    po = _ok(client.post("/api/purchase-orders", headers=admin,
                         json={"partner_id": sup, "lines": [{"item_id": it, "qty": 10, "unit_price": 1000}]}))

    # draft 상태에서는 발행 불가
    assert client.post("/api/tax-invoices", headers=admin,
                       json={"ref_type": "PO", "ref_id": po["id"]}).status_code == 400
    _ok(client.post(f"/api/purchase-orders/{po['id']}/confirm", headers=admin))

    inv = _ok(client.post("/api/tax-invoices", headers=admin,
                          json={"ref_type": "PO", "ref_id": po["id"]}))
    assert inv["invoice_no"].startswith("TXI-")
    assert inv["kind"] == "purchase" and inv["status"] == "issued"
    assert inv["supply_amount"] == 10000 and inv["tax_amount"] == 1000 and inv["total_amount"] == 11000
    assert inv["partner_name"] == "계산서공급" and inv["partner_business_no"] == "123-45-67890"
    assert inv["lines"][0]["item_name"] == "계산서품" and inv["lines"][0]["spec"] == "A4"
    assert inv["lines"][0]["supply_amount"] == 10000 and inv["lines"][0]["tax_amount"] == 1000

    # 중복 발행 차단
    assert client.post("/api/tax-invoices", headers=admin,
                       json={"ref_type": "PO", "ref_id": po["id"]}).status_code == 409
    # 취소 후에는 재발행 가능
    assert _ok(client.post(f"/api/tax-invoices/{inv['id']}/cancel", headers=admin))["status"] == "cancelled"
    inv2 = _ok(client.post("/api/tax-invoices", headers=admin,
                           json={"ref_type": "PO", "ref_id": po["id"]}))
    assert inv2["id"] != inv["id"] and inv2["status"] == "issued"
    # 목록 필터(원천 문서)
    assert _ok(client.get("/api/tax-invoices", headers=admin,
                          params={"ref_type": "PO", "ref_id": po["id"]}))["total"] == 2


# ---------- 반품 — Phase 4 ----------
def _levels(client, admin, q):
    return _ok(client.get("/api/stock/levels", headers=admin, params={"q": q}))["items"][0]["on_hand"]


def test_purchase_return(client, admin):
    """매입 반품: 재고 감소·미지급 차감, 순 입고 기준 청구, 과반품 차단."""
    sup = _ok(client.post("/api/partners", headers=admin, json={"name": "반품공급", "partner_type": "supplier"}))["id"]
    iid = _ok(client.post("/api/items", headers=admin, json={"name": "매입반품품", "unit": "EA"}))["id"]
    po = _ok(client.post("/api/purchase-orders", headers=admin,
                         json={"partner_id": sup, "lines": [{"item_id": iid, "qty": 10, "unit_price": 1000}]}))
    line = po["lines"][0]["id"]
    _ok(client.post(f"/api/purchase-orders/{po['id']}/confirm", headers=admin))
    _ok(client.post(f"/api/purchase-orders/{po['id']}/receive", headers=admin, json={"lines": [{"line_id": line, "qty": 10}]}))
    assert _levels(client, admin, "매입반품품") == 10
    # 미지급(VAT 포함) 11000
    assert _ok(client.get(f"/api/partners/{sup}/balance", headers=admin))["ap_outstanding"] == 11000

    # 3개 반품 → 재고 7, 미지급 7700, 문서 순청구 7700
    r = _ok(client.post(f"/api/purchase-orders/{po['id']}/return", headers=admin, json={"lines": [{"line_id": line, "qty": 3}]}))
    assert r["lines"][0]["returned_qty"] == 3 and r["lines"][0]["returnable_qty"] == 7
    assert r["billed_amount"] == 7700 and r["outstanding"] == 7700
    assert _levels(client, admin, "매입반품품") == 7
    assert _ok(client.get(f"/api/partners/{sup}/balance", headers=admin))["ap_outstanding"] == 7700

    # 과반품 차단(남은 반품가능 7 초과)
    assert client.post(f"/api/purchase-orders/{po['id']}/return", headers=admin,
                       json={"lines": [{"line_id": line, "qty": 8}]}).status_code == 400
    assert _levels(client, admin, "매입반품품") == 7   # 실패 후 재고 불변


def test_sales_return(client, admin):
    """매출 반품: 재고 증가·미수 차감, 매출총이익 역산."""
    sup = _ok(client.post("/api/partners", headers=admin, json={"name": "반품매입처", "partner_type": "supplier"}))["id"]
    cus = _ok(client.post("/api/partners", headers=admin, json={"name": "반품고객", "partner_type": "customer"}))["id"]
    iid = _ok(client.post("/api/items", headers=admin, json={"name": "매출반품품", "unit": "EA"}))["id"]
    # 재고 20 @원가 1000 확보
    po = _ok(client.post("/api/purchase-orders", headers=admin,
                         json={"partner_id": sup, "lines": [{"item_id": iid, "qty": 20, "unit_price": 1000}]}))
    _ok(client.post(f"/api/purchase-orders/{po['id']}/confirm", headers=admin))
    _ok(client.post(f"/api/purchase-orders/{po['id']}/receive", headers=admin, json={"lines": [{"line_id": po["lines"][0]["id"], "qty": 20}]}))
    # 수주 10 @2000 출고
    so = _ok(client.post("/api/sales-orders", headers=admin,
                         json={"partner_id": cus, "lines": [{"item_id": iid, "qty": 10, "unit_price": 2000}]}))
    sline = so["lines"][0]["id"]
    _ok(client.post(f"/api/sales-orders/{so['id']}/confirm", headers=admin))
    _ok(client.post(f"/api/sales-orders/{so['id']}/ship", headers=admin, json={"lines": [{"line_id": sline, "qty": 10}]}))
    assert _levels(client, admin, "매출반품품") == 10
    assert _ok(client.get(f"/api/partners/{cus}/balance", headers=admin))["ar_outstanding"] == 22000  # 20000+세2000

    # 4개 반품 → 재고 14, 미수 13200, 매출총이익 역산
    r = _ok(client.post(f"/api/sales-orders/{so['id']}/return", headers=admin, json={"lines": [{"line_id": sline, "qty": 4}]}))
    assert r["lines"][0]["returned_qty"] == 4 and r["billed_amount"] == 13200 and r["outstanding"] == 13200
    assert _levels(client, admin, "매출반품품") == 14
    assert _ok(client.get(f"/api/partners/{cus}/balance", headers=admin))["ar_outstanding"] == 13200
    # 순매출 6개: 매출 12000, COGS 6000, 총이익 6000
    m = _ok(client.get("/api/costing/margin", headers=admin))
    assert m["revenue"] == 12000 and m["cogs"] == 6000 and m["gross_profit"] == 6000


# ---------- 마이그레이션 무결성 (SQLite 테스트는 alembic을 안 거치므로 별도 검증) ----------
def test_alembic_single_head_no_duplicate_revision():
    """마이그레이션 체인은 head가 하나이고 revision id 중복/순환이 없어야 한다.
    (revision id 충돌은 실 DB의 `alembic upgrade head`에서만 터지므로 여기서 선제 검증.)"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = script.get_heads()
    assert len(heads) == 1, f"단일 head가 아님: {heads}"
    ids = [r.revision for r in script.walk_revisions()]   # 순환이면 예외 발생
    assert len(ids) == len(set(ids)), f"중복 revision id 존재: {ids}"


# ---------- 여신한도 · Aging — Phase 5 ----------
def _stock_for(client, admin, iid, qty, price=1000):
    """공급처에서 qty 만큼 입고해 재고를 확보한다."""
    sup = _ok(client.post("/api/partners", headers=admin, json={"name": f"공급{iid}", "partner_type": "supplier"}))["id"]
    po = _ok(client.post("/api/purchase-orders", headers=admin,
                         json={"partner_id": sup, "lines": [{"item_id": iid, "qty": qty, "unit_price": price}]}))
    _ok(client.post(f"/api/purchase-orders/{po['id']}/confirm", headers=admin))
    _ok(client.post(f"/api/purchase-orders/{po['id']}/receive", headers=admin,
                    json={"lines": [{"line_id": po["lines"][0]["id"], "qty": qty}]}))


def test_credit_limit_blocks_shipment(client, admin):
    """여신한도 초과 출고는 차단되고, 수금으로 여신이 회복되면 출고된다."""
    cus = _ok(client.post("/api/partners", headers=admin,
                          json={"name": "여신고객", "partner_type": "customer", "credit_limit": 15000}))["id"]
    iid = _ok(client.post("/api/items", headers=admin, json={"name": "여신품", "unit": "EA"}))["id"]
    _stock_for(client, admin, iid, 30)

    def make_so():
        so = _ok(client.post("/api/sales-orders", headers=admin,
                             json={"partner_id": cus, "lines": [{"item_id": iid, "qty": 10, "unit_price": 1000}]}))
        _ok(client.post(f"/api/sales-orders/{so['id']}/confirm", headers=admin))
        return so
    # SO1 출고: 청구 11000 ≤ 한도 15000 → OK
    so1 = make_so()
    _ok(client.post(f"/api/sales-orders/{so1['id']}/ship", headers=admin, json={"lines": [{"line_id": so1["lines"][0]["id"], "qty": 10}]}))
    bal = _ok(client.get(f"/api/partners/{cus}/balance", headers=admin))
    assert bal["ar_outstanding"] == 11000 and bal["credit_available"] == 4000
    # SO2 출고: 11000 + 11000 = 22000 > 15000 → 차단
    so2 = make_so()
    r = client.post(f"/api/sales-orders/{so2['id']}/ship", headers=admin, json={"lines": [{"line_id": so2["lines"][0]["id"], "qty": 10}]})
    assert r.status_code == 400 and "여신한도" in r.json()["detail"]
    assert _levels(client, admin, "여신품") == 20   # 차단됐으니 재고 불변(30-10)
    # 11000 수금 → 미수 0, 여신 회복 → SO2 출고 가능
    _ok(client.post("/api/payments", headers=admin, json={"partner_id": cus, "kind": "AR", "amount": 11000}))
    _ok(client.post(f"/api/sales-orders/{so2['id']}/ship", headers=admin, json={"lines": [{"line_id": so2["lines"][0]["id"], "qty": 10}]}))
    assert _levels(client, admin, "여신품") == 10


def test_aging_fifo_and_credit(client, admin):
    """Aging(AR): 미결제 청구가 경과구간에 잡히고, 수금은 오래된 청구부터 상계된다."""
    cus = _ok(client.post("/api/partners", headers=admin, json={"name": "연령고객", "partner_type": "customer"}))["id"]
    iid = _ok(client.post("/api/items", headers=admin, json={"name": "연령품", "unit": "EA"}))["id"]
    _stock_for(client, admin, iid, 20)
    so = _ok(client.post("/api/sales-orders", headers=admin,
                         json={"partner_id": cus, "lines": [{"item_id": iid, "qty": 10, "unit_price": 1000}]}))
    _ok(client.post(f"/api/sales-orders/{so['id']}/confirm", headers=admin))
    _ok(client.post(f"/api/sales-orders/{so['id']}/ship", headers=admin, json={"lines": [{"line_id": so["lines"][0]["id"], "qty": 10}]}))

    def row_for(cid):
        rep = _ok(client.get("/api/reports/aging", headers=admin, params={"kind": "AR"}))
        return next((b for b in rep["buckets"] if b["partner_id"] == cid), None)

    b = row_for(cus)
    assert b and b["total"] == 11000 and b["b_0_30"] == 11000  # 오늘 청구 → 현재 구간
    # 4000 수금 → 미결제 7000
    _ok(client.post("/api/payments", headers=admin, json={"partner_id": cus, "kind": "AR", "amount": 4000}))
    b = row_for(cus)
    assert b and b["total"] == 7000 and b["b_0_30"] == 7000
    # 전액 수금 → aging 에서 사라짐
    _ok(client.post("/api/payments", headers=admin, json={"partner_id": cus, "kind": "AR", "amount": 7000}))
    assert row_for(cus) is None


# ---------- 관측성(request_id · 메트릭) — Phase 6 ----------
def test_request_id_roundtrip(client):
    """X-Request-ID: 들어온 값은 그대로 되돌려주고, 없으면 서버가 생성해 준다."""
    r = client.get("/health", headers={"X-Request-ID": "corr-test-123"})
    assert r.headers["X-Request-ID"] == "corr-test-123"
    r = client.get("/health")
    assert r.headers.get("X-Request-ID")  # UUID4 자동 생성


def test_error_response_includes_request_id(client):
    """오류 응답 본문의 request_id 가 응답 헤더와 일치해 지원 문의 시 상관 가능."""
    r = client.get("/api/partners", headers={"X-Request-ID": "corr-err-1"})  # 미인증 → 401
    assert r.status_code == 401
    body = r.json()
    assert body["request_id"] == "corr-err-1" == r.headers["X-Request-ID"]
    # 검증 오류(422)에도 request_id 포함
    r = client.post("/api/auth/login", data={})
    assert r.status_code == 422 and r.json()["request_id"] == r.headers["X-Request-ID"]


def test_metrics_endpoint(client):
    """/metrics: 인메모리 카운터가 총 요청수·상태코드대별로 노출된다(text/plain)."""
    client.get("/health")
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "http_requests_total " in r.text
    assert 'http_requests{class="2xx"}' in r.text
