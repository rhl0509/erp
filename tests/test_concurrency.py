"""동시성 회귀 테스트 (실 MySQL 대상, opt-in).

SQLite 는 SELECT ... FOR UPDATE 가 no-op 이라 이 부류 버그를 재현할 수 없다.
`TEST_MYSQL_URL` 환경변수가 있을 때만 실행한다. 예:
  TEST_MYSQL_URL="mysql+pymysql://root:pw@localhost:3306/erp_test?charset=utf8mb4"

검증하는 것: 같은 발주 라인에 동시 입고 요청이 쏟아져도, 헤더 FOR UPDATE(직렬화)
+ 엔진 READ COMMITTED(락 획득 후 최신 커밋본 읽기) 조합으로 주문수량을 초과
입고하지 않는다. (라이브 스모크에서 잡힌 lost-update 버그의 회귀 방지)
"""
import os
import threading

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker

MYSQL_URL = os.environ.get("TEST_MYSQL_URL")
pytestmark = pytest.mark.skipif(not MYSQL_URL, reason="TEST_MYSQL_URL 미설정 — MySQL 동시성 테스트 건너뜀")


def test_concurrent_receive_no_overreceive():
    from app.database import Base
    from app import models  # noqa: F401  (매퍼 등록)
    from app.models import PurchaseOrder, PurchaseOrderLine, Partner, Item, StockMovement
    from app.services import post_movement, generate_code

    # 앱과 동일하게 READ COMMITTED 엔진 구성
    engine = create_engine(MYSQL_URL, isolation_level="READ COMMITTED", pool_size=20, max_overflow=20)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    # 셋업: 발주 qty=10 확정
    db = Session()
    sup = Partner(code=generate_code(db, "PARTNER", "S"), name="동시공급", partner_type="supplier")
    it = Item(code=generate_code(db, "ITEM", "I"), name="동시품", unit="EA")
    db.add_all([sup, it]); db.flush()
    po = PurchaseOrder(po_no=generate_code(db, "PO", "PO", use_period=True),
                       partner_id=sup.id, status="confirmed")
    po.lines.append(PurchaseOrderLine(item_id=it.id, qty=10, unit_price=1000))
    db.add(po); db.commit()
    po_id, line_id, item_id = po.id, po.lines[0].id, it.id
    db.close()

    codes = []
    lk = threading.Lock()

    def receive_5():
        db = Session()
        try:
            po = db.execute(
                select(PurchaseOrder).where(PurchaseOrder.id == po_id).with_for_update()
            ).scalar_one()  # 헤더 락(직렬화)
            l = {x.id: x for x in po.lines}[line_id]
            if po.status not in ("confirmed", "partial") or 5 > (l.qty - l.received_qty):
                db.rollback(); code = 400
            else:
                post_movement(db, item_id=item_id, movement_type="IN", quantity=5,
                              unit_price=1000, ref_type="PO", ref_line_id=line_id)
                l.received_qty += 5
                po.status = "received" if all(x.received_qty >= x.qty for x in po.lines) else "partial"
                db.commit(); code = 200
        except Exception:
            db.rollback(); code = 500
        finally:
            db.close()
        with lk:
            codes.append(code)

    threads = [threading.Thread(target=receive_5) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()

    db = Session()
    received = db.execute(
        select(PurchaseOrderLine.received_qty).where(PurchaseOrderLine.id == line_id)
    ).scalar_one()
    move_count, move_qty = db.execute(
        select(func.count(), func.coalesce(func.sum(StockMovement.quantity), 0))
        .where(StockMovement.ref_line_id == line_id, StockMovement.ref_type == "PO")
    ).one()
    db.close()
    engine.dispose()

    # 주문 10개 → 정확히 2건(5+5)만 성공, 나머지는 400, 초과 입고 없음
    assert codes.count(200) == 2, f"성공 건수 이상: {codes}"
    assert received == 10, f"received_qty={received} (초과 입고)"
    assert move_count == 2 and int(move_qty) == 10, f"이동 {move_count}건/{move_qty}개 (초과)"


def test_concurrent_movements_unique_no_and_no_deadlock():
    """Phase 6 채번 락 분리(autonomous 채번) 검증.

    같은 품목에 다수 스레드가 동시에 입출고(post_movement)를 쏟아도
    (a) movement_no 가 전부 유니크하고, (b) 교착/오류 없이 전부 완료되며,
    (c) 시퀀스(last_seq)가 소비 건수만큼 증가한다.
    결번(gap)은 설계상 허용이므로 번호 '연속성'은 단언하지 않는다.
    """
    from app.database import Base
    from app import models  # noqa: F401  (매퍼 등록)
    from app.models import Item, StockMovement, NumberSequence
    from app.services import post_movement, generate_code

    # 앱과 동일하게 READ COMMITTED 엔진 구성. autonomous 채번이 요청당 커넥션을
    # 잠깐 2개 쓰므로 풀을 스레드 수 대비 넉넉히 잡는다.
    engine = create_engine(MYSQL_URL, isolation_level="READ COMMITTED", pool_size=20, max_overflow=20)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    # 셋업: 품목 + 초기 재고 1000 (이 IN 1건도 STOCK 시퀀스를 1 소비한다)
    db = Session()
    it = Item(code=generate_code(db, "ITEM", "I"), name="채번품", unit="EA")
    db.add(it); db.flush()
    post_movement(db, item_id=it.id, movement_type="IN", quantity=1000, unit_price=100)
    db.commit()
    item_id = it.id
    db.close()

    N = 16
    results: list[tuple[str, str]] = []
    lk = threading.Lock()

    def move(i: int):
        db = Session()
        try:
            mtype = "IN" if i % 2 == 0 else "OUT"   # 입출고 혼합(잔고 락 경합 유발)
            m = post_movement(db, item_id=item_id, movement_type=mtype,
                              quantity=1, unit_price=100)
            no = m.movement_no   # commit 후 만료(expire) 전에 확보
            db.commit()
            with lk:
                results.append(("ok", no))
        except Exception as e:
            db.rollback()
            with lk:
                results.append(("err", repr(e)))
        finally:
            db.close()

    threads = [threading.Thread(target=move, args=(i,)) for i in range(N)]
    for t in threads: t.start()
    for t in threads: t.join()

    # (b) 교착·오류 없이 전부 완료
    errors = [r for r in results if r[0] == "err"]
    assert not errors, f"동시 입출고 실패: {errors}"
    assert len(results) == N

    # (a) movement_no 전부 유니크 (스레드 반환값 + DB 저장값 양쪽 확인)
    nos = [no for _, no in results]
    assert len(set(nos)) == N, f"movement_no 중복: {sorted(nos)}"

    db = Session()
    db_total, db_distinct = db.execute(
        select(func.count(StockMovement.id), func.count(func.distinct(StockMovement.movement_no)))
    ).one()
    # (c) 시퀀스 정상 증가: 성공 N건 + 셋업 IN 1건 = N+1 소비 (결번 없음 = 전부 성공)
    last_seq = db.execute(
        select(NumberSequence.last_seq).where(NumberSequence.seq_key == "STOCK")
    ).scalar_one()
    db.close()
    engine.dispose()

    assert db_total == db_distinct == N + 1, f"DB 이동 {db_total}건 / 유니크 번호 {db_distinct}건"
    assert last_seq == N + 1, f"last_seq={last_seq} (기대 {N + 1})"
