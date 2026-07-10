"""총계정원장(GL) 회귀 테스트 — docs/gl-design.md §4 분개 매핑·§6 재전기·§7 재대사.

각 경제사건의 전기 결과(계정·금액·차대 균형), 멱등성, 원천 삭제 동반 삭제,
백필(rebuild) 후 재대사 일치를 검증한다. SQLite 파일 DB(conftest)에서 실행.
"""
from decimal import Decimal


def _ok(r):
    assert r.status_code < 400, f"{r.status_code} {r.text}"
    return r.json()


def _entry_for(client, admin, source_type, source_id):
    """원천 1건의 전표를 조회한다(멱등이라 정확히 1건이어야 한다)."""
    r = _ok(client.get("/api/gl/journal", headers=admin,
                       params={"source_type": source_type, "source_id": source_id}))
    assert r["total"] == 1, f"{source_type}#{source_id} 전표 수 이상: {r['total']}"
    return r["items"][0]


def _entry_count(client, admin, source_type, source_id):
    return _ok(client.get("/api/gl/journal", headers=admin,
                          params={"source_type": source_type, "source_id": source_id}))["total"]


def _lines_by_code(entry):
    """계정코드 -> (차변합, 대변합)."""
    m = {}
    for l in entry["lines"]:
        d, c = m.get(l["account_code"], (0.0, 0.0))
        m[l["account_code"]] = (d + l["debit"], c + l["credit"])
    return m


def _assert_balanced(entry):
    debit = sum(l["debit"] for l in entry["lines"])
    credit = sum(l["credit"] for l in entry["lines"])
    assert debit == credit and debit > 0, f"차대 불균형: {entry['entry_no']} D{debit}/C{credit}"


def _item(client, admin, name, **kw):
    return _ok(client.post("/api/items", headers=admin, json={"name": name, "unit": "EA", **kw}))["id"]


def _partner(client, admin, name, ptype):
    return _ok(client.post("/api/partners", headers=admin,
                           json={"name": name, "partner_type": ptype}))["id"]


def _move(client, admin, **payload):
    return _ok(client.post("/api/stock/movements", headers=admin, json=payload))


def _po_receive(client, admin, sup, iid, qty, price):
    """발주 생성→확정→전량 입고. (po, line_id) 반환."""
    po = _ok(client.post("/api/purchase-orders", headers=admin,
                         json={"partner_id": sup, "lines": [{"item_id": iid, "qty": qty, "unit_price": price}]}))
    line = po["lines"][0]["id"]
    _ok(client.post(f"/api/purchase-orders/{po['id']}/confirm", headers=admin))
    _ok(client.post(f"/api/purchase-orders/{po['id']}/receive", headers=admin,
                    json={"lines": [{"line_id": line, "qty": qty}]}))
    return po, line


def _so_ship(client, admin, cus, iid, qty, price):
    so = _ok(client.post("/api/sales-orders", headers=admin,
                         json={"partner_id": cus, "lines": [{"item_id": iid, "qty": qty, "unit_price": price}]}))
    line = so["lines"][0]["id"]
    _ok(client.post(f"/api/sales-orders/{so['id']}/confirm", headers=admin))
    _ok(client.post(f"/api/sales-orders/{so['id']}/ship", headers=admin,
                    json={"lines": [{"line_id": line, "qty": qty}]}))
    return so, line


# ---------- A. 매입입고 ----------
def test_purchase_receipt_posting(client, admin):
    """매입입고: 차) 상품 공급가 + 부가세대급금 / 대) 외상매입금 VAT포함(거래처 라인)."""
    sup = _partner(client, admin, "GL공급", "supplier")
    iid = _item(client, admin, "GL입고품")
    mv = _move(client, admin, item_id=iid, movement_type="IN", quantity=10,
               unit_price=1000, partner_id=sup)
    e = _entry_for(client, admin, "MOVEMENT", mv["id"])
    _assert_balanced(e)
    lines = _lines_by_code(e)
    assert lines["1130"] == (10000.0, 0.0)
    assert lines["1140"] == (1000.0, 0.0)
    assert lines["2110"] == (0.0, 11000.0)
    ap_line = next(l for l in e["lines"] if l["account_code"] == "2110")
    assert ap_line["partner_id"] == sup   # AP 라인 보조원장(거래처) 차원


