"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { getToken } from "@/lib/api/client";

/** 루트 진입 — 토큰 유무로 /dashboard | /login 분기(유효성은 (app) 가드가 재검증). */
export default function Home() {
  const router = useRouter();

  useEffect(() => {
    router.replace(getToken() ? "/dashboard" : "/login");
  }, [router]);

  return null;
}
