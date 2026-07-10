"""Phase 3 다중창고 회귀 테스트 (SQLite).

검증하는 것:
- 같은 품목이라도 창고별로 on_hand·avg_cost 가 독립이다(창고별 이동평균).
- 창고간 이전(TRANSFER)이 총수량을 보존하고 출고창고 평균원가로 입고창고
  이동평균을 갱신한다(회사 전체 재고평가액 보존).
- 이전 시 출고창고 재고부족 차단, 창고별 음수재고 차단.
- TRANSFER 는 매입/매출 통계·aging·매출총이익에 잡히지 않는다.
- 창고 미지정 기존 호출은 기본창고로 동작한다(하위호환).
"""


def _ok(r):
    assert r.status_code < 400, f"{r.status_code} {r.text}"
    return r.json()


def _default_wh(client, admin) -> dict:
    ws = _ok(client.get("/api/warehouses", headers=admin))["items"]
    return next(w for w in ws if w["is_default"])


def _make_wh(client, admin, name="B창고") -> dict:
    return _ok(client.post("/api/warehouses", headers=admin, json={"name": name}))


def _make_item(client, admin, name) -> int:
    return _ok(client.post("/api/items", headers=admin, json={"name": name, "unit": "EA"}))["id"]


def _in(client, admin, iid, qty, price, wid=None, partner_id=None):
    body = {"item_id": iid, "movement_type": "IN", "quantity": qty, "unit_price": price}
    if wid is not None:
        body["warehouse_id"] = wid
    if partner_id is not None:
        body["partner_id"] = partner_id
    return _ok(client.post("/api/stock/movements", headers=admin, json=body))


def _level(client, admin, q, wid=None):
    params = {"q": q}
    if wid is not None:
        params["warehouse_id"] = wid
    return _ok(client.get("/api/stock/levels", headers=admin, params=params))["items"][0]["on_hand"]


def _valuation(client, admin, q, wid=None):
    params = {"q": q, "in_stock_only": False}
    if wid is not None:
        params["warehouse_id"] = wid
    items = _ok(client.get("/api/costing/valuation", headers=admin, params=params))["items"]
    return items[0] if items else None


# ---------- 창고 마스터 CRUD + 기본창고 불변식 ----------
def test_warehouse_crud_and_default_invariant(client, admin):
    main = _default_wh(client, admin)
    assert main["code"] == "MAIN" and main["is_default"] is True

    # 생성(코드 자동 채번) — 신규 창고는 기본창고가 아니다
    b = _make_wh(client, admin, "제2창고")
    assert b["code"].startswith("WH-") and b["is_default"] is False
    # 코드 중복 차단
    assert client.post("/api/warehouses", headers=admin,
                       json={"code": "MAIN", "name": "중복"}).status_code == 409
    # 수정(이름)
    upd = _ok(client.put(f"/api/warehouses/{b['id']}", headers=admin, json={"name": "제2창고-개명"}))
    assert upd["name"] == "제2창고-개명"
    # 기본창고 직접 해제 불가(교체만 허용)
    assert client.put(f"/api/warehouses/{main['id']}", headers=admin,
                      json={"is_default": False}).status_code == 400
    # 기본창고 비활성화 불가
    assert client.put(f"/api/warehouses/{main['id']}", headers=admin,
                      json={"is_active": False}).status_code == 400
    # 기본창고 교체 → 정확히 1개 유지
    _ok(client.put(f"/api/warehouses/{b['id']}", headers=admin, json={"is_default": True}))
    ws = _ok(client.get("/api/warehouses", headers=admin))["items"]
    assert [w["id"] for w in ws if w["is_default"]] == [b["id"]]