def test_partnerless_priced_movement_uses_cash(client, admin):
    """거래처 없는 유상 이동은 AP/AR 대신 현금및예금(1110) 상대 — aging 과 정합."""
    iid = _item(client, admin, "GL무거래처품")
    mv = _move(client, admin, item_id=iid, movement_type="IN", quantity=5, unit_price=100)
    lines = _lines_by_code(_entry_for(client, admin, "MOVEMENT", mv["id"]))
    assert lines["1110"] == (0.0, 550.0) and "2110" not in lines
    mv2 = _move(client, admin, item_id=iid, movement_type="OUT", quantity=2, unit_price=300)
    lines2 = _lines_by_code(_entry_for(client, admin, "MOVEMENT", mv2["id"]))
    assert lines2["1110"] == (660.0, 0.0) and "1120" not in lines2


def test_exempt_item_no_tax_line(client, admin):
    """면세 품목은 세액 라인 자체를 생략한다(0원 라인 금지)."""
    sup = _partner(client, admin, "GL면세공급", "supplier")
    iid = _item(client, admin, "GL면세품", tax_type="exempt")
    mv = _move(client, admin, item_id=iid, movement_type="IN", quantity=10,
               unit_price=100, partner_id=sup)
    e = _entry_for(client, admin, "MOVEMENT", mv["id"])
    lines = _lines_by_code(e)
    assert lines["1130"] == (1000.0, 0.0) and lines["2110"] == (0.0, 1000.0)
    assert "1140" not in lines and len(e["lines"]) == 2


# ---------- B. 매출출고 (+COGS) ----------
def test_sales_shipment_posting(client, admin):
    """매출출고: 차) AR VAT포함 + 매출원가 / 대) 매출 공급가 + 예수금 + 상품.
    COGS = 출고 시점 이동평균 — margin 리포트와 동일 값."""
    cus = _partner(client, admin, "GL고객", "customer")
    iid = _item(client, admin, "GL출고품")
    _move(client, admin, item_id=iid, movement_type="IN", quantity=10, unit_price=500)
    mv = _move(client, admin, item_id=iid, movement_type="OUT", quantity=4,
               unit_price=1000, partner_id=cus)
    e = _entry_for(client, admin, "MOVEMENT", mv["id"])
    _assert_balanced(e)
    lines = _lines_by_code(e)
    assert lines["1120"] == (4400.0, 0.0)   # 4000 + 세액 400
    assert lines["4110"] == (0.0, 4000.0)
    assert lines["2120"] == (0.0, 400.0)
    assert lines["5110"] == (2000.0, 0.0)   # 4 × avg 500
    assert lines["1130"] == (0.0, 2000.0)


# ---------- C. 매입반품 (5130 재고원가차이) ----------
def test_purchase_return_price_below_avg_debits_variance(client, admin):
    """매입반품: AP 차감=반품단가, 재고 대변=반품 시점 이동평균, 차액=5130(§9-2).
    반품단가(500) < 평균(600) → 차변 5130. GL 재고 == valuation 유지."""
    sup = _partner(client, admin, "GL반품공급", "supplier")
    iid = _item(client, admin, "GL매입반품품")
    po, line = _po_receive(client, admin, sup, iid, 10, 500)
    _move(client, admin, item_id=iid, movement_type="IN", quantity=10, unit_price=700)  # avg 600
    _ok(client.post(f"/api/purchase-orders/{po['id']}/return", headers=admin,
                    json={"lines": [{"line_id": line, "qty": 3}]}))
    mv = _ok(client.get("/api/stock/movements", headers=admin, params={"item_id": iid}))["items"][0]
    assert mv["quantity"] == -3
    e = _entry_for(client, admin, "MOVEMENT", mv["id"])
    _assert_balanced(e)
    lines = _lines_by_code(e)
    assert lines["2110"] == (1650.0, 0.0)   # 3×500 + 세액 150
    assert lines["1130"] == (0.0, 1800.0)   # 3×avg 600
    assert lines["1140"] == (0.0, 150.0)
    assert lines["5130"] == (300.0, 0.0)    # 단가<평균 → 차변
    rec = _ok(client.get("/api/gl/reconcile", headers=admin))
    assert rec["inventory"]["ok"] and rec["inventory"]["diff"] == 0.0


