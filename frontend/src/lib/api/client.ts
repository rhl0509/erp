/**
 * 타입 안전 API 클라이언트 — openapi-fetch + 생성된 schema.d.ts.
 *
 * 레거시 index.html 의 api() 헬퍼 패리티:
 *  - localStorage("erp_token") 에서 Bearer 토큰 주입 (레거시와 같은 키 → 공존 기간 단일 로그인)
 *  - 401 응답 → 토큰 제거 + /login 리다이렉트 (로그인 요청 자체는 예외)
 *  - 오류는 서버 {detail, code, request_id, fields?} 를 ApiRequestError 로 표면화
 *  - form-encoded 로그인 헬퍼(loginRequest) — JSON 으로 보내면 422 나는 함정을 여기 1곳에 캡슐화
 *  - blob 다운로드 헬퍼(downloadFile) — 엑셀 export 용 골격(슬라이스 6에서 사용)
 */
import createClient from "openapi-fetch";

import type { paths } from "./schema";
import { ApiRequestError, type ApiError } from "./errors";

/** 레거시 index.html 의 TOKEN_KEY 와 동일해야 한다(localStorage 공유 = 단일 로그인). */
export const TOKEN_KEY = "erp_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

/** 토큰 제거 + 로그인 화면으로. 레거시 doLogout() 의 401 처리 패리티. */
export function forceLogout(): void {
  setToken(null);
  if (typeof window !== "undefined" && window.location.pathname !== "/login") {
    // 하드 내비게이션: React Query 캐시 등 클라이언트 상태를 함께 초기화한다.
    window.location.assign("/login");
  }
}

/** 상대경로 /api — next.config.ts rewrites 가 FastAPI(:8000)로 프록시(same-origin). */
export const client = createClient<paths>({ baseUrl: "" });

client.use({
  onRequest({ request }) {
    const token = getToken();
    if (token && !request.headers.has("Authorization")) {
      request.headers.set("Authorization", `Bearer ${token}`);
    }
    return request;
  },
  onResponse({ request, response }) {
    // 401 → 강제 로그아웃. 단, 로그인 시도 자체의 401(잘못된 비밀번호)은 폼에서 처리한다.
    if (
      response.status === 401 &&
      !new URL(request.url).pathname.endsWith("/api/auth/login")
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
 * 로그인 — OAuth2 form-encoded(POST /api/auth/login). 성공 시 토큰을 저장하고 반환한다.
 * openapi-fetch 는 Content-Type 이 x-www-form-urlencoded 면 기본 serializer 가
 * URLSearchParams 로 직렬화한다(레거시 index.html 의 URLSearchParams 전송과 동일).
 */
export async function loginRequest(
  username: string,
  password: string,
): Promise<string> {
  const result = await client.POST("/api/auth/login", {
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: { username, password, scope: "" },
  });
  const data = unwrap(result);
  setToken(data.access_token);
  return data.access_token;
}

/**
 * 파일 다운로드(엑셀 등) — <a href> 로는 Authorization 헤더를 못 실으므로
 * fetch → blob → objectURL 패턴(레거시 downloadFile 패리티). 실패 시 throw
 * (호출부에서 toast). 슬라이스 6(리포트 export)에서 사용할 골격.
 */
export async function downloadFile(
  path: string,
  filename: string,
): Promise<void> {
  const token = getToken();
  const res = await fetch(path, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
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
