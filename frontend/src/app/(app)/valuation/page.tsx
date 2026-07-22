"use client";

import { useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import DataTable, { Pager, type Column } from "@/components/ui/DataTable";
import { Panel, Toolbar } from "@/components/ui/Panel";
import StatCard from "@/components/ui/StatCard";
import { client, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { won } from "@/lib/format";

import styles from "./page.module.css";

type Valuation = components["schemas"]["StockValuationOut"];
type Warehouse = components["schemas"]["WarehouseOut"];

/** 레거시 페이지당 10건 패리티 */
const PAGE_SIZE = 10;

/** 레거시 창고 셀렉트 옵션 라벨 패리티 — "이름 (코드) · 기본" */
const whLabel = (w: Warehouse) =>
  `${w.name} (${w.code})${w.is_default ? " · 기본" : ""}`;

/**
 * 재고평가 / 손익(슬라이스 6) — 레거시 #valuation 패리티:
 * 총 재고평가액·매출/매출원가·매출총이익 stat 3장(손익은 창고 필터 미지원 = 전사 기준),
 * 품목별 평가 목록(품목 검색 + 창고 필터, 미지정 시 전 창고 합산, 페이지네이션).
 * export 엔드포인트는 백엔드에 없어 내보내기 버튼도 두지 않는다(costing.py 확인).
 */
export default function ValuationPage() {
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const [page, setPage] = useState(1);

  // 레거시 debounce(…, 300) 패리티 — 입력 후 300ms 뒤 검색 적용 + 1페이지로
  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(qInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [qInput]);

  // 창고 필터 옵션(레거시 populateWarehouseFilter 패리티 — 활성 창고만, stock 페이지와 캐시 공유)
  const warehouses = useQuery({
    queryKey: ["warehouses", "active"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/warehouses", {
          params: { query: { active_only: true, page: 1, page_size: 100 } },
        }),
      ),
  });

  const list = useQuery({
    queryKey: ["costing", "valuation", "list", { q, warehouseId, page }],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/costing/valuation", {
          params: {
            query: {
              page,
              page_size: PAGE_SIZE,
              ...(q ? { q } : {}),
              ...(warehouseId ? { warehouse_id: Number(warehouseId) } : {}),
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  const summary = useQuery({
    queryKey: ["costing", "valuation", "summary", { warehouseId }],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/costing/valuation/summary", {
          params: {
            query: warehouseId ? { warehouse_id: Number(warehouseId) } : {},
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  // 손익은 창고 필터 미지원(전사 기준) — 레거시 loadValuation 의 /api/costing/margin 패리티
  const margin = useQuery({
    queryKey: ["costing", "margin"],
    queryFn: async () => unwrap(await client.GET("/api/costing/margin")),
    placeholderData: keepPreviousData,
  });

  const columns: Column<Valuation>[] = [
    { key: "item_code", header: "코드", code: true },
    { key: "item_name", header: "품목" },
    {
      key: "on_hand",
      header: "현재고",
      num: true,
      render: (v) => won(v.on_hand),
    },
    {
      key: "avg_cost",
      header: "평균원가",
      num: true,
      render: (v) => won(Math.round(v.avg_cost)),
    },
    {
      key: "value",
      header: "평가액",
      num: true,
      render: (v) => `₩${won(v.value)}`,
    },
  ];

  const stat = <T,>(
    query: { isPending: boolean; isError: boolean; data?: T },
    pick: (data: T) => string,
  ) => (query.isPending ? "…" : query.isError || !query.data ? "-" : pick(query.data));

  return (
    <section>
      <div className={styles.pageHead}>
        <div>
          <h1 className={styles.title}>재고평가 / 손익</h1>
          <p className={styles.subtitle}>
            이동평균 원가 기준 재고평가액과 매출총이익
          </p>
        </div>
      </div>

      <div className={styles.cards}>
        <StatCard
          label="총 재고평가액"
          value={stat(summary, (s) => `₩${won(s.total_value)}`)}
          foot={
            summary.isPending || summary.isError
              ? "보유 품목 -"
              : `보유 품목 ${won(summary.data?.item_count)}개`
          }
        />
        <StatCard
          label="매출 / 매출원가"
          value={stat(margin, (m) => `₩${won(m.revenue)} / ₩${won(m.cogs)}`)}
        />
        <StatCard
          label="매출총이익"
          value={stat(margin, (m) => `₩${won(m.gross_profit)}`)}
          foot={
            margin.isPending || margin.isError
              ? "이익률 -"
              : `이익률 ${((margin.data?.margin_rate ?? 0) * 100).toFixed(1)}%`
          }
        />
      </div>

      <Panel>
        <Toolbar>
          <input
            type="text"
            placeholder="품목 검색"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            aria-label="품목 검색"
          />
          <select
            value={warehouseId}
            onChange={(e) => {
              setWarehouseId(e.target.value);
              setPage(1);
            }}
            aria-label="창고 필터"
            title="창고 필터"
          >
            <option value="">전체 창고</option>
            {(warehouses.data?.items ?? []).map((w) => (
              <option key={w.id} value={String(w.id)}>
                {whLabel(w)}
              </option>
            ))}
          </select>
        </Toolbar>
        <DataTable
          columns={columns}
          rows={list.data?.items}
          error={list.error}
          onRetry={() => void list.refetch()}
          busy={list.isFetching && !list.isPending}
          rowKey={(v) => v.item_id}
          loading={list.isPending}
          emptyText={"데이터가 없습니다."}
        />
        {list.data && (
          <Pager
            page={list.data.page}
            pages={list.data.pages}
            total={list.data.total}
            onPageChange={setPage}
            busy={list.isFetching && !list.isPending}
          />
        )}
      </Panel>
    </section>
  );
}
