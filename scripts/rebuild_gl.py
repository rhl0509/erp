"""GL 백필/재전기 스크립트 — docs/gl-design.md §6.

실행:  python -m scripts.rebuild_gl
전제:  alembic upgrade head (accounts/journal_* 테이블 + 계정 시드)

동작: 기존 전표 전체 삭제 → StockMovement(id 순)·Payment(pay_date 순) replay 재전기
      → 재대사(reconcile) 통과 시에만 커밋. 실패하면 전체 롤백(부분 백필 금지).
      최초 전기·API rebuild 와 동일한 코드 경로(app.gl)를 쓴다.
"""
import sys

from app.database import SessionLocal
from app import models  # noqa: F401  (매퍼 등록)
from app.gl import rebuild_gl, reconcile, GLError


def main() -> int:
    db = SessionLocal()
    try:
        summary = rebuild_gl(db)
        rec = reconcile(db)
        print(f"재전기 완료(커밋 전): 이동 {summary['movement_entries']}건, "
              f"결제 {summary['payment_entries']}건, 총 {summary['total_entries']}건")
        for name in ("inventory", "ar", "ap"):
            r = rec[name]
            print(f"  재대사 {name}: GL={r['gl']:.2f} 기대값={r['expected']:.2f} "
                  f"diff={r['diff']:.4f} {'OK' if r['ok'] else 'FAIL'}")
        t = rec["trial"]
        print(f"  시산표: 차변 {t['debit']:.2f} / 대변 {t['credit']:.2f} {'OK' if t['ok'] else 'FAIL'}")
        if not rec["ok"]:
            db.rollback()
            print("재대사 불일치 — 롤백했습니다. 원인 조사 후 다시 실행하세요.")
            return 1
        db.commit()
        print("커밋 완료.")
        return 0
    except GLError as e:
        db.rollback()
        print(f"재전기 실패(롤백): {e}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
