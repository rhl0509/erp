/**
 * 비밀번호 정책 — 규칙의 단일 소스는 서버(app/security.py)다.
 * 화면은 GET /api/auth/password-policy 로 문구·최소길이를 받아 쓰고, 강도 점수만
 * 서버와 같은 공식으로 로컬 계산한다(타이핑 중 즉시 피드백용). 최종 판정은 항상 서버가 한다.
 */
import { useQuery } from "@tanstack/react-query";

import { client, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

export type PasswordPolicy = components["schemas"]["PasswordPolicyOut"];

export const PASSWORD_POLICY_QUERY_KEY = ["auth", "password-policy"] as const;

/** 정책(최소길이·안내문구). 로그인 전에도 호출 가능한 공개 엔드포인트다. */
export function usePasswordPolicy() {
  const { data } = useQuery({
    queryKey: PASSWORD_POLICY_QUERY_KEY,
    queryFn: async () => unwrap(await client.GET("/api/auth/password-policy")),
    staleTime: Infinity, // 서버 설정값 — 세션 중 바뀌지 않는다
    retry: false,
  });
  return data ?? null;
}

/** 0~4. app/security.py password_strength 와 같은 공식. */
export function passwordStrength(password: string, minLength: number): number {
  if (!password) return 0;
  let score = 0;
  if (password.length >= minLength) score += 1;
  if (password.length >= 14) score += 1;
  if (/[A-Za-z]/.test(password) && /\d/.test(password)) score += 1;
  if (/[^A-Za-z0-9]/.test(password)) score += 1;
  return Math.min(score, 4);
}

export const STRENGTH_LABELS = ["매우 약함", "약함", "보통", "강함", "매우 강함"] as const;

/**
 * 폼 단계에서 거를 수 있는 규칙만 검사한다(길이·영문+숫자·공백·아이디 포함).
 * 서버의 '흔한 비밀번호' 블록리스트는 여기서 흉내 내지 않는다 — 서버가 422 로 돌려주면
 * 그 메시지를 필드에 그대로 붙인다(applyServerFieldErrors).
 */
export function validatePasswordClient(
  password: string,
  minLength: number,
  username?: string,
): string | null {
  if (password.length < minLength) return `비밀번호는 ${minLength}자 이상이어야 합니다.`;
  if (/\s/.test(password)) return "비밀번호에 공백을 포함할 수 없습니다.";
  if (!/[A-Za-z]/.test(password) || !/\d/.test(password))
    return "비밀번호는 영문과 숫자를 모두 포함해야 합니다.";
  const name = (username ?? "").trim().toLowerCase();
  if (name.length >= 3 && password.toLowerCase().includes(name))
    return "비밀번호에 아이디를 포함할 수 없습니다.";
  return null;
}
