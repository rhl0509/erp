/**
 * 서버 공통 오류 계약 — app/main.py 의 exception handler 가 모든 오류를
 * {detail, code, request_id, fields?} 형식으로 통일한다(422는 fields 에
 * 필드별 한글 메시지). 프론트에서 이 형태는 여기 1곳에서만 정의한다.
 */
export type ApiError = {
  detail: string;
  code: string;
  request_id: string;
  /** 422 validation_error 일 때 필드명 → 한글 메시지 */
  fields?: Record<string, string>;
};

/** 레거시 api() 헬퍼와 동일한 기본 문구 */
export const GENERIC_ERROR_MESSAGE = "요청을 처리하지 못했습니다.";

/**
 * API 호출 실패를 나타내는 예외. message 는 서버 detail(없으면 기본 문구),
 * fields 는 422 필드 에러(react-hook-form setError 매핑용).
 */
export class ApiRequestError extends Error {
  status: number;
  code?: string;
  requestId?: string;
  fields?: Record<string, string>;

  constructor(status: number, body?: Partial<ApiError> | null) {
    super(body?.detail || GENERIC_ERROR_MESSAGE);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = body?.code;
    this.requestId = body?.request_id;
    this.fields = body?.fields;
  }
}

/** unknown 오류에서 사용자에게 보여줄 메시지를 뽑는다(토스트용). */
export function errorMessage(err: unknown): string {
  if (err instanceof Error && err.message) return err.message;
  return GENERIC_ERROR_MESSAGE;
}
