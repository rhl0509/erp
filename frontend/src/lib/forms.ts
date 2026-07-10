import type { FieldValues, Path, UseFormSetError } from "react-hook-form";

import { ApiRequestError } from "@/lib/api/errors";

/**
 * 서버 422 {fields} → RHF 필드 에러 매핑. 폼에 없는 필드/그 외 오류는
 * 호출부 toast 로 처리하도록 "매핑했는지" 여부를 반환한다.
 * (레거시 #mSave 핸들러의 err.fields → .fld-err 표시 패리티)
 */
export function applyServerFieldErrors<T extends FieldValues>(
  err: unknown,
  fieldNames: readonly Path<T>[],
  setError: UseFormSetError<T>,
): boolean {
  if (!(err instanceof ApiRequestError) || !err.fields) return false;
  let applied = false;
  for (const name of fieldNames) {
    const message = err.fields[name];
    if (message) {
      setError(name, { type: "server", message });
      applied = true;
    }
  }
  return applied;
}