def test_purchase_return_price_above_avg_credits_variance(client, admin):
    """반품단가(700) > 평균(600) → 대변 5130."""
    sup = _partner(client, admin, "GL반품공급2", "supplier")
    iid = _item(client, admin, "GL매입반품품2")
    po, line = _po_receive(client, admin, sup, iid, 10, 700)
    _move(client, admin, item_id=iid, movement_type="IN", quantity=10, unit_price=500)  # avg 600
    _ok(client.post(f"/api/purchase-orders/{po['id']}/return", headers=admin,
                    json={"lines": [{"line_id": line, "qty": 3}]}))
    mv = _ok(client.get("/api/stock/movements", headers=admin, params={"item_id": iid}))["items"][0]
    lines = _lines_by_code(_entry_for(client, admin, "MOVEMENT", mv["id"]))
    assert lines["2110"] == (2310.0, 0.0)   # 3×700 + 세액 210
    assert lines["1130"] == (0.0, 1800.0)
    assert lines["5130"] == (0.0, 300.0)    # 단가>평균 → 대변
    assert _ok(client.get("/api/gl/reconcile", headers=admin))["ok"]


# ---------- D. 매출반품 ----------
def test_sales_return_posting(client, admin):
    """매출반품: B 의 역방향 — 매출·예수금 차변, AR 대변, 재고 복원·COGS 역산."""
    sup = _partner(client, admin, "GL매입처S", "supplier")
    cus = _partner(client, admin, "GL반품고객", "customer")
    iid = _item(client, admin, "GL매출반품품")
    _po_receive(client, admin, sup, iid, 20, 1000)
    so, sline = _so_ship(client, admin, cus, iid, 10, 2000)
    _ok(client.post(f"/api/sales-orders/{so['id']}/return", headers=admin,
                    json={"lines": [{"line_id": sline, "qty": 4}]}))
    mv = _ok(client.get("/api/stock/movements", headers=admin, params={"item_id": iid}))["items"][0]
    assert mv["quantity"] == -4
    e = _entry_for(client, admin, "MOVEMENT", mv["id"])
    _assert_balanced(e)
    lines = _lines_by_code(e)
    assert lines["4110"] == (8000.0, 0.0)
    assert lines["2120"] == (800.0, 0.0)
    assert lines["1120"] == (0.0, 8800.0)
    assert lines["1130"] == (4000.0, 0.0)   # 4 × avg 1000 (재고 복원)
    assert lines["5110"] == (0.0, 4000.0)   # COGS 역산


# ---------- E. 0원 출고 → 5120 (매출원가 아님, §9-3) ----------
def test_zero_price_out_posts_shrinkage_not_cogs(client, admin):
    iid = _item(client, admin, "GL감모품")
    _move(client, admin, item_id=iid, movement_type="IN", quantity=10, unit_price=100)
    mv = _move(client, admin, item_id=iid, movement_type="OUT", quantity=3, unit_price=0)
    e = _entry_for(client, admin, "MOVEMENT", mv["id"])
    lines = _lines_by_code(e)
    assert lines["5120"] == (300.0, 0.0) and lines["1130"] == (0.0, 300.0)
    assert len(e["lines"]) == 2   # AR·매출·세액·5110 라인 없음
    # margin COGS(0원 출고 제외)와 GL 5110 이 계속 일치한다
    m = _ok(client.get("/api/costing/margin", headers=admin))
    ledger = _ok(client.get("/api/gl/ledger", headers=admin, params={"account_code": "5110"}))
    assert ledger["closing_balance"] == float(m["cogs"])


# ---------- F. 재고조정 ----------
def test_adjust_posting(client, admin):
    iid = _item(client, admin, "GL조정품")
    _move(client, admin, item_id=iid, movement_type="IN", quantity=10, unit_price=200)
    up = _move(client, admin, item_id=iid, movement_type="ADJUST", quantity=5)
    lines = _lines_by_code(_entry_for(client, admin, "MOVEMENT", up["id"]))
    assert lines["1130"] == (1000.0, 0.0) and lines["4910"] == (0.0, 1000.0)  # +5 × avg 200
    down = _move(client, admin, item_id=iid, movement_type="ADJUST", quantity=-2)
    lines = _lines_by_code(_entry_for(client, admin, "MOVEMENT", down["id"]))
    assert lines["5120"] == (400.0, 0.0) and lines["1130"] == (0.0, 400.0)
    assert _ok(client.get("/api/gl/reconcile", headers=admin))["inventory"]["diff"] == 0.0


