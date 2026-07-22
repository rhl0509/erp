"""API 계층 공용 쿼리 파라미터 파서·검증기."""
from datetime import datetime

from fastapi import HTTPException


def parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="날짜 형식이 올바르지 않습니다 (YYYY-MM-DD)")


def validate_order(value: str) -> None:
    """정렬 방향은 asc/desc 만 허용한다. 오타를 조용히 desc로 흡수하지 않는다."""
    if value not in ("asc", "desc"):
        raise HTTPException(
            status_code=400,
            detail=f"정렬 방향은 asc 또는 desc 여야 합니다: {value}",
        )