# ---------- (a)(d) 창고별 독립 잔고·이동평균 + 창고별 음수재고 차단 ----------
def test_per_warehouse_balance_and_avg_cost_independent(client, admin):
    main = _default_wh(client, admin)
    b = _make_wh(client, admin)
    iid = _make_item(client, admin, "창고독립품")

    _in(client, admin, iid, 10, 100)              # 기본창고(미지정) 10 @100
    _in(client, admin, iid, 10, 300, wid=b["id"])  # B창고 10 @300

    # 창고별 잔고 독립
    assert _level(client, admin, "창고독립품", main["id"]) == 10
    assert _level(client, admin, "창고독립품", b["id"]) == 10
    assert _level(client, admin, "창고독립품") == 20   # 미지정 = 전 창고 합산

    # 창고별 이동평균 독립
    vm = _valuation(client, admin, "창고독립품", main["id"])
    vb = _valuation(client, admin, "창고독립품", b["id"])
    vt = _valuation(client, admin, "창고독립품")
    assert vm["avg_cost"] == 100 and vm["value"] == 1000
    assert vb["avg_cost"] == 300 and vb["value"] == 3000
    assert vt["on_hand"] == 20 and vt["value"] == 4000 and vt["avg_cost"] == 200  # 가중평균

    # (d) 창고별 음수재고 차단: 전사 재고는 20이지만 각 창고엔 10뿐
    for wid in (main["id"], b["id"]):
        r = client.post("/api/stock/movements", headers=admin,
                        json={"item_id": iid, "movement_type": "OUT",
                              "quantity": 11, "warehouse_id": wid})
        assert r.status_code == 400
    # 실패 후 잔고 불변
    assert _level(client, admin, "창고독립품") == 20


# ---------- (b) 이전: 총수량 보존 + 출고평균 → 입고 이동평균 갱신(원가 보존) ----------
def test_transfer_preserves_quantity_and_cost(client, admin):
    main = _default_wh(client, admin)
    b = _make_wh(client, admin)
    iid = _make_item(client, admin, "이전원가품")

    _in(client, admin, iid, 10, 100)               # 기본창고 10 @100
    _in(client, admin, iid, 10, 300, wid=b["id"])   # B창고 10 @300
    before_total = _ok(client.get("/api/costing/valuation/summary", headers=admin))["total_value"]

    # 기본창고 → B창고 10개 이전. 이전 단가 = 출고창고 이동평균(100)
    t = _ok(client.post("/api/stock/transfer", headers=admin, json={
        "item_id": iid, "from_warehouse_id": main["id"],
        "to_warehouse_id": b["id"], "quantity": 10, "note": "재배치",
    }))
    assert t["cost"] == 100
    assert t["out_movement_no"] != t["in_movement_no"]

    # 총수량 보존: 0 + 20
    assert _level(client, admin, "이전원가품", main["id"]) == 0
    assert _level(client, admin, "이전원가품", b["id"]) == 20
    assert _level(client, admin, "이전원가품") == 20

    # 입고창고 이동평균 = (10x300 + 10x100) / 20 = 200 (출고창고 평균이 단가로 유입)
    vb = _valuation(client, admin, "이전원가품", b["id"])
    assert vb["avg_cost"] == 200 and vb["value"] == 4000
    # 회사 전체 재고평가액 불변(원가 보존)
    after_total = _ok(client.get("/api/costing/valuation/summary", headers=admin))["total_value"]
    assert after_total == before_total

    # TRANSFER 페어 2행(출고 음수/입고 양수) 기록 확인
    mv = _ok(client.get("/api/stock/movements", headers=admin,
                        params={"item_id": iid, "movement_type": "TRANSFER"}))["items"]
    assert sorted(m["quantity"] for m in mv) == [-10, 10]
    assert {m["warehouse_id"] for m in mv} == {main["id"], b["id"]}
    # 문서성 이동이라 개별 삭제 불가(페어 한쪽만 지워 정합이 깨지는 것 방지)
    assert client.delete(f"/api/stock/movements/{mv[0]['id']}", headers=admin).status_code == 400


