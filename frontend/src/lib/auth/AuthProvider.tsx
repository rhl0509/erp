"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";

import { client, getToken, setToken, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

export type Me = components["schemas"]["MeOut"];

export const ME_QUERY_KEY = ["auth", "me"] as const;

type AuthContextValue = {
  /** 현재 사용자(GET /api/auth/me). 비로그인/로딩 중이면 null */
  me: Me | null;
  /** me 조회 진행 중 여부 — 가드에서 리다이렉트 판정 전 대기용 */
  isLoading: boolean;
  /** 레거시 can(code) 패리티: permissions.includes("*") || includes(code) */
  can: (perm: string) => boolean;
  /** 토큰 제거 + 캐시 비우기 + /login 이동 (레거시 doLogout 패리티) */
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

async function fetchMe(): Promise<Me | null> {
  if (!getToken()) return null; // 토큰 없음 = 비로그인 (요청 자체를 생략)
  return unwrap(await client.GET("/api/auth/me"));
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const router = useRouter();

  const { data, isPending } = useQuery({
    queryKey: ME_QUERY_KEY,
    queryFn: fetchMe,
    staleTime: 5 * 60_000, // 권한은 자주 안 바뀜 — 401 시 어차피 강제 로그아웃
    retry: false,
  });

  const me = data ?? null;

  const can = useCallback(
    (perm: string) =>
      !!me && (me.permissions.includes("*") || me.permissions.includes(perm)),
    [me],
  );

  const logout = useCallback(() => {
    setToken(null);
    queryClient.clear(); // 사용자별 서버 상태 캐시 제거
    router.replace("/login");
  }, [queryClient, router]);

  const value = useMemo<AuthContextValue>(
    () => ({ me, isLoading: isPending, can, logout }),
    [me, isPending, can, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth 는 <AuthProvider> 안에서만 사용할 수 있습니다.");
  return ctx;
}

/** 개별 버튼/액션 게이팅용(레거시 data-perm 대체). */
export function usePermission(perm: string): boolean {
  return useAuth().can(perm);
}
