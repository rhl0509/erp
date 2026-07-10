"use client";

import { useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import Button from "@/components/ui/Button";
import DataTable, { type Column } from "@/components/ui/DataTable";
import { Panel, Spacer, Toolbar } from "@/components/ui/Panel";
import StatCard from "@/components/ui/StatCard";
import { client, unwrap } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { won } from "@/lib/format";

import styles from "./page.module.css";

type AgingBucket = components["schemas"]["AgingBucket"];

/** 합계 행(클라이언트 합성)의 rowKey — 실제 partner_id 와 겹치지 않는 음수 */
const TOTAL_ROW_ID = -1;

/** 레거시 aging 초기 기준일 패리티 — 오늘(로컬) YYYY-MM-DD */
function todayStr(): string {
  const t = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())}`;
}

/**
 * 채권/채무 연령분석(슬라이스 6) — 레거시 #aging 패리티:
 * AR/AP 토글 + 기준일(기본 오늘) + 거래처 필터, 총 미수/미지급 stat 1장,
 * 경과일 버킷 표(0-30/31-60/61-90/90+/합계) + 합계 행,
 * 90일 초과는 잔액이 있으면 굵게(모노크롬 강조). 필터는 즉시 적용(조회 버튼 불필요).
 */
export default function AgingPage() {
  const [kind, setKind] = useState<"AR" | "AP">("AR");
  // (app) 레이아웃이 me 확인 전 children 을 렌더하지 않아 이 초기화는 클라이언트에서만 실행된다.
  const [asOf, setAsOf] = useState(() => todayStr());
  const [partnerId, setPartnerId] = useState("");

  // 거래처 필터 옵션(레거시 populateAgingPartners 패리티 — transactions 페이지와 캐시 공유)
  const partnerOptions = useQuery({
    queryKey: ["partners", "options"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/partners", {
          params: { query: { page: 1, page_size: 100 } },
        }),
      ),
  });

  const aging = useQuery({
    queryKey: ["reports", "aging", { kind, asOf, partnerId }],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/reports/aging", {
          params: {
            query: {
              kind,
              ...(asOf ? { as_of: asOf } : {}),
              ...(partnerId ? { partner_id: Number(partnerId) } : {}),
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  // 버킷 열 합계 행을 클라이언트에서 합성해 목록 끝에 붙인다(레거시 sum 행 패리티)
  const rows = useMemo(() => {
    const buckets = aging.data?.buckets;
    if (!buckets || buckets.length === 0) return buckets;
    const sum = buckets.reduce(
      (acc, b) => ({
        b_0_30: acc.b_0_30 + b.b_0_30,
        b_31_60: acc.b_31_60 + b.b_31_60,
        b_61_90: acc.b_61_90 + b.b_61_90,
        b_over_90: acc.b_over_90 + b.b_over_90,
      }),
      { b_0_30: 0, b_31_60: 0, b_61_90: 0, b_over_90: 0 },
    );
    const totalRow: AgingBucket = {
      partner_id: TOTAL_ROW_ID,
      partner_name: "합계",
      ...sum,
      total: aging.data?.total ?? 0,
    };
    return [...buckets, totalRow];
  }, [aging.data]);

  // 90일 초과: 잔액 있으면 굵게(레거시 risk() 모노크롬 강조)
  const risk = (v: number) => (v > 0 ? <b>{won(v)}</b> : won(v));

  const columns: Column<AgingBucket>[] = [
    { key: "partner_name", header: "거래처" },
    { key: "b_0_30", header: "0-30일", num: true, render: (b) => won(b.b_0_30) },
    {
      key: "b_31_60",
      header: "31-60일",
      num: true,
      render: (b) => won(b.b_31_60),
    },
    {
      key: "b_61_90",
      header: "61-90일",
      num: true,
      render: (b) => won(b.b_61_90),
    },
    {
      key: "b_over_90",
      header: "90일 초과",
      num: true,
      render: (b) => risk(b.b_over_90),
    },
    {
      key: "total",
      header: "합계",
      num: true,
      render: (b) =>
        b.partner_id === TOTAL_ROW_ID ? (
          <b>₩{won(b.total)}</b>
        ) : (
          <b>{won(b.total)}</b>
        ),
    },
  ];

  const isAR = kind === "AR";

  return (
    <section>
      <div className={styles.pageHead}>
        <div>
          <h2 className={styles.title}>채권/채무 연령분석</h2>
          <p className={styles.subtitle}>
            청구 경과일 구간별 미결제 잔액(Aging)을 확인합니다.
          </p>
        </div>
      </div>

      <div className={styles.cards}>
        <StatCard
          label={isAR ? "총 미수" : "총 미지급"}
          value={
            aging.isPending
              ? "…"
              : aging.isError
                ? "-"
                : `₩${won(aging.data?.total)}`
          }
          foot={
            aging.isPending || aging.isError || !aging.data
              ? "기준일 -"
              : `기준일 ${aging.data.as_of} · 거래처 ${won(aging.data.buckets.length)}곳`
          }
        />
      </div>

      <Panel>
        <Toolbar>
          <Button
            variant={isAR ? "primary" : "ghost"}
            size="sm"
            onClick={() => setKind("AR")}
            aria-pressed={isAR}
          >
            미수 (AR)
          </Button>
          <Button
            variant={isAR ? "ghost" : "primary"}
            size="sm"
            onClick={() => setKind("AP")}
            aria-pressed={!isAR}
          >
            미지급 (AP)
          </Button>
          <input
            type="date"
            className={styles.dateInput}
            value={asOf}
            onChange={(e) => setAsOf(e.target.value)}
            aria-label="기준일"
            title="기준일"
          />
          <select
            value={partnerId}
            onChange={(e) => setPartnerId(e.target.value)}
            aria-label="거래처 필터"
          >
            <option value="">전체 거래처</option>
            {(partnerOptions.data?.items ?? []).map((p) => (
              <option key={p.id} value={String(p.id)}>
                {p.name} ({p.code})
              </option>
            ))}
          </select>
          <Spacer />
        </Toolbar>
        <DataTable
          columns={columns}
          rows={aging.isError ? [] : rows}
          rowKey={(b) => b.partner_id}
          loading={aging.isPending}
          emptyText={
            aging.isError ? errorMessage(aging.error) : "데이터가 없습니다."
          }
          rowClassName={(b) =>
            b.partner_id === TOTAL_ROW_ID ? styles.totalRow : undefined
          }
        />
      </Panel>
    </section>
  );
}