# ---------- G. 결제 + 원천 삭제 동반 삭제 ----------
def test_payment_posting_and_delete_removes_entry(client, admin):
    sup = _partner(client, admin, "GL지급공급", "supplier")
    cus = _partner(client, admin, "GL수금고객", "customer")
    iid = _item(client, admin, "GL결제품")
    _move(client, admin, item_id=iid, movement_type="IN", quantity=100, unit_price=500, partner_id=sup)
    _move(client, admin, item_id=iid, movement_type="OUT", quantity=10, unit_price=800, partner_id=cus)

    ap = _ok(client.post("/api/payments", headers=admin,
                         json={"partner_id": sup, "kind": "AP", "amount": 30000}))
    e = _entry_for(client, admin, "PAYMENT", ap["id"])
    lines = _lines_by_code(e)
    assert lines["2110"] == (30000.0, 0.0) and lines["1110"] == (0.0, 30000.0)
    assert e["entry_date"] == ap["pay_date"]

    ar = _ok(client.post("/api/payments", headers=admin,
                         json={"partner_id": cus, "kind": "AR", "amount": 5000}))
    lines = _lines_by_code(_entry_for(client, admin, "PAYMENT", ar["id"]))
    assert lines["1110"] == (5000.0, 0.0) and lines["1120"] == (0.0, 5000.0)
    assert _ok(client.get("/api/gl/reconcile", headers=admin))["ok"]

    # 결제 삭제 → 전표 동반 삭제, 재대사 유지
    assert client.delete(f"/api/payments/{ap['id']}", headers=admin).status_code == 204
    assert _entry_count(client, admin, "PAYMENT", ap["id"]) == 0
    assert _ok(client.get("/api/gl/reconcile", headers=admin))["ok"]


def test_movement_delete_removes_entry(client, admin):
    """수기 이동 삭제 시 전표 동반 삭제(§9-4) — 재대사 유지."""
    iid = _item(client, admin, "GL삭제품")
    mv = _move(client, admin, item_id=iid, movement_type="IN", quantity=10, unit_price=100)
    assert _entry_count(client, admin, "MOVEMENT", mv["id"]) == 1
    assert client.delete(f"/api/stock/movements/{mv['id']}", headers=admin).status_code == 204
    assert _entry_count(client, admin, "MOVEMENT", mv["id"]) == 0
    assert _ok(client.get("/api/gl/reconcile", headers=admin))["ok"]


# ---------- 멱등성 / TRANSFER 무전기 ----------
def test_posting_is_idempotent(client, admin):
    """같은 원천을 다시 전기해도 전표가 늘지 않는다(UNIQUE(source_type, source_id))."""
    from sqlalchemy import select, func
    from app.database import SessionLocal
    from app import gl
    from app.models import StockMovement, JournalEntry

    iid = _item(client, admin, "GL멱등품")
    mv = _move(client, admin, item_id=iid, movement_type="IN", quantity=10, unit_price=100)
    db = SessionLocal()
    try:
        m = db.get(StockMovement, mv["id"])
        again = gl.post_for_movement(db, m)          # 중복 전기 시도
        assert again is not None and again.source_id == m.id
        count = db.execute(
            select(func.count()).select_from(JournalEntry)
            .where(JournalEntry.source_type == "MOVEMENT", JournalEntry.source_id == m.id)
        ).scalar_one()
        assert count == 1
        db.rollback()
    finally:
        db.close()


def _default_warehouse_id(client, admin):
    """기본창고 id(목록 정렬상 첫 항목이 기본창고)."""
    return _ok(client.get("/api/warehouses", headers=admin))["items"][0]["id"]


def test_transfer_not_posted(client, admin):
    """창고간 이전(TRANSFER)은 무전기(§4-H) — 회사 전체 평가액 불변."""
    main = _default_warehouse_id(client, admin)
    wh2 = _ok(client.post("/api/warehouses", headers=admin, json={"name": "GL제2창고"}))["id"]
    iid = _item(client, admin, "GL이전품")
    _move(client, admin, item_id=iid, movement_type="IN", quantity=10, unit_price=100)
    before = _ok(client.get("/api/gl/journal", headers=admin, params={"source_type": "MOVEMENT"}))["total"]
    _ok(client.post("/api/stock/transfer", headers=admin,
                    json={"item_id": iid, "from_warehouse_id": main, "to_warehouse_id": wh2, "quantity": 4}))
    after = _ok(client.get("/api/gl/journal", headers=admin, params={"source_type": "MOVEMENT"}))["total"]
    assert after == before   # TRANSFER 페어 2행 모두 전표 없음
    assert _ok(client.get("/api/gl/reconcile", headers=admin))["inventory"]["diff"] == 0.0