# ---------- (c) 이전 가드: 재고부족·동일창고·수량 검증 ----------
def test_transfer_guards(client, admin):
    main = _default_wh(client, admin)
    b = _make_wh(client, admin)
    iid = _make_item(client, admin, "이전가드품")
    _in(client, admin, iid, 5, 100)

    def transfer(qty, src, dst):
        return client.post("/api/stock/transfer", headers=admin, json={
            "item_id": iid, "from_warehouse_id": src, "to_warehouse_id": dst, "quantity": qty,
        })

    # 출고창고 재고부족 차단
    assert transfer(6, main["id"], b["id"]).status_code == 400
    # 빈 창고에서의 이전도 차단
    assert transfer(1, b["id"], main["id"]).status_code == 400
    # 동일 창고 이전 차단
    assert transfer(1, main["id"], main["id"]).status_code == 400
    # 수량 0/음수는 스키마에서 422
    assert transfer(0, main["id"], b["id"]).status_code == 422
    # 없는 창고 404
    assert transfer(1, main["id"], 99999).status_code == 404
    # 실패들 이후 잔고 불변
    assert _level(client, admin, "이전가드품", main["id"]) == 5
    assert _level(client, admin, "이전가드품", b["id"]) == 0


# ---------- (e) TRANSFER 는 매입/매출 통계·aging·마진에 잡히지 않는다 ----------
def test_transfer_excluded_from_stats_and_aging(client, admin):
    main = _default_wh(client, admin)
    b = _make_wh(client, admin)
    sup = _ok(client.post("/api/partners", headers=admin,
                          json={"name": "이전통계공급", "partner_type": "supplier"}))["id"]
    cus = _ok(client.post("/api/partners", headers=admin,
                          json={"name": "이전통계고객", "partner_type": "customer"}))["id"]
    iid = _make_item(client, admin, "이전통계품")

    # 매입 10@1000(AP 발생) + 매출 5@2000(AR 발생)
    _in(client, admin, iid, 10, 1000, partner_id=sup)
    _ok(client.post("/api/stock/movements", headers=admin,
                    json={"item_id": iid, "movement_type": "OUT", "quantity": 5,
                          "unit_price": 2000, "partner_id": cus}))

    def snapshot():
        return {
            "summary": _ok(client.get("/api/reports/transactions/summary", headers=admin)),
            "txn_total": _ok(client.get("/api/reports/transactions", headers=admin))["total"],
            "aging_ar": _ok(client.get("/api/reports/aging", headers=admin, params={"kind": "AR"})),
            "aging_ap": _ok(client.get("/api/reports/aging", headers=admin, params={"kind": "AP"})),
            "margin": _ok(client.get("/api/costing/margin", headers=admin)),
            "balance": _ok(client.get(f"/api/partners/{sup}/balance", headers=admin)),
        }

    before = snapshot()
    assert before["summary"]["purchase_amount"] >= 10000  # 시드 샘플 입고 포함 가능
    assert before["aging_ar"]["total"] == 11000           # 5x2000 + 세액 1000

    # 이전 3개 → 어떤 매입/매출·AP/AR 지표도 변하면 안 된다
    _ok(client.post("/api/stock/transfer", headers=admin, json={
        "item_id": iid, "from_warehouse_id": main["id"],
        "to_warehouse_id": b["id"], "quantity": 3,
    }))
    assert snapshot() == before

    # 매입/매출 내역 목록에도 TRANSFER 행이 없다
    txns = _ok(client.get("/api/reports/transactions", headers=admin, params={"page_size": 100}))
    assert all(t["kind"] in ("purchase", "sales") for t in txns["items"])


# ---------- 하위호환: 창고 미지정 호출은 기본창고 ----------
def test_movement_defaults_to_default_warehouse(client, admin):
    main = _default_wh(client, admin)
    iid = _make_item(client, admin, "하위호환품")
    m = _in(client, admin, iid, 3, 100)   # warehouse_id 미지정
    assert m["warehouse_id"] == main["id"]
    assert m["warehouse_name"] == main["name"]
    # 미지정 OUT 도 기본창고에서 차감
    _ok(client.post("/api/stock/movements", headers=admin,
                    json={"item_id": iid, "movement_type": "OUT", "quantity": 1}))
    assert _level(client, admin, "하위호환품", main["id"]) == 2
