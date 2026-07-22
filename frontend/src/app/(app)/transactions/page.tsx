"use client";

import { useEffect, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import Button from "@/components/ui/Button";
import DataTable, {
  Pager,
  type Column,
  type SortState,
} from "@/components/ui/DataTable";
import { Panel, PanelHead, Spacer, Toolbar } from "@/components/ui/Panel";
import StatCard from "@/components/ui/StatCard";
import Tag, { type TagVariant } from "@/components/ui/Tag";
import { useToast } from "@/components/ui/Toast";
import { client, downloadFile, unwrap } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { dateOnly, won } from "@/lib/format";

import styles from "./page.module.css";

type Txn = components["schemas"]["TransactionOut"];
type TxnPeriod = components["schemas"]["TransactionPeriod"];
type PartnerSubtotal = components["schemas"]["TransactionPartnerSubtotal"];
type ItemSubtotal = components["schemas"]["TransactionItemSubtotal"];

/** 레거시 페이지당 10건 패리티 */
const PAGE_SIZE = 10;

/** 레거시 KIND_TAG 패리티 */
const KIND_TAG: Record<string, [TagVariant, string]> = {
  purchase: ["supp", "매입"],
  sales: ["cust", "매출"],
};

function kindTag(kind: string) {
  const entry = KIND_TAG[kind];
  return entry ? (
    <Tag variant={entry[0]}>{entry[1]}</Tag>
  ) : (
    <Tag variant="gray">{kind}</Tag>
  );
}

/**
 * 레거시 정렬 토글 패리티 — 같은 컬럼이면 방향 토글(DataTable 이 계산해 준 next),
 * 다른 컬럼이면 내림차순부터 시작한다(DataTable 기본은 asc 시작이라 여기서 보정).
 */
function legacySortNext(
  current: SortState | undefined,
  next: SortState,
): SortState {
  return current && current.key === next.key
    ? next
    : { key: next.key, order: "desc" };
}

/** 레거시 subtotalSorted 패리티 — 소계 배열 클라이언트 정렬(이름은 ko 로케일 비교) */
function sortSubtotals<T>(
  rows: T[] | undefined,
  sort: SortState | undefined,
  nameKey: keyof T,
): T[] | undefined {
  if (!rows || !sort) return rows;
  const key = sort.key as keyof T;
  const dir = sort.order === "asc" ? 1 : -1;
  return [...rows].sort((a, b) =>
    key === nameKey
      ? String(a[key]).localeCompare(String(b[key]), "ko") * dir
      : (Number(a[key]) - Number(b[key])) * dir,
  );
}

/** 레거시 csvCell 패리티 — 쉼표/따옴표/개행 포함 시 따옴표로 감싼다 */
function csvCell(v: unknown): string {
  const s = String(v ?? "");
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** 레거시 downloadCsv 패리티 — 헤더+행을 CSV 로 클라이언트에서 즉시 다운로드(BOM 포함) */
function downloadCsv(
  filename: string,
  header: string[],
  rows: (string | number)[][],
): void {
  const text =
    "﻿" +
    [header, ...rows].map((r) => r.map(csvCell).join(",")).join("\r\n");
  const blob = new Blob([text], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/**
 * 매입/매출 리포트(슬라이스 6) — 레거시 #transactions 패리티:
 * 합계 stat 2장, 기간별(월/일) 합계, 거래처별·품목별 소계(클라이언트 정렬 + CSV),
 * 내역 목록(검색·구분·거래처·기간 필터, 서버 정렬, 페이지네이션), 엑셀 내보내기.
 */
export default function TransactionsPage() {
  const toast = useToast();

  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [partnerId, setPartnerId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  // 목록 정렬(서버사이드) — 레거시 txnState.sort/order 기본값 패리티
  const [sort, setSort] = useState<SortState>({
    key: "created_at",
    order: "desc",
  });
  const [groupBy, setGroupBy] = useState("month");
  // 소계 정렬(클라이언트사이드) — 재요청 없이 정렬, 엑셀 export 에도 반영
  const [partnerSort, setPartnerSort] = useState<SortState | undefined>();
  const [itemSort, setItemSort] = useState<SortState | undefined>();

  // 레거시 debounce(…, 300) 패리티 — 입력 후 300ms 뒤 검색 적용 + 1페이지로
  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(qInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [qInput]);

  // 목록/합계/소계/내보내기가 같은 필터를 쓰도록 한 곳에서 만든다(레거시 txnQuery 패리티)
  const filters = useMemo(
    () => ({
      ...(q ? { q } : {}),
      ...(kind ? { kind } : {}),
      ...(partnerId ? { partner_id: Number(partnerId) } : {}),
      ...(dateFrom ? { date_from: dateFrom } : {}),
      ...(dateTo ? { date_to: dateTo } : {}),
    }),
    [q, kind, partnerId, dateFrom, dateTo],
  );

  // 거래처 필터 옵션(레거시 셀렉트 채우기 패리티 — page_size=100)
  const partnerOptions = useQuery({
    queryKey: ["partners", "options"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/partners", {
          params: { query: { page: 1, page_size: 100 } },
        }),
      ),
  });

  const list = useQuery({
    queryKey: [
      "reports",
      "transactions",
      "list",
      { ...filters, page, sort: sort.key, order: sort.order },
    ],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/reports/transactions", {
          params: {
            query: {
              page,
              page_size: PAGE_SIZE,
              sort: sort.key,
              order: sort.order,
              ...filters,
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  const summary = useQuery({
    queryKey: ["reports", "transactions", "summary", filters],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/reports/transactions/summary", {
          params: { query: filters },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  const periods = useQuery({
    queryKey: ["reports", "transactions", "periods", { ...filters, groupBy }],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/reports/transactions/periods", {
          params: { query: { group_by: groupBy, ...filters } },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  const partnerSubtotals = useQuery({
    queryKey: ["reports", "transactions", "partners", filters],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/reports/transactions/partners", {
          params: { query: filters },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  const itemSubtotals = useQuery({
    queryKey: ["reports", "transactions", "items", filters],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/reports/transactions/items", {
          params: { query: filters },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  // 이미 받아온 소계를 클라이언트에서 정렬해 렌더(레거시 renderTxnPartners/Items 패리티)
  const sortedPartners = useMemo(
    () => sortSubtotals(partnerSubtotals.data, partnerSort, "partner_name"),
    [partnerSubtotals.data, partnerSort],
  );
  const sortedItems = useMemo(
    () => sortSubtotals(itemSubtotals.data, itemSort, "item_name"),
    [itemSubtotals.data, itemSort],
  );

  // 현재 필터(검색·구분·거래처·기간)와 소계 정렬을 그대로 반영해 엑셀로 내보낸다
  // (레거시 exportTransactions 패리티 — export 는 소계 정렬 파라미터만 받는다)
  const exportExcel = () => {
    const qs = new URLSearchParams();
    if (q) qs.set("q", q);
    if (kind) qs.set("kind", kind);
    if (partnerId) qs.set("partner_id", partnerId);
    if (dateFrom) qs.set("date_from", dateFrom);
    if (dateTo) qs.set("date_to", dateTo);
    if (partnerSort) {
      qs.set("partner_sort", partnerSort.key);
      qs.set("partner_order", partnerSort.order);
    }
    if (itemSort) {
      qs.set("item_sort", itemSort.key);
      qs.set("item_order", itemSort.order);
    }
    void downloadFile(
      `/api/reports/transactions/export?${qs.toString()}`,
      "매입매출_내역.xlsx",
    )
      .then(() => toast("엑셀 파일을 내려받았습니다."))
      .catch((err) => toast(errorMessage(err), true));
  };

  // 현재 정렬된 소계를 CSV 로 내보낸다(레거시 exportTxnPartnersCsv/ItemsCsv 패리티)
  const exportPartnersCsv = () => {
    downloadCsv(
      "거래처별_소계.csv",
      ["거래처", "매입 합계", "매출 합계", "매입 건수", "매출 건수"],
      (sortedPartners ?? []).map((p) => [
        p.partner_name,
        p.purchase_amount,
        p.sales_amount,
        p.purchase_count,
        p.sales_count,
      ]),
    );
    toast("CSV 파일을 내려받았습니다.");
  };
  const exportItemsCsv = () => {
    downloadCsv(
      "품목별_소계.csv",
      ["품목코드", "품목명", "매입 합계", "매출 합계", "매입 건수", "매출 건수"],
      (sortedItems ?? []).map((i) => [
        i.item_code,
        i.item_name,
        i.purchase_amount,
        i.sales_amount,
        i.purchase_count,
        i.sales_count,
      ]),
    );
    toast("CSV 파일을 내려받았습니다.");
  };

  const periodColumns: Column<TxnPeriod>[] = [
    { key: "period", header: "기간" },
    {
      key: "purchase_amount",
      header: "매입 합계",
      num: true,
      render: (p) => `₩${won(p.purchase_amount)}`,
    },
    {
      key: "sales_amount",
      header: "매출 합계",
      num: true,
      render: (p) => `₩${won(p.sales_amount)}`,
    },
    {
      key: "purchase_count",
      header: "매입 건수",
      num: true,
      render: (p) => won(p.purchase_count),
    },
    {
      key: "sales_count",
      header: "매출 건수",
      num: true,
      render: (p) => won(p.sales_count),
    },
  ];

  const partnerColumns: Column<PartnerSubtotal>[] = [
    { key: "partner_name", header: "거래처", sortKey: "partner_name" },
    {
      key: "purchase_amount",
      header: "매입 합계",
      num: true,
      sortKey: "purchase_amount",
      render: (p) => `₩${won(p.purchase_amount)}`,
    },
    {
      key: "sales_amount",
      header: "매출 합계",
      num: true,
      sortKey: "sales_amount",
      render: (p) => `₩${won(p.sales_amount)}`,
    },
    {
      key: "purchase_count",
      header: "매입 건수",
      num: true,
      sortKey: "purchase_count",
      render: (p) => won(p.purchase_count),
    },
    {
      key: "sales_count",
      header: "매출 건수",
      num: true,
      sortKey: "sales_count",
      render: (p) => won(p.sales_count),
    },
  ];

  const itemColumns: Column<ItemSubtotal>[] = [
    {
      key: "item_name",
      header: "품목",
      sortKey: "item_name",
      render: (i) => `${i.item_name} (${i.item_code})`,
    },
    {
      key: "purchase_amount",
      header: "매입 합계",
      num: true,
      sortKey: "purchase_amount",
      render: (i) => `₩${won(i.purchase_amount)}`,
    },
    {
      key: "sales_amount",
      header: "매출 합계",
      num: true,
      sortKey: "sales_amount",
      render: (i) => `₩${won(i.sales_amount)}`,
    },
    {
      key: "purchase_count",
      header: "매입 건수",
      num: true,
      sortKey: "purchase_count",
      render: (i) => won(i.purchase_count),
    },
    {
      key: "sales_count",
      header: "매출 건수",
      num: true,
      sortKey: "sales_count",
      render: (i) => won(i.sales_count),
    },
  ];

  const listColumns: Column<Txn>[] = [
    {
      key: "created_at",
      header: "일자",
      sortKey: "created_at",
      render: (t) => dateOnly(t.created_at),
    },
    { key: "movement_no", header: "번호", code: true, sortKey: "movement_no" },
    { key: "kind", header: "구분", render: (t) => kindTag(t.kind) },
    {
      key: "partner_name",
      header: "거래처",
      render: (t) => t.partner_name || "-",
    },
    {
      key: "item_name",
      header: "품목",
      render: (t) => `${t.item_name} (${t.item_code})`,
    },
    {
      key: "quantity",
      header: "수량",
      num: true,
      sortKey: "quantity",
      render: (t) => won(t.quantity),
    },
    {
      key: "unit_price",
      header: "단가",
      num: true,
      sortKey: "unit_price",
      render: (t) => won(t.unit_price),
    },
    {
      key: "amount",
      header: "금액",
      num: true,
      sortKey: "amount",
      render: (t) => `₩${won(t.amount)}`,
    },
  ];

  const statValue = (amount?: number) =>
    summary.isPending ? "…" : summary.isError ? "-" : `₩${won(amount)}`;
  const statFoot = (count?: number) =>
    summary.isPending || summary.isError ? "건수 -" : `${won(count)}건`;

  return (
    <section>
      <div className={styles.pageHead}>
        <div>
          <h1 className={styles.title}>매입/매출 내역</h1>
          <p className={styles.subtitle}>
            거래처와 연동된 입고(매입)·출고(매출) 내역입니다.
          </p>
        </div>
      </div>

      <div className={styles.cards}>
        <StatCard
          label="매입 합계"
          value={statValue(summary.data?.purchase_amount)}
          foot={statFoot(summary.data?.purchase_count)}
        />
        <StatCard
          label="매출 합계"
          value={statValue(summary.data?.sales_amount)}
          foot={statFoot(summary.data?.sales_count)}
        />
      </div>

      {/* ==================== 기간별 합계 ==================== */}
      <Panel className={styles.stack}>
        <PanelHead
          title="기간별 합계"
          actions={
            <select
              className={styles.headSelect}
              value={groupBy}
              onChange={(e) => setGroupBy(e.target.value)}
              aria-label="집계 단위"
            >
              <option value="month">월별</option>
              <option value="day">일별</option>
            </select>
          }
        />
        <DataTable
          columns={periodColumns}
          rows={periods.data}
          error={periods.error}
          onRetry={() => void periods.refetch()}
          busy={periods.isFetching && !periods.isPending}
          rowKey={(p) => p.period}
          loading={periods.isPending}
          emptyText={"데이터가 없습니다."}
        />
      </Panel>

      {/* ==================== 거래처별 소계 ==================== */}
      <Panel className={styles.stack}>
        <PanelHead
          title="거래처별 소계"
          actions={
            <Button variant="ghost" size="sm" onClick={exportPartnersCsv}>
              CSV
            </Button>
          }
        />
        <DataTable
          columns={partnerColumns}
          rows={sortedPartners}
          error={partnerSubtotals.error}
          onRetry={() => void partnerSubtotals.refetch()}
          busy={partnerSubtotals.isFetching && !partnerSubtotals.isPending}
          rowKey={(p) => p.partner_id ?? "none"}
          loading={partnerSubtotals.isPending}
          emptyText={
            partnerSubtotals.isError
              ? errorMessage(partnerSubtotals.error)
              : "데이터가 없습니다."
          }
          sort={partnerSort}
          onSortChange={(next) =>
            setPartnerSort(legacySortNext(partnerSort, next))
          }
        />
      </Panel>

      {/* ==================== 품목별 소계 ==================== */}
      <Panel className={styles.stack}>
        <PanelHead
          title="품목별 소계"
          actions={
            <Button variant="ghost" size="sm" onClick={exportItemsCsv}>
              CSV
            </Button>
          }
        />
        <DataTable
          columns={itemColumns}
          rows={sortedItems}
          error={itemSubtotals.error}
          onRetry={() => void itemSubtotals.refetch()}
          busy={itemSubtotals.isFetching && !itemSubtotals.isPending}
          rowKey={(i) => i.item_id}
          loading={itemSubtotals.isPending}
          emptyText={
            itemSubtotals.isError
              ? errorMessage(itemSubtotals.error)
              : "데이터가 없습니다."
          }
          sort={itemSort}
          onSortChange={(next) => setItemSort(legacySortNext(itemSort, next))}
        />
      </Panel>

      {/* ==================== 내역 목록 ==================== */}
      <Panel>
        <Toolbar>
          <input
            type="text"
            placeholder="번호·품목·거래처 검색"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            aria-label="매입/매출 검색"
          />
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              setPage(1);
            }}
            aria-label="구분 필터"
          >
            <option value="">전체 구분</option>
            <option value="purchase">매입</option>
            <option value="sales">매출</option>
          </select>
          <select
            value={partnerId}
            onChange={(e) => {
              setPartnerId(e.target.value);
              setPage(1);
            }}
            aria-label="거래처 필터"
          >
            <option value="">전체 거래처</option>
            {(partnerOptions.data?.items ?? []).map((p) => (
              <option key={p.id} value={String(p.id)}>
                {p.name} ({p.code})
              </option>
            ))}
          </select>
          <input
            type="date"
            className={styles.dateInput}
            value={dateFrom}
            onChange={(e) => {
              setDateFrom(e.target.value);
              setPage(1);
            }}
            aria-label="시작일"
          />
          <span className={styles.tilde}>~</span>
          <input
            type="date"
            className={styles.dateInput}
            value={dateTo}
            onChange={(e) => {
              setDateTo(e.target.value);
              setPage(1);
            }}
            aria-label="종료일"
          />
          <Spacer />
          <Button variant="ghost" onClick={exportExcel}>
            엑셀 내보내기
          </Button>
        </Toolbar>
        <DataTable
          columns={listColumns}
          rows={list.data?.items}
          error={list.error}
          onRetry={() => void list.refetch()}
          busy={list.isFetching && !list.isPending}
          rowKey={(t) => t.id}
          loading={list.isPending}
          emptyText={"데이터가 없습니다."}
          sort={sort}
          onSortChange={(next) => {
            setSort(legacySortNext(sort, next));
            setPage(1);
          }}
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