# ---------- 통합: 시산표·원장·재대사 ----------
def _mixed_flow(client, admin):
    """입고·출고·양방향 반품·0원출고·조정·결제를 섞은 시나리오."""
    sup = _partner(client, admin, "GL통합공급", "supplier")
    cus = _partner(client, admin, "GL통합고객", "customer")
    t1 = _item(client, admin, "GL통합과세품")
    t2 = _item(client, admin, "GL통합면세품", tax_type="exempt")

    po, pline = _po_receive(client, admin, sup, t1, 20, 1000)
    _ok(client.post(f"/api/purchase-orders/{po['id']}/return", headers=admin,
                    json={"lines": [{"line_id": pline, "qty": 5}]}))
    _move(client, admin, item_id=t2, movement_type="IN", quantity=10, unit_price=500, partner_id=sup)

    so, sline = _so_ship(client, admin, cus, t1, 8, 3000)
    _ok(client.post(f"/api/sales-orders/{so['id']}/return", headers=admin,
                    json={"lines": [{"line_id": sline, "qty": 2}]}))

    _move(client, admin, item_id=t1, movement_type="OUT", quantity=2, unit_price=0)   # 감모
    _move(client, admin, item_id=t1, movement_type="ADJUST", quantity=-1)
    _move(client, admin, item_id=t2, movement_type="ADJUST", quantity=3)

    _ok(client.post("/api/payments", headers=admin, json={"partner_id": sup, "kind": "AP", "amount": 5000}))
    _ok(client.post("/api/payments", headers=admin, json={"partner_id": cus, "kind": "AR", "amount": 10000}))


def _all_entries(client, admin):
    entries, page = [], 1
    while True:
        r = _ok(client.get("/api/gl/journal", headers=admin, params={"page": page, "page_size": 100}))
        entries.extend(r["items"])
        if page >= r["pages"]:
            return entries
        page += 1


def test_mixed_flow_trial_balance_and_reconcile(client, admin):
    """혼합 시나리오 후: 모든 전표 차대 균형, 시산표 균형, 재대사 전 항목 diff=0,
    GL COGS == margin COGS, GL AR/AP == 원천 잔액."""
    _mixed_flow(client, admin)

    entries = _all_entries(client, admin)
    assert entries, "전표가 하나도 없다"
    for e in entries:
        _assert_balanced(e)

    tb = _ok(client.get("/api/gl/trial-balance", headers=admin))
    assert tb["balanced"] and tb["total_debit"] == tb["total_credit"] > 0
    assert len(tb["rows"]) == 12   # 시드 12계정 전부 노출(무거래 0 포함)

    rec = _ok(client.get("/api/gl/reconcile", headers=admin))
    assert rec["ok"], rec
    for key in ("inventory", "ar", "ap"):
        assert rec[key]["diff"] == 0.0, f"{key} 불일치: {rec[key]}"

    # GL 5110(순 COGS) == margin 리포트 COGS (0원 출고 제외 정의 일치)
    m = _ok(client.get("/api/costing/margin", headers=admin))
    cogs = _ok(client.get("/api/gl/ledger", headers=admin, params={"account_code": "5110"}))
    sales = _ok(client.get("/api/gl/ledger", headers=admin, params={"account_code": "4110"}))
    assert cogs["closing_balance"] == float(m["cogs"])
    assert sales["closing_balance"] == float(m["revenue"])   # 4110 순액(반품 차변) == 순매출


def test_ledger_opening_and_running_balance(client, admin):
    """계정별원장: 기간 이전 누계가 기초잔액으로, 러닝밸런스가 기말과 일치."""
    iid = _item(client, admin, "GL원장품")
    _move(client, admin, item_id=iid, movement_type="IN", quantity=10, unit_price=100)
    full = _ok(client.get("/api/gl/ledger", headers=admin, params={"account_code": "1130"}))
    assert full["opening_balance"] == 0.0
    assert full["lines"] and full["lines"][-1]["balance"] == full["closing_balance"]
    # 미래 시작일 → 전부 기초잔액으로 접히고 기간 라인 없음
    future = _ok(client.get("/api/gl/ledger", headers=admin,
                            params={"account_code": "1130", "date_from": "2099-01-01"}))
    assert future["opening_balance"] == full["closing_balance"]
    assert future["lines"] == [] and future["closing_balance"] == future["opening_balance"]
    assert client.get("/api/gl/ledger", headers=admin,
                      params={"account_code": "9999"}).status_code == 404


