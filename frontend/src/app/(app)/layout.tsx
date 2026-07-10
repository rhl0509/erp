"use client";

import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";

import TopNav from "@/components/ui/TopNav";
import { useAuth } from "@/lib/auth/AuthProvider";

import styles from "./layout.module.css";

/**
 * (app) route group 인증 셸 — URL 에는 나타나지 않고 하위 페이지에 공통 적용.
 * me 확인 전에는 빈 스켈레톤, 비로그인(me 없음)이면 /login 으로 보낸다.
 * (토큰이 무효한 경우는 client.ts 의 401 미들웨어가 forceLogout 으로 처리.)
 */
export default function AppShellLayout({ children }: { children: ReactNode }) {
  const { me, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!isLoading && !me) router.replace("/login");
  }, [isLoading, me, router]);

  if (isLoading || !me) {
    // 로딩 중 또는 /login 리다이렉트 대기 — 보호 콘텐츠를 렌더하지 않는다.
    return (
      <div className={styles.loading} aria-busy="true">
        <div className={styles.loadingBar} />
      </div>
    );
  }

  return (
    <>
      <TopNav />
      <main className={styles.container}>{children}</main>
    </>
  );
}
