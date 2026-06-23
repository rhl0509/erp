import json
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .models import NumberSequence, AuditLog


def paginate(db: Session, stmt, page: int, page_size: int) -> dict:
    """공통 페이지네이션. {items, total, page, page_size, pages} 를 돌려준다."""
    page = max(1, page)
    page_size = min(max(1, page_size), 100)  # 과도한 요청 방어(최대 100)

    total = db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()

    rows = db.execute(
        stmt.limit(page_size).offset((page - 1) * page_size)
    ).scalars().all()

    pages = (total + page_size - 1) // page_size
    return {"items": rows, "total": total, "page": page, "page_size": page_size, "pages": pages}


def generate_code(
    db: Session,
    seq_key: str,
    prefix: str,
    use_period: bool = False,
    width: int = 4,
) -> str:
    """
    채번 서비스. 같은 트랜잭션 안에서 SELECT ... FOR UPDATE 로 행을 잠가
    동시 요청에도 번호가 중복되지 않게 한다.

    예) generate_code(db, "PARTNER", "CUST")            -> CUST-0001
        generate_code(db, "PO", "PO", use_period=True)  -> PO-202506-0001
    """
    period = datetime.now().strftime("%Y%m") if use_period else ""

    row = db.execute(
        select(NumberSequence)
        .where(NumberSequence.seq_key == seq_key, NumberSequence.period == period)
        .with_for_update()
    ).scalar_one_or_none()

    if row is None:
        # 최초 생성. 동시 최초생성 충돌은 uq_seq_key_period 제약으로 방어된다.
        row = NumberSequence(seq_key=seq_key, prefix=prefix, period=period, last_seq=0)
        db.add(row)
        db.flush()

    row.last_seq += 1
    seq = row.last_seq
    db.flush()

    parts = [p for p in (prefix, period, str(seq).zfill(width)) if p]
    return "-".join(parts)


def record_audit(
    db: Session,
    user,
    action: str,
    entity: str,
    entity_id,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    """변경 이력 기록. commit 은 호출하는 쪽에서 한다."""
    db.add(
        AuditLog(
            user_id=getattr(user, "id", None),
            username=getattr(user, "username", ""),
            action=action,
            entity=entity,
            entity_id=str(entity_id),
            before=json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
            after=json.dumps(after, ensure_ascii=False, default=str) if after is not None else None,
        )
    )
