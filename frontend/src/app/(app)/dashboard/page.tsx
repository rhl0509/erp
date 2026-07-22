"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import DataTable, { type Column } from "@/components/ui/DataTable";
import { Panel, PanelHead } from "@/components/ui/Panel";
import StatCard from "@/components/ui/StatCard";
import Tag from "@/components/ui/Tag";
import { client, unwrap } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { useAuth } from "@/lib/auth/AuthProvider";
import { won } from "@/lib/format";

import styles from "./page.module.css";

type Overview = components["schemas"]["SalesPurchaseOverview"];
type StockLevel = components["schemas"]["StockLevelOut"];
type Transaction = components["schemas"]["TransactionOut"];

/** 금액 카드 공용 포맷 — 원 단위 콤마 */
const money = (n: number | null | undefined) => `${won(Math.round(Number(n) || 0))}원`;

/**
 * 대시보드(풀버전) — 기존 조회 엔드포인트를 조합해 한눈에 보는 경영 요약:
 * KPI(이번 달 매출·매입, 미수/미지급, 재고평가) · 6개월 매입매출 추이 ·
 * 재고부족 알림 · 최근 거래. 백엔드 신규 없이 stats/aging/valuation/alerts/
 * transactions 를 재사용하며, 권한 없는 카드는 조회 자체를 건너뛴다.
 */
export default function DashboardPage() {
  const { me, can } = useAuth();
  const canStock = can("stock:read");
  const canPayment = can("payment:read");

  const overview = useQuery({
    queryKey: ["stats", "overview"],
    queryFn: async () => unwrap(await client.GET("/api/stats/overview")),
    enabled: canStock,
  });

  const valuation = useQuery({
    queryKey: ["valuation", "summary"],
    queryFn: async () =>
      unwrap(await client.GET("/api/costing/valuation/summary")),
    enabled: canStock,
  });

  const ar = useQuery({
    queryKey: ["aging", "AR", "summary"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/reports/aging", {
          params: { query: { kind: "AR" } },
        }),
      ),
    enabled: canPayment,
  });

  const ap = useQuery({
    queryKey: ["aging", "AP", "summary"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/reports/aging", {
          params: { query: { kind: "AP" } },
        }),
      ),
    enabled: canPayment,
  });

  const lowStock = useQuery({
    queryKey: ["stock", "alerts", "dashboard"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/stock/alerts", {
          params: { query: { page: 1, page_size: 5 } },
        }),
      ),
    enabled: canStock,
  });

  const recent = useQuery({
    queryKey: ["reports", "transactions", "recent"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/reports/transactions", {
          params: {
            query: { page: 1, page_size: 6, sort: "created_at", order: "desc" },
          },
        }),
      ),
    enabled: canStock,
  });

  const roleNames = me?.roles.map((r) => r.name).join(", ") || "-";

  // KPI 값 렌더 — 로딩/오류/무권한 상태 처리
  const kpi = (
    enabled: boolean,
    q: { isPending: boolean; isError: boolean },
    value: ReactNode,
  ): ReactNode => {
    if (!enabled) return "—";
    if (q.isPending) return "…";
    if (q.isError) return "-";
    return value;
  };

  return (
    <section>
      <div className={styles.pageHead}>
        <div>
          <h1 className={styles.title}>대시보드</h1>
          <p className={styles.subtitle}>
            매입·매출, 채권/채무, 재고를 한눈에 확인합니다.
          </p>
        </div>
      </div>

      {/* ---- KPI 카드 ---- */}
      <div className={styles.cards}>
        <StatCard
          label="이번 달 매출"
          value={kpi(canStock, overview, money(overview.data?.this_month.sales_amount))}
          foot={
            canStock && overview.data
              ? `매입 ${money(overview.data.this_month.purchase_amount)}`
              : "매입 —"
          }
        />
        <StatCard
          label="미수금 (AR)"
          smallValue
          value={kpi(canPayment, ar, money(ar.data?.total))}
          foot="미결제 매출채권"
        />
        <StatCard
          label="미지급금 (AP)"
          smallValue
          value={kpi(canPayment, ap, money(ap.data?.total))}
          foot="미결제 매입채무"
        />
        <StatCard
          label="재고 평가액"
          smallValue
          value={kpi(canStock, valuation, money(valuation.data?.total_value))}
          foot={
            canStock && valuation.data
              ? `보유 품목 ${won(valuation.data.item_count)}종`
              : "이동평균 기준"
          }
        />
      </div>

      {/* ---- 6개월 매입/매출 추이 ---- */}
      {canStock && (
        <Panel>
          <PanelHead title="최근 6개월 매입·매출 추이" />
          <MonthlyTrend
            data={overview.data}
            loading={overview.isPending}
            error={overview.isError ? overview.error : null}
          />
        </Panel>
      )}

      {/* ---- 재고부족 + 최근 거래 ---- */}
      {canStock && (
        <div className={styles.twoCol}>
          <Panel>
            <PanelHead
              title="재고 부족 알림"
              actions={
                <Link href="/stock" className={styles.moreLink}>
                  전체 보기
                </Link>
              }
            />
            <LowStockList
              rows={lowStock.data?.items}
              loading={lowStock.isPending}
              error={lowStock.isError ? lowStock.error : null}
            />
          </Panel>

          <Panel>
            <PanelHead
              title="최근 거래"
              actions={
                <Link href="/transactions" className={styles.moreLink}>
                  전체 보기
                </Link>
              }
            />
            <RecentTransactions
              rows={recent.data?.items}
              loading={recent.isPending}
              error={recent.isError ? recent.error : null}
            />
          </Panel>
        </div>
      )}

      {!canStock && (
        <Panel>
          <div className={styles.noPerm}>
            재고·매출 통계 조회 권한(stock:read)이 없습니다. 현재 역할: {roleNames}
          </div>
        </Panel>
      )}
    </section>
  );
}

