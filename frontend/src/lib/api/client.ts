/**
 * 타입 안전 API 클라이언트 — openapi-fetch + 생성된 schema.d.ts.
 *
 * 인증: httpOnly 세션 쿠키(erp_session). 로그인 시 서버가 쿠키를 심고, 이후 모든
 * 요청은 same-origin(next rewrites 로 /api → FastAPI) 이라 브라우저가 쿠키를 자동
 * 전송한다. 토큰을 JS/localStorage 에 두지 않아 XSS 토큰 탈취면이 없다.
 *  - 401 응답 → 강제 로그아웃(쿠키 삭제 + /login). 단 로그인·me 프로브는 예외.
 *  - 오류는 서버 {detail, code, request_id, fields?} 를 ApiRequestError 로 표면화.
 *  - form-encoded 로그인 헬퍼(loginRequest).
 *  - blob 다운로드 헬퍼(downloadFile) — 쿠키가 자동 전송되므로 헤더 주입 불필요.
 */
import createClient from "openapi-fetch";

import type { paths } from "./schema";
import { ApiRequestError, type ApiError } from "./errors";

/** 세션 쿠키는 httpOnly 라 JS 로 읽거나 지울 수 없다. 로그아웃은 서버에 위임한다. */
export function forceLogout(): void {
  if (typeof window === "undefined") return;
  // keepalive: 곧바로 이어질 내비게이션에도 요청이 완료되도록 한다.
  void fetch("/api/auth/logout", { method: "POST", keepalive: true }).catch(
    () => {},
  );
  if (window.location.pathname !== "/login") {
    // 하드 내비게이션: React Query 캐시 등 클라이언트 상태를 함께 초기화한다.
    window.location.assign("/login");
  }
}

/** 상대경로 /api — next.config.ts rewrites 가 FastAPI(:8000)로 프록시(same-origin). */
export const client = createClient<paths>({ baseUrl: "" });

client.use({
  onResponse({ request, response }) {
    // 401 → 강제 로그아웃. 단 (1)로그인 시도의 401(잘못된 비밀번호)은 폼에서 처리,
    // (2)me 프로브의 401(비로그인)은 AuthProvider 가 null 로 해석 — 둘 다 제외.
    const path = new URL(request.url).pathname;
    if (
      response.status === 401 &&
      !path.endsWith("/api/auth/login") &&
      !path.endsWith("/api/auth/me")
    ) {
      forceLogout();
    }
    return response;
  },
});

/**
 * openapi-fetch 결과 {data, error, response} 를 "성공 데이터 또는 throw" 로 변환.
 * TanStack Query 의 queryFn/mutationFn 에서 사용한다. 204 는 undefined 를 반환.
 */
export function unwrap<T>(result: {
  data?: T;
  error?: unknown;
  response: Response;
}): T {
  if (result.error !== undefined) {
    throw new ApiRequestError(
      result.response.status,
      result.error as Partial<ApiError>,
    );
  }
  return result.data as T;
}

/**
 * 로그인 — OAuth2 form-encoded(POST /api/auth/login). 성공 시 서버가 httpOnly
 * 세션 쿠키를 심는다(응답 본문의 access_token 은 헤더 방식 클라이언트용이라 무시).
 */
export async function loginRequest(
  username: string,
  password: string,
): Promise<void> {
  const result = await client.POST("/api/auth/login", {
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: { username, password, scope: "" },
  });
  unwrap(result); // 실패 시 throw
}

/** 로그아웃 — 서버가 세션 쿠키를 제거한다. */
export async function logoutRequest(): Promise<void> {
  await client.POST("/api/auth/logout");
}

/**
 * 파일 다운로드(엑셀 등) — <a href> 대신 fetch → blob → objectURL 패턴.
 * same-origin 이라 세션 쿠키가 자동 전송되어 인증된다. 실패 시 throw(호출부 toast).
 */
export async function downloadFile(
  path: string,
  filename: string,
): Promise<void> {
  const res = await fetch(path);
  if (!res.ok) {
    let body: Partial<ApiError> | null = null;
    try {
      body = (await res.json()) as Partial<ApiError>;
    } catch {
      /* 본문이 JSON 이 아니면 무시 */
    }
    if (res.status === 401) forceLogout();
    throw new ApiRequestError(res.status, {
      ...body,
      detail: body?.detail || "내보내기에 실패했습니다.",
    });
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
