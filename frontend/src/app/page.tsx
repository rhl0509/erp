"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * 루트 진입 — /dashboard 로 보낸다. 미인증이면 proxy(세션 쿠키 가드)가 이 페이지에
 * 닿기 전에 /login 으로 돌리고, 토큰 유효성은 (app) 셸 가드(/me)가 재검증한다.
 */
export default function Home() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/dashboard");
  }, [router]);

  return null;
}