// ==================== 6개월 추이 (monochrome 막대) ====================

function MonthlyTrend({
  data,
  loading,
  error,
}: {
  data: Overview | undefined;
  loading: boolean;
  error: unknown;
}) {
  if (loading) return <div className={styles.hint}>불러오는 중…</div>;
  if (error) return <div className={styles.hint}>{errorMessage(error)}</div>;
  if (!data) return <div className={styles.hint}>데이터가 없습니다.</div>;

  const rows = data.monthly;
  const max = Math.max(
    1,
    ...rows.map((r) => Math.max(r.sales_amount, r.purchase_amount)),
  );
  const pct = (v: number) => `${(v / max) * 100}%`;

  return (
    <div className={styles.trend}>
      <div className={styles.legend}>
        <span className={styles.legendSales}>매출</span>
        <span className={styles.legendPurchase}>매입</span>
      </div>
      {rows.map((r) => (
        <div key={r.period} className={styles.trendRow}>
          <span className={styles.trendPeriod}>{r.period}</span>
          <div className={styles.trendBars}>
            <div className={styles.barLine}>
              <div
                className={`${styles.bar} ${styles.barSales}`}
                style={{ width: pct(r.sales_amount) }}
              />
              <span className={styles.barVal}>{won(r.sales_amount)}</span>
            </div>
            <div className={styles.barLine}>
              <div
                className={`${styles.bar} ${styles.barPurchase}`}
                style={{ width: pct(r.purchase_amount) }}
              />
              <span className={styles.barVal}>{won(r.purchase_amount)}</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ==================== 재고 부족 리스트 ====================

function LowStockList({
  rows,
  loading,
  error,
}: {
  rows: StockLevel[] | undefined;
  loading: boolean;
  error: unknown;
}) {
  const columns: Column<StockLevel>[] = [
    { key: "item_name", header: "품목", render: (r) => r.item_name },
    {
      key: "on_hand",
      header: "현재고 / 안전",
      num: true,
      render: (r) => `${won(r.on_hand)} / ${won(r.safety_stock)}`,
    },
    {
      key: "shortage",
      header: "부족",
      num: true,
      render: (r) => <span className={styles.shortage}>{won(r.shortage)}</span>,
    },
  ];
  return (
    <DataTable
      columns={columns}
      rows={error ? [] : rows}
      rowKey={(r) => r.item_id}
      loading={loading}
      emptyText={error ? errorMessage(error) : "부족한 품목이 없습니다."}
    />
  );
}

// ==================== 최근 거래 ====================

function RecentTransactions({
  rows,
  loading,
  error,
}: {
  rows: Transaction[] | undefined;
  loading: boolean;
  error: unknown;
}) {
  const columns: Column<Transaction>[] = [
    {
      key: "created_at",
      header: "일자",
      render: (r) => r.created_at.slice(0, 10),
    },
    {
      key: "kind",
      header: "구분",
      render: (r) =>
        r.kind === "purchase" ? (
          <Tag variant="supp">매입</Tag>
        ) : (
          <Tag variant="cust">매출</Tag>
        ),
    },
    {
      key: "item_name",
      header: "품목",
      render: (r) => r.item_name,
    },
    { key: "amount", header: "금액", num: true, render: (r) => won(r.amount) },
  ];
  return (
    <DataTable
      columns={columns}
      rows={error ? [] : rows}
      rowKey={(r) => r.id}
      loading={loading}
      emptyText={error ? errorMessage(error) : "거래 내역이 없습니다."}
    />
  );
}