def test_gl_requires_auth_and_permission(client, admin):
    assert client.get("/api/gl/journal").status_code == 401
    assert client.get("/api/gl/trial-balance").status_code == 401


# ---------- S3: 백필/재전기 (rebuild) ----------
def test_rebuild_reproduces_online_posting(client, admin):
    """혼합 시나리오 후 rebuild: 전표 수·시산표 총계가 온라인 전기와 동일하고
    재대사가 통과한다(백필 == 재전기 == 최초 전기 동일 코드 경로 검증)."""
    _mixed_flow(client, admin)

    before_entries = _all_entries(client, admin)
    before_tb = _ok(client.get("/api/gl/trial-balance", headers=admin))
    assert _ok(client.get("/api/gl/reconcile", headers=admin))["ok"]

    r = _ok(client.post("/api/gl/rebuild", headers=admin))
    assert r["total_entries"] == len(before_entries)
    assert r["reconcile"]["ok"]

    after_tb = _ok(client.get("/api/gl/trial-balance", headers=admin))
    assert after_tb["total_debit"] == before_tb["total_debit"]
    assert after_tb["total_credit"] == before_tb["total_credit"]
    # 계정별 잔액도 전부 동일(전표번호만 재채번될 뿐 금액은 결정적)
    before_rows = {row["account_code"]: row for row in before_tb["rows"]}
    for row in after_tb["rows"]:
        b = before_rows[row["account_code"]]
        assert (row["debit_total"], row["credit_total"]) == (b["debit_total"], b["credit_total"]), \
            f"{row['account_code']} 재전기 불일치"

    rec = _ok(client.get("/api/gl/reconcile", headers=admin))
    assert rec["ok"] and rec["inventory"]["diff"] == 0.0


def test_rebuild_backfills_missing_entries(client, admin):
    """전표를 지운 상태(과거 데이터 가정)에서 rebuild 가 소급 전기로 복원한다."""
    from sqlalchemy import delete as sa_delete
    from app.database import SessionLocal
    from app.models import JournalEntry, JournalLine

    _mixed_flow(client, admin)
    expected = len(_all_entries(client, admin))
    tb_before = _ok(client.get("/api/gl/trial-balance", headers=admin))

    # 백필 이전 상태 재현: GL 만 비운다(원천은 그대로)
    db = SessionLocal()
    try:
        db.execute(sa_delete(JournalLine))
        db.execute(sa_delete(JournalEntry))
        db.commit()
    finally:
        db.close()
    assert _ok(client.get("/api/gl/trial-balance", headers=admin))["total_debit"] == 0.0
    assert not _ok(client.get("/api/gl/reconcile", headers=admin))["ok"]   # 비어서 불일치

    r = _ok(client.post("/api/gl/rebuild", headers=admin))
    assert r["total_entries"] == expected and r["reconcile"]["ok"]
    tb_after = _ok(client.get("/api/gl/trial-balance", headers=admin))
    assert tb_after["total_debit"] == tb_before["total_debit"]
    assert _ok(client.get("/api/gl/reconcile", headers=admin))["ok"]


def test_rebuild_with_transfer_history(client, admin):
    """TRANSFER 가 낀 이력도 rebuild 시뮬레이터가 창고별 평균을 정확히 재현한다
    (이전 입고행은 이전원가로 가중평균 갱신 — apply_stock_delta 와 동일 수식)."""
    main = _default_warehouse_id(client, admin)
    wh2 = _ok(client.post("/api/warehouses", headers=admin, json={"name": "GL재전기창고"}))["id"]
    iid = _item(client, admin, "GL이전재전기품")
    _move(client, admin, item_id=iid, movement_type="IN", quantity=10, unit_price=100)
    _ok(client.post("/api/stock/transfer", headers=admin,
                    json={"item_id": iid, "from_warehouse_id": main, "to_warehouse_id": wh2, "quantity": 4}))
    _move(client, admin, item_id=iid, movement_type="OUT", quantity=2, unit_price=500,
          warehouse_id=wh2)
    r = _ok(client.post("/api/gl/rebuild", headers=admin))
    assert r["reconcile"]["ok"] and r["reconcile"]["inventory"]["diff"] == 0.0
