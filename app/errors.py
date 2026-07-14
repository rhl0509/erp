"""필드 단위 오류를 라우터에서 낼 때 쓰는 예외.

Pydantic 검증(422)은 {detail, code, fields} 형식으로 내려가고 화면은 fields 로 입력칸 옆에
메시지를 붙인다(applyServerFieldErrors). 그런데 비밀번호 정책처럼 **다른 필드(아이디)나 DB
상태를 봐야 판정되는 규칙**은 스키마 안에서 검증할 수 없어 라우터에서 판정한다. 이때도 같은
형식으로 내려주기 위한 예외 — main 의 HTTPException 핸들러가 fields 를 함께 실어 보낸다.
"""
from fastapi import HTTPException


class FieldError(HTTPException):
    def __init__(self, field: str, message: str, status_code: int = 422):
        super().__init__(status_code=status_code, detail=message)
        self.fields = {field: message}
