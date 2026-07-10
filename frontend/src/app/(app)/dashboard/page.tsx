"use client";

import { useQuery } from "@tanstack/react-query";

import StatCard from "@/components/ui/StatCard";
import { client, unwrap } from "@/lib/api/client";
import { useAuth } from "@/lib/auth/AuthProvider";

import styles from "./page.module.css";

/** 총건수만 필요하므로 page_size=1 로 최소 페이로드 조회(Page.total 사용) */
const COUNT_QUERY = { page: 1, page_size: 1 } as const;

function CountValue({
  isPending,
  isError,
  total,
}: {
  isPending: boolean;
  isError: boolean;
  total: number | undefined;
}) {
  if (isPending) return <>…</>;
  if (isError || total === undefined) return <>-</>;
  return (
    <>
      {total.toLocaleString("ko-KR")} <small>건</small>
    </>
  );
}

/**
 * 대시보드(슬라이스 1 범위) — 레거시 #dashboard 상단 stat 카드 3종 패리티:
 * 전체 거래처/전체 품목 총건수 + 내 역할·보유 권한. 최근 목록/매입·매출 통계는
 * 후속 슬라이스(거래처·품목·리포트 이전)에서 붙인다.
 */
export default function DashboardPage() {
  const { me } = useAuth();

  const partners = useQuery({
    queryKey: ["partners", "count"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/partners", { params: { query: COUNT_QUERY } }),
      ),
  });

  const items = useQuery({
    queryKey: ["items", "count"],
    queryFn: async () =>
      unwrap(await client.GET("/api/items", { params: { query: COUNT_QUERY } })),
  });

  const roleNames = me?.roles.map((r) => r.name).join(", ") || "-";
  const permCount = me?.permissions.length ?? 0;

  return (
    <section>
      <div className={styles.pageHead}>
        <div>
          <h2 className={styles.title}>대시보드</h2>
          <p className={styles.subtitle}>
            거래처·품목 마스터 현황을 한눈에 확인합니다.
          </p>
        </div>
      </div>
      <div className={styles.cards}>
        <StatCard
          label="전체 거래처"
          value={
            <CountValue
              isPending={partners.isPending}
              isError={partners.isError}
              total={partners.data?.total}
            />
          }
          foot="등록된 거래처 마스터"
        />
        <StatCard
          label="전체 품목"
          value={
            <CountValue
              isPending={items.isPending}
              isError={items.isError}
              total={items.data?.total}
            />
          }
          foot="등록된 품목 마스터"
        />
        <StatCard
          label="내 역할"
          value={roleNames}
          smallValue
          foot={`보유 권한 ${permCount}개`}
        />
      </div>
    </section>
  );
}
