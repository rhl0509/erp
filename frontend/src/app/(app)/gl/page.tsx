"use client";

import { useEffect, useMemo, useState } from "react";
import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import Button from "@/components/ui/Button";
import DataTable, { Pager, type Column } from "@/components/ui/DataTable";
import Modal from "@/components/ui/Modal";
import { Panel, PanelHead, Spacer, Toolbar } from "@/components/ui/Panel";
import StatCard from "@/components/ui/StatCard";
import Tag, { type TagVariant } from "@/components/ui/Tag";
import { useConfirm } from "@/components/ui/ConfirmProvider";
import { useToast } from "@/components/ui/Toast";
import { client, unwrap } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { useAuth } from "@/lib/auth/AuthProvider";
import { won } from "@/lib/format";

import styles from "./page.module.css";

type TrialBalanceRow = components["schemas"]["TrialBalanceRow"];
type ReconcileItem = components["schemas"]["ReconcileItem"];
type JournalEntry = components["schemas"]["JournalEntryOut"];
type JournalLine = components["schemas"]["JournalLineOut"];
type LedgerOut = components["schemas"]["LedgerOut"];
type IncomeStatementOut = components["schemas"]["IncomeStatementOut"];
type BalanceSheetOut = components["schemas"]["BalanceSheetOut"];
type FinancialRow = components["schemas"]["FinancialRow"];

/** 레거시 페이지당 10건 패리티(다른 목록 페이지와 동일) */
const PAGE_SIZE = 10;

/** 합계/기초 등 클라이언트 합성 행 식별용(실제 코드·id 와 겹치지 않는 값) */
const TOTAL_ROW_CODE = "__total__";

/** GL 금액 공용 포맷 — 백엔드 float(Decimal 유래) 를 원 단위 반올림 후 콤마 */
const amt = (n: number | null | undefined) => won(Math.round(Number(n) || 0));

/** 라인 차/대 칸 — 0 이면 비워 원장 가독성 유지(합계 행은 0 도 표시) */
const cell = (n: number, force = false) => (force || n !== 0 ? amt(n) : "");

/** 계정 유형 Tag(모노크롬 변형 매핑) */
const TYPE_TAG: Record<string, [TagVariant, string]> = {
  asset: ["default", "자산"],
  liability: ["supp", "부채"],
  equity: ["both", "자본"],
  revenue: ["cust", "수익"],
  expense: ["gray", "비용"],
};

function typeTag(accountType: string) {
  const entry = TYPE_TAG[accountType];
  return entry ? (
    <Tag variant={entry[0]}>{entry[1]}</Tag>
  ) : (
    <Tag variant="gray">{accountType}</Tag>
  );
}

/** 전표 출처 Tag — 백엔드 source_type(MOVEMENT/PAYMENT/MANUAL/CLOSING) */
const SOURCE_TAG: Record<string, [TagVariant, string]> = {
  MOVEMENT: ["supp", "재고이동"],
  PAYMENT: ["cust", "결제"],
  MANUAL: ["gray", "수동"],
  CLOSING: ["both", "기간마감"],
};

function sourceTag(sourceType: string) {
  const entry = SOURCE_TAG[sourceType];
  return entry ? (
    <Tag variant={entry[0]}>{entry[1]}</Tag>
  ) : (
    <Tag variant="gray">{sourceType}</Tag>
  );
}

/** 계정 셀렉트 라벨 — "1130 상품" */
const accountLabel = (r: TrialBalanceRow) => `${r.account_code} ${r.account_name}`;

type GlTab = "trial" | "journal" | "ledger" | "income" | "balance" | "periods";

const TABS: [GlTab, string][] = [
  ["trial", "시산표"],
  ["journal", "분개장"],
  ["ledger", "계정별원장"],
  ["income", "손익계산서"],
  ["balance", "재무상태표"],
  ["periods", "기간 마감"],
];

/**
 * 총계정원장(GL) — 복식부기 전표 조회 뷰를 탭으로 묶는다:
 * 시산표(+재대사 상태·재전기), 분개장(전표 목록·상세 모달), 계정별원장(러닝밸런스),
 * 간이 손익계산서·재무상태표, 기간 마감(월 마감·해제).
 * 조회는 payment:read(nav 게이팅과 동일), 재전기·마감은 payment:write.
 */
export default function GlPage() {
  const [tab, setTab] = useState<GlTab>("trial");

  return (
    <section>
      <div className={styles.pageHead}>
        <div>
          <h2 className={styles.title}>총계정원장</h2>
          <p className={styles.subtitle}>
            복식부기 전표(GL) — 시산표 · 분개장 · 계정별원장 · 손익계산서 · 재무상태표 · 기간 마감
          </p>
        </div>
      </div>

      <div className={styles.tabs} role="tablist" aria-label="총계정원장 보기">
        {TABS.map(([key, label]) => (
          <Button
            key={key}
            variant={tab === key ? "primary" : "ghost"}
            size="sm"
            onClick={() => setTab(key)}
            role="tab"
            aria-selected={tab === key}
          >
            {label}
          </Button>
        ))}
      </div>

      {tab === "trial" && <TrialBalanceView />}
      {tab === "journal" && <JournalView />}
      {tab === "ledger" && <LedgerView />}
      {tab === "income" && <IncomeStatementView />}
      {tab === "balance" && <BalanceSheetView />}
      {tab === "periods" && <PeriodsView />}
    </section>
  );
}

// ==================== 기간 마감 ====================

type PeriodRow = components["schemas"]["PeriodOut"];

/**
 * 월별 회계기간 마감. 마감하면 그 달 **이하**의 모든 기간이 얼어붙는다 —
 * 수기 전표·과거 날짜 결제·원천 삭제가 전부 서버에서 400(period_closed)으로 막힌다.
 * 마감 시 손익계정은 3110 이월이익잉여금으로 대체된다(CLOSING 전표).
 *
 * 진행 중인 이번 달은 마감할 수 없다(서버 규칙) — 마감하면 그 달의 남은 영업이 막힌다.
 * 해제는 최근 마감분부터 역순으로만 가능하다.
 */
function PeriodsView() {
  const { can } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const canClose = can("payment:write");
  const [busy, setBusy] = useState(false);

  const periods = useQuery({
    queryKey: ["gl", "periods"],
    queryFn: async () => unwrap(await client.GET("/api/gl/periods")),
    placeholderData: keepPreviousData,
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["gl"] });

  const doClose = async (p: PeriodRow) => {
    const ok = await confirm({
      title: `${p.period} 기간 마감`,
      message: (
        <>
          <b>{p.period}</b> 을(를) 마감할까요? 당기순이익 <b>{amt(p.net_income)}원</b>이
          이월이익잉여금(3110)으로 대체되고, <b>{p.period} 이전의 모든 기간이 얼어붙습니다</b> —
          그 기간에는 수기 전표·과거 날짜 결제·원천 삭제가 더 이상 불가능합니다.
          되돌리려면 마감 해제를 하면 됩니다.
        </>
      ),
      confirmText: "마감",
    });
    if (!ok) return;
    setBusy(true);
    try {
      const res = unwrap(
        await client.POST("/api/gl/periods/{period}/close", {
          params: { path: { period: p.period } },
        }),
      );
      toast(
        res.closing_entry_no
          ? `${p.period} 마감 완료 — 마감 전표 ${res.closing_entry_no}`
          : `${p.period} 마감 완료 (손익 활동 없음)`,
      );
      refresh();
    } catch (err) {
      toast(errorMessage(err), true);
    } finally {
      setBusy(false);
    }
  };

  const doReopen = async (p: PeriodRow) => {
    const ok = await confirm({
      title: `${p.period} 마감 해제`,
      message: (
        <>
          <b>{p.period}</b> 의 마감을 해제할까요? 마감 전표({p.closing_entry_no || "없음"})가
          삭제되고 손익이 손익계정으로 되돌아옵니다. 수정을 마치면 <b>다시 마감해야</b>
          재무상태표의 이월이익잉여금이 맞습니다. (해제 이력은 감사 로그에 남습니다.)
        </>
      ),
      confirmText: "마감 해제",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      unwrap(
        await client.POST("/api/gl/periods/{period}/reopen", {
          params: { path: { period: p.period } },
        }),
      );
      toast(`${p.period} 마감을 해제했습니다.`);
      refresh();
    } catch (err) {
      toast(errorMessage(err), true);
    } finally {
      setBusy(false);
    }
  };

  const data = periods.data;
  const rows = data?.periods ?? [];
  // 해제는 최근 마감분부터 역순으로만 — 마지막으로 마감된 기간만 버튼을 노출한다.
  const lastClosed = [...rows].reverse().find((p) => p.status === "closed")?.period ?? "";

  const columns: Column<PeriodRow>[] = [
    { key: "period", header: "회계기간", code: true },
    {
      key: "status",
      header: "상태",
      render: (p) =>
        p.status === "closed" ? <Tag variant="both">마감</Tag> : <Tag variant="gray">열림</Tag>,
    },
    {
      key: "net_income",
      header: "당기순이익",
      num: true,
      render: (p) => amt(p.net_income),
    },
    {
      key: "closing_entry_no",
      header: "마감 전표",
      code: true,
      render: (p) => p.closing_entry_no || "-",
    },
    {
      key: "closed_at",
      header: "마감일시",
      render: (p) =>
        p.closed_at
          ? `${new Date(p.closed_at).toLocaleString("ko-KR")}${p.closed_by ? ` · ${p.closed_by}` : ""}`
          : "-",
    },
  ];

  return (
    <Panel>
      <PanelHead title="기간 마감" />
      <div className={styles.note}>
        {data?.next_closable
          ? `다음 마감 대상: ${data.next_closable} — 오래된 달부터 순서대로만 마감할 수 있습니다. 마감하면 그 달 이하의 기간이 얼어붙습니다(소급 기표 차단).`
          : "마감할 수 있는 지난 기간이 없습니다. 진행 중인 이번 달은 마감할 수 없습니다(그 달의 남은 영업이 막히므로)."}
      </div>

      <DataTable
        columns={columns}
        rows={periods.isError ? [] : rows}
        rowKey={(p) => p.period}
        loading={periods.isPending}
        emptyText={periods.isError ? errorMessage(periods.error) : "회계기간이 없습니다."}
        actions={(p) => (
          <>
            {canClose && p.status !== "closed" && p.period === data?.next_closable && (
              <Button size="sm" disabled={busy} onClick={() => void doClose(p)}>
                마감
              </Button>
            )}
            {canClose && p.status === "closed" && p.period === lastClosed && (
              <Button
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={() => void doReopen(p)}
              >
                마감 해제
              </Button>
            )}
          </>
        )}
      />
    </Panel>
  );
}

// ==================== 시산표 + 재대사 + 재전기 ====================

function TrialBalanceView() {
  const { can } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const canRebuild = can("payment:write");
  const [rebuilding, setRebuilding] = useState(false);

  const trial = useQuery({
    queryKey: ["gl", "trial-balance"],
    queryFn: async () => unwrap(await client.GET("/api/gl/trial-balance")),
    placeholderData: keepPreviousData,
  });

  const recon = useQuery({
    queryKey: ["gl", "reconcile"],
    queryFn: async () => unwrap(await client.GET("/api/gl/reconcile")),
    placeholderData: keepPreviousData,
  });

  // 전체 재전기(관리 액션) — 성공 시 GL 전체(시산표·재대사·분개장·원장) refetch
  const doRebuild = async () => {
    const ok = await confirm({
      title: "GL 전체 재전기",
      message: (
        <>
          GL 전표 <b>전체</b>를 삭제하고 원천(재고이동·결제)에서 다시 전기합니다. 수기 전표는
          보존되며, 재대사 검증에 실패하면 전체가 롤백됩니다. 계속할까요?
        </>
      ),
      confirmText: "재전기",
      danger: true,
    });
    if (!ok) return;
    setRebuilding(true);
    try {
      const res = unwrap(await client.POST("/api/gl/rebuild"));
      toast(
        `재전기 완료 — 전표 ${won(res.total_entries)}건 (재고이동 ${won(res.movement_entries)} · 결제 ${won(res.payment_entries)})`,
      );
      void queryClient.invalidateQueries({ queryKey: ["gl"] });
    } catch (err) {
      toast(errorMessage(err), true);
    } finally {
      setRebuilding(false);
    }
  };

  // 재대사 카드 값 — 일치/불일치(모노크롬 강조) + GL vs 실제 원천값
  const reconValue = (item: ReconcileItem | undefined) =>
    recon.isPending ? (
      "…"
    ) : recon.isError || !item ? (
      "-"
    ) : item.ok ? (
      "일치"
    ) : (
      <span className={styles.mismatch}>차이 {amt(item.diff)}</span>
    );
  const reconFoot = (item: ReconcileItem | undefined) =>
    recon.isPending || recon.isError || !item
      ? "GL - · 실제 -"
      : `GL ${amt(item.gl)} · 실제 ${amt(item.expected)}`;

  const data = trial.data;
  // 하단 합계 행(Σ차·Σ대) 클라이언트 합성 — aging 합계 행 관례
  const rows = useMemo(() => {
    if (!data || data.rows.length === 0) return data?.rows;
    const totalRow: TrialBalanceRow = {
      account_code: TOTAL_ROW_CODE,
      account_name: data.balanced ? "합계 (차대 일치)" : "합계 (차대 불일치!)",
      account_type: "",
      normal_side: "",
      debit_total: data.total_debit,
      credit_total: data.total_credit,
      balance: 0,
    };
    return [...data.rows, totalRow];
  }, [data]);

  const isTotal = (r: TrialBalanceRow) => r.account_code === TOTAL_ROW_CODE;

  const columns: Column<TrialBalanceRow>[] = [
    {
      key: "account_code",
      header: "코드",
      code: true,
      render: (r) => (isTotal(r) ? "" : r.account_code),
    },
    { key: "account_name", header: "계정명" },
    {
      key: "account_type",
      header: "유형",
      render: (r) => (isTotal(r) ? "" : typeTag(r.account_type)),
    },
    {
      key: "debit_total",
      header: "차변",
      num: true,
      render: (r) => cell(r.debit_total, isTotal(r)),
    },
    {
      key: "credit_total",
      header: "대변",
      num: true,
      render: (r) => cell(r.credit_total, isTotal(r)),
    },
    {
      key: "balance",
      header: "잔액",
      num: true,
      render: (r) => (isTotal(r) ? "" : amt(r.balance)),
    },
  ];

  const reconOk = recon.data?.ok;

  return (
    <>
      <div className={styles.cards}>
        <StatCard
          smallValue
          label="재대사 상태 (GL ↔ 보조원장)"
          value={
            recon.isPending ? (
              "…"
            ) : recon.isError ? (
              "-"
            ) : reconOk ? (
              "정상"
            ) : (
              <span className={styles.mismatch}>불일치 — 재전기 권장</span>
            )
          }
          foot={
            recon.isPending || recon.isError || !recon.data
              ? "시산표 균형 -"
              : `시산표 균형 ${recon.data.trial.ok ? "OK" : "불일치"} (차 ${amt(recon.data.trial.debit)} · 대 ${amt(recon.data.trial.credit)})`
          }
        />
        <StatCard
          smallValue
          label="재고자산 1130 vs 재고평가"
          value={reconValue(recon.data?.inventory)}
          foot={reconFoot(recon.data?.inventory)}
        />
        <StatCard
          smallValue
          label="외상매출금 1120 vs 미수(AR)"
          value={reconValue(recon.data?.ar)}
          foot={reconFoot(recon.data?.ar)}
        />
        <StatCard
          smallValue
          label="외상매입금 2110 vs 미지급(AP)"
          value={reconValue(recon.data?.ap)}
          foot={reconFoot(recon.data?.ap)}
        />
      </div>

      <Panel>
        <PanelHead
          title="시산표"
          actions={
            canRebuild ? (
              <Button
                variant="danger"
                size="sm"
                onClick={() => void doRebuild()}
                disabled={rebuilding}
              >
                {rebuilding ? "재전기 중…" : "전체 재전기"}
              </Button>
            ) : undefined
          }
        />
        <DataTable
          columns={columns}
          rows={trial.isError ? [] : rows}
          rowKey={(r) => r.account_code}
          loading={trial.isPending}
          emptyText={
            trial.isError ? errorMessage(trial.error) : "데이터가 없습니다."
          }
          rowClassName={(r) =>
            isTotal(r)
              ? data?.balanced
                ? styles.totalRow
                : `${styles.totalRow} ${styles.warnRow}`
              : undefined
          }
        />
      </Panel>
    </>
  );
}

// ==================== 분개장 ====================

function JournalView() {
  const { can } = useAuth();
  const canWrite = can("payment:write");
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [sourceType, setSourceType] = useState("");
  const [accountCode, setAccountCode] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [manualOpen, setManualOpen] = useState(false);

  // 레거시 debounce(…, 300) 패리티 — 입력 후 300ms 뒤 검색 적용 + 1페이지로
  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(qInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [qInput]);

  // 계정 필터 옵션 — 시산표 활성 12계정(쿼리 캐시 공유)
  const accounts = useQuery({
    queryKey: ["gl", "trial-balance"],
    queryFn: async () => unwrap(await client.GET("/api/gl/trial-balance")),
  });

  const params = {
    q,
    sourceType,
    accountCode,
    dateFrom,
    dateTo,
    page,
  };
  const list = useQuery({
    queryKey: ["gl", "journal", params],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/gl/journal", {
          params: {
            query: {
              page,
              page_size: PAGE_SIZE,
              ...(q ? { q } : {}),
              ...(sourceType ? { source_type: sourceType } : {}),
              ...(accountCode ? { account_code: accountCode } : {}),
              ...(dateFrom ? { date_from: dateFrom } : {}),
              ...(dateTo ? { date_to: dateTo } : {}),
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  // 전표 금액 = 라인 차변 합(복식부기 불변식으로 대변 합과 같다)
  const entryAmount = (e: JournalEntry) =>
    e.lines.reduce((sum, l) => sum + l.debit, 0);

  const columns: Column<JournalEntry>[] = [
    { key: "entry_no", header: "전표번호", code: true },
    { key: "entry_date", header: "일자" },
    {
      key: "description",
      header: "적요",
      render: (e) => e.description || "-",
    },
    {
      key: "source_type",
      header: "출처",
      render: (e) => (
        <>
          {sourceTag(e.source_type)}
          {e.source_id !== null && e.source_id !== undefined && (
            <span className={styles.muted}> #{e.source_id}</span>
          )}
        </>
      ),
    },
    {
      key: "amount",
      header: "금액 (차=대)",
      num: true,
      render: (e) => amt(entryAmount(e)),
    },
    {
      key: "lines",
      header: "라인",
      num: true,
      render: (e) => won(e.lines.length),
    },
  ];

  return (
    <>
      <Panel>
        <Toolbar>
          <input
            type="text"
            placeholder="전표번호·적요 검색"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            aria-label="전표 검색"
          />
          <select
            value={sourceType}
            onChange={(e) => {
              setSourceType(e.target.value);
              setPage(1);
            }}
            aria-label="출처 필터"
          >
            <option value="">전체 출처</option>
            <option value="MOVEMENT">재고이동</option>
            <option value="PAYMENT">결제</option>
            <option value="MANUAL">수동</option>
          </select>
          <select
            value={accountCode}
            onChange={(e) => {
              setAccountCode(e.target.value);
              setPage(1);
            }}
            aria-label="계정 필터"
          >
            <option value="">전체 계정</option>
            {(accounts.data?.rows ?? []).map((r) => (
              <option key={r.account_code} value={r.account_code}>
                {accountLabel(r)}
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
            title="시작일"
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
            title="종료일"
          />
          <Spacer />
          {canWrite && (
            <Button variant="primary" size="sm" onClick={() => setManualOpen(true)}>
              수기 전표 등록
            </Button>
          )}
        </Toolbar>
        <DataTable
          columns={columns}
          rows={list.isError ? [] : list.data?.items}
          rowKey={(e) => e.id}
          loading={list.isPending}
          emptyText={
            list.isError ? errorMessage(list.error) : "데이터가 없습니다."
          }
          onRowClick={(e) => setDetailId(e.id)}
        />
        {list.data && (
          <Pager
            page={list.data.page}
            pages={list.data.pages}
            total={list.data.total}
            onPageChange={setPage}
          />
        )}
      </Panel>

      {detailId !== null && (
        <JournalEntryModal entryId={detailId} onClose={() => setDetailId(null)} />
      )}
      {manualOpen && <ManualEntryModal onClose={() => setManualOpen(false)} />}
    </>
  );
}

// ==================== 전표 상세 모달 ====================

/** 합계 행 식별용 라인 id(실제 라인 id 와 겹치지 않는 음수) */
const TOTAL_LINE_ID = -1;

function JournalEntryModal({
  entryId,
  onClose,
}: {
  entryId: number;
  onClose: () => void;
}) {
  const { can } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const [deleting, setDeleting] = useState(false);
  const detail = useQuery({
    queryKey: ["gl", "journal", "detail", entryId],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/gl/journal/{entry_id}", {
          params: { path: { entry_id: entryId } },
        }),
      ),
  });

  const e = detail.data;
  // 수기 전표만 직접 삭제 가능(자동 전기 전표는 원천 문서에서 삭제해야 대사 유지)
  const canDelete = can("payment:write") && e?.source_type === "MANUAL";

  const doDelete = async () => {
    if (!e) return;
    const ok = await confirm({
      title: "수기 전표 삭제",
      message: (
        <>
          수기 전표 <b>{e.entry_no}</b> 을(를) 삭제할까요? 되돌릴 수 없습니다.
        </>
      ),
      confirmText: "삭제",
      danger: true,
    });
    if (!ok) return;
    setDeleting(true);
    try {
      unwrap(
        await client.DELETE("/api/gl/journal/{entry_id}", {
          params: { path: { entry_id: entryId } },
        }),
      );
      toast("수기 전표를 삭제했습니다.");
      void queryClient.invalidateQueries({ queryKey: ["gl"] });
      onClose();
    } catch (err) {
      toast(errorMessage(err), true);
    } finally {
      setDeleting(false);
    }
  };
  // 라인 + 차대합 행(복식부기 검산 표시) 합성
  const rows = useMemo(() => {
    if (!e) return undefined;
    const totalRow: JournalLine = {
      id: TOTAL_LINE_ID,
      line_no: 0,
      account_code: "",
      account_name: "합계",
      debit: e.lines.reduce((s, l) => s + l.debit, 0),
      credit: e.lines.reduce((s, l) => s + l.credit, 0),
      partner_name: "",
      memo: "",
    };
    return [...e.lines, totalRow];
  }, [e]);

  const isTotal = (l: JournalLine) => l.id === TOTAL_LINE_ID;

  const columns: Column<JournalLine>[] = [
    {
      key: "account",
      header: "계정",
      render: (l) =>
        isTotal(l) ? (
          <b>합계</b>
        ) : (
          `${l.account_code} ${l.account_name}`
        ),
    },
    {
      key: "debit",
      header: "차변",
      num: true,
      render: (l) => cell(l.debit, isTotal(l)),
    },
    {
      key: "credit",
      header: "대변",
      num: true,
      render: (l) => cell(l.credit, isTotal(l)),
    },
    {
      key: "partner_name",
      header: "거래처",
      render: (l) => (isTotal(l) ? "" : l.partner_name || "-"),
    },
    {
      key: "memo",
      header: "적요",
      render: (l) => (isTotal(l) ? "" : l.memo || "-"),
    },
  ];

  return (
    <Modal
      title={e ? `전표 ${e.entry_no}` : "전표 상세"}
      onClose={onClose}
      wide
      footer={
        <>
          {canDelete && (
            <Button
              variant="danger"
              onClick={() => void doDelete()}
              disabled={deleting}
            >
              {deleting ? "삭제 중…" : "삭제"}
            </Button>
          )}
          <Button variant="ghost" onClick={onClose}>
            닫기
          </Button>
        </>
      }
    >
      {detail.isPending ? (
        <div className={styles.modalNote}>불러오는 중…</div>
      ) : detail.isError ? (
        <div className={styles.modalNote}>{errorMessage(detail.error)}</div>
      ) : (
        e && (
          <>
            <div className={styles.metaLine}>
              {e.entry_date} · {sourceTag(e.source_type)}
              {e.source_id !== null && e.source_id !== undefined && (
                <span className={styles.muted}> #{e.source_id}</span>
              )}
              {e.description && <span> · {e.description}</span>}
            </div>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(l) => l.id}
              rowClassName={(l) => (isTotal(l) ? styles.totalRow : undefined)}
            />
          </>
        )
      )}
    </Modal>
  );
}

// ==================== 계정별원장 ====================

/** 원장 표 1행 뷰모델 — 기초/합계 합성 행 포함(라인은 고유 id 가 없어 인덱스 키) */
type LedgerRowVM = {
  key: string;
  entryId: number | null;
  entryNo: string;
  date: string;
  description: string;
  sourceType: string | null;
  debit: number | null;
  credit: number | null;
  balance: number;
  kind: "opening" | "line" | "total";
};

function ledgerRows(data: LedgerOut): LedgerRowVM[] {
  const opening: LedgerRowVM = {
    key: "__opening__",
    entryId: null,
    entryNo: "",
    date: data.date_from ?? "",
    description: "기초잔액",
    sourceType: null,
    debit: null,
    credit: null,
    balance: data.opening_balance,
    kind: "opening",
  };
  const lines = data.lines.map<LedgerRowVM>((l, i) => ({
    key: `${l.entry_id}-${i}`,
    entryId: l.entry_id,
    entryNo: l.entry_no,
    date: l.entry_date,
    description: l.description,
    sourceType: l.source_type,
    debit: l.debit,
    credit: l.credit,
    balance: l.balance,
    kind: "line",
  }));
  const total: LedgerRowVM = {
    key: "__total__",
    entryId: null,
    entryNo: "",
    date: "",
    description: "합계 / 기말잔액",
    sourceType: null,
    debit: data.total_debit,
    credit: data.total_credit,
    balance: data.closing_balance,
    kind: "total",
  };
  return [opening, ...lines, total];
}

function LedgerView() {
  const [accountCode, setAccountCode] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [detailId, setDetailId] = useState<number | null>(null);

  // 계정 셀렉트 옵션 — 시산표 활성 12계정(쿼리 캐시 공유)
  const accounts = useQuery({
    queryKey: ["gl", "trial-balance"],
    queryFn: async () => unwrap(await client.GET("/api/gl/trial-balance")),
  });

  const ledger = useQuery({
    queryKey: ["gl", "ledger", { account: accountCode, from: dateFrom, to: dateTo }],
    enabled: !!accountCode,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/gl/ledger", {
          params: {
            query: {
              account_code: accountCode,
              ...(dateFrom ? { date_from: dateFrom } : {}),
              ...(dateTo ? { date_to: dateTo } : {}),
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  const data = ledger.data;
  const rows = useMemo(() => (data ? ledgerRows(data) : undefined), [data]);

  const columns: Column<LedgerRowVM>[] = [
    { key: "date", header: "일자" },
    { key: "entryNo", header: "전표번호", code: true },
    {
      key: "description",
      header: "적요",
      render: (r) => (
        <>
          {r.kind === "line" ? r.description || "-" : <b>{r.description}</b>}
          {r.sourceType && (
            <span className={styles.muted}> · {sourceTag(r.sourceType)}</span>
          )}
        </>
      ),
    },
    {
      key: "debit",
      header: "차변",
      num: true,
      render: (r) => (r.debit === null ? "" : cell(r.debit, r.kind === "total")),
    },
    {
      key: "credit",
      header: "대변",
      num: true,
      render: (r) =>
        r.credit === null ? "" : cell(r.credit, r.kind === "total"),
    },
    {
      key: "balance",
      header: "잔액",
      num: true,
      render: (r) => amt(r.balance),
    },
  ];

  const statValue = (pick: (d: LedgerOut) => number) =>
    !accountCode
      ? "-"
      : ledger.isPending
        ? "…"
        : ledger.isError || !data
          ? "-"
          : amt(pick(data));

  return (
    <>
      <div className={styles.cards}>
        <StatCard
          label="기초잔액"
          value={statValue((d) => d.opening_balance)}
          foot={dateFrom ? `${dateFrom} 이전 누계` : "기간 미지정 (기초 0)"}
        />
        <StatCard
          label="기간 증감"
          value={statValue((d) => d.closing_balance - d.opening_balance)}
          foot={
            data && !ledger.isError
              ? `차변 ${amt(data.total_debit)} · 대변 ${amt(data.total_credit)}`
              : "차변 - · 대변 -"
          }
        />
        <StatCard
          label="기말잔액"
          value={statValue((d) => d.closing_balance)}
          foot={
            data && !ledger.isError
              ? `정상잔액 ${data.normal_side === "debit" ? "차변" : "대변"} 기준`
              : "정상잔액 기준"
          }
        />
      </div>

      <Panel>
        <Toolbar>
          <select
            value={accountCode}
            onChange={(e) => setAccountCode(e.target.value)}
            aria-label="계정 선택"
          >
            <option value="">계정 선택…</option>
            {(accounts.data?.rows ?? []).map((r) => (
              <option key={r.account_code} value={r.account_code}>
                {accountLabel(r)}
              </option>
            ))}
          </select>
          <input
            type="date"
            className={styles.dateInput}
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            aria-label="시작일"
            title="시작일"
          />
          <span className={styles.tilde}>~</span>
          <input
            type="date"
            className={styles.dateInput}
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            aria-label="종료일"
            title="종료일"
          />
          <Spacer />
        </Toolbar>
        {!accountCode ? (
          <div className={styles.hint}>
            계정을 선택하면 기초잔액과 러닝밸런스를 표시합니다.
          </div>
        ) : (
          <DataTable
            columns={columns}
            rows={ledger.isError ? [] : rows}
            rowKey={(r) => r.key}
            loading={ledger.isPending}
            emptyText={
              ledger.isError ? errorMessage(ledger.error) : "데이터가 없습니다."
            }
            onRowClick={(r) => {
              if (r.entryId !== null) setDetailId(r.entryId);
            }}
            rowClassName={(r) =>
              r.kind === "total"
                ? styles.totalRow
                : r.kind === "opening"
                  ? styles.openingRow
                  : undefined
            }
          />
        )}
      </Panel>

      {detailId !== null && (
        <JournalEntryModal entryId={detailId} onClose={() => setDetailId(null)} />
      )}
    </>
  );
}

// ==================== 수기 전표 등록 모달 ====================

type ManualLine = { account_code: string; debit: string; credit: string; memo: string };

const emptyLine = (): ManualLine => ({ account_code: "", debit: "", credit: "", memo: "" });

/** 오늘 날짜 YYYY-MM-DD (기본 전표일자) */
function todayStr() {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/**
 * 수기 전표(MANUAL) 등록 — 기초잔액·마감·수정 분개(3110 상대가 전형).
 * 차대 균형이 맞아야 저장 버튼이 열린다(서버도 이중 검증). 저장 후 GL 전체 refetch.
 */
function ManualEntryModal({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [entryDate, setEntryDate] = useState(todayStr());
  const [description, setDescription] = useState("");
  const [lines, setLines] = useState<ManualLine[]>([emptyLine(), emptyLine()]);
  const [saving, setSaving] = useState(false);

  // 계정 옵션 — 시산표 활성 12계정(쿼리 캐시 공유)
  const accounts = useQuery({
    queryKey: ["gl", "trial-balance"],
    queryFn: async () => unwrap(await client.GET("/api/gl/trial-balance")),
  });

  const setLine = (i: number, patch: Partial<ManualLine>) =>
    setLines((prev) => prev.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));
  const addLine = () => setLines((prev) => [...prev, emptyLine()]);
  const removeLine = (i: number) =>
    setLines((prev) => (prev.length <= 2 ? prev : prev.filter((_, idx) => idx !== i)));

  const totalDebit = lines.reduce((s, l) => s + (Number(l.debit) || 0), 0);
  const totalCredit = lines.reduce((s, l) => s + (Number(l.credit) || 0), 0);
  const balanced = Math.round((totalDebit - totalCredit) * 10000) === 0;
  const hasAmount = totalDebit > 0;
  // 코드 지정 + 금액 있는 라인이 2개 이상, 차대 균형이어야 저장 가능
  const validLines = lines.filter(
    (l) => l.account_code && (Number(l.debit) > 0 || Number(l.credit) > 0),
  );
  const canSave = balanced && hasAmount && validLines.length >= 2;

  const doSave = async () => {
    if (!canSave) return;
    setSaving(true);
    try {
      unwrap(
        await client.POST("/api/gl/manual", {
          body: {
            entry_date: entryDate,
            description,
            lines: validLines.map((l) => ({
              account_code: l.account_code,
              debit: Number(l.debit) || 0,
              credit: Number(l.credit) || 0,
              memo: l.memo,
            })),
          },
        }),
      );
      toast("수기 전표를 등록했습니다.");
      void queryClient.invalidateQueries({ queryKey: ["gl"] });
      onClose();
    } catch (err) {
      toast(errorMessage(err), true);
    } finally {
      setSaving(false);
    }
  };

  const accountOptions = accounts.data?.rows ?? [];

  return (
    <Modal
      title="수기 전표 등록"
      onClose={onClose}
      wide
      footer={
        <>
          <span className={balanced ? styles.muted : styles.mismatch}>
            차 {amt(totalDebit)} · 대 {amt(totalCredit)}
            {balanced ? " (일치)" : " (불일치)"}
          </span>
          <Spacer />
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button
            variant="primary"
            onClick={() => void doSave()}
            disabled={!canSave || saving}
          >
            {saving ? "저장 중…" : "저장"}
          </Button>
        </>
      }
    >
      <div className={styles.manualMeta}>
        <label className={styles.field}>
          <span>전표일자</span>
          <input
            type="date"
            className={styles.dateInput}
            value={entryDate}
            onChange={(ev) => setEntryDate(ev.target.value)}
          />
        </label>
        <label className={`${styles.field} ${styles.grow}`}>
          <span>적요</span>
          <input
            type="text"
            value={description}
            onChange={(ev) => setDescription(ev.target.value)}
            placeholder="예: 기초 재고 잔액"
          />
        </label>
      </div>

      <div className={styles.lineHead}>
        <span>계정</span>
        <span className={styles.numHead}>차변</span>
        <span className={styles.numHead}>대변</span>
        <span>적요</span>
        <span />
      </div>
      {lines.map((l, i) => (
        <div key={i} className={styles.lineRow}>
          <select
            value={l.account_code}
            onChange={(ev) => setLine(i, { account_code: ev.target.value })}
            aria-label={`라인 ${i + 1} 계정`}
          >
            <option value="">계정 선택…</option>
            {accountOptions.map((r) => (
              <option key={r.account_code} value={r.account_code}>
                {accountLabel(r)}
              </option>
            ))}
          </select>
          <input
            type="number"
            min="0"
            step="1"
            className={styles.numInput}
            value={l.debit}
            onChange={(ev) => setLine(i, { debit: ev.target.value, credit: "" })}
            aria-label={`라인 ${i + 1} 차변`}
          />
          <input
            type="number"
            min="0"
            step="1"
            className={styles.numInput}
            value={l.credit}
            onChange={(ev) => setLine(i, { credit: ev.target.value, debit: "" })}
            aria-label={`라인 ${i + 1} 대변`}
          />
          <input
            type="text"
            value={l.memo}
            onChange={(ev) => setLine(i, { memo: ev.target.value })}
            placeholder="(선택)"
            aria-label={`라인 ${i + 1} 적요`}
          />
          <button
            type="button"
            className={styles.removeBtn}
            onClick={() => removeLine(i)}
            disabled={lines.length <= 2}
            aria-label={`라인 ${i + 1} 삭제`}
            title="라인 삭제"
          >
            ×
          </button>
        </div>
      ))}
      <div className={styles.addLineWrap}>
        <Button variant="ghost" size="sm" onClick={addLine}>
          + 라인 추가
        </Button>
      </div>
    </Modal>
  );
}

// ==================== 손익계산서 / 재무상태표 공용 ====================

/** 재무제표 섹션 표(FinancialRow[] + 합계 행). 정상잔액 방향 양수 표시. */
function FinancialTable({
  rows,
  totalLabel,
  total,
  loading,
  error,
}: {
  rows: FinancialRow[] | undefined;
  totalLabel: string;
  total: number | undefined;
  loading: boolean;
  error: unknown;
}) {
  const TOTAL = "__ftotal__";
  const data = useMemo(() => {
    if (!rows) return rows;
    const totalRow: FinancialRow = {
      account_code: TOTAL,
      account_name: totalLabel,
      amount: total ?? 0,
    };
    return [...rows, totalRow];
  }, [rows, total, totalLabel]);
  const isTotal = (r: FinancialRow) => r.account_code === TOTAL;

  const columns: Column<FinancialRow>[] = [
    { key: "account_code", header: "코드", code: true, render: (r) => (isTotal(r) ? "" : r.account_code) },
    { key: "account_name", header: "계정명", render: (r) => (isTotal(r) ? <b>{r.account_name}</b> : r.account_name) },
    { key: "amount", header: "금액", num: true, render: (r) => amt(r.amount) },
  ];
  return (
    <DataTable
      columns={columns}
      rows={error ? [] : data}
      rowKey={(r) => r.account_code}
      loading={loading}
      emptyText={error ? errorMessage(error) : "데이터가 없습니다."}
      rowClassName={(r) => (isTotal(r) ? styles.totalRow : undefined)}
    />
  );
}

/** 기간 필터(from~to) 공용 툴바 조각 */
function PeriodFilter({
  dateFrom,
  dateTo,
  onFrom,
  onTo,
  fromLabel = "시작일",
}: {
  dateFrom: string;
  dateTo: string;
  onFrom?: (v: string) => void;
  onTo: (v: string) => void;
  fromLabel?: string;
}) {
  return (
    <>
      {onFrom && (
        <>
          <input
            type="date"
            className={styles.dateInput}
            value={dateFrom}
            onChange={(e) => onFrom(e.target.value)}
            aria-label={fromLabel}
            title={fromLabel}
          />
          <span className={styles.tilde}>~</span>
        </>
      )}
      <input
        type="date"
        className={styles.dateInput}
        value={dateTo}
        onChange={(e) => onTo(e.target.value)}
        aria-label="종료일"
        title="종료일"
      />
    </>
  );
}

// ==================== 손익계산서 ====================

function IncomeStatementView() {
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const is = useQuery({
    queryKey: ["gl", "income-statement", { from: dateFrom, to: dateTo }],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/gl/income-statement", {
          params: {
            query: {
              ...(dateFrom ? { date_from: dateFrom } : {}),
              ...(dateTo ? { date_to: dateTo } : {}),
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  const d = is.data as IncomeStatementOut | undefined;
  const stat = (v: number | undefined) => (is.isPending ? "…" : is.isError || d == null ? "-" : amt(v ?? 0));

  return (
    <>
      <div className={styles.cards}>
        <StatCard label="총수익" value={stat(d?.total_revenue)} foot="수익 계정 합(대변 정상)" />
        <StatCard label="총비용" value={stat(d?.total_expense)} foot="비용 계정 합(차변 정상)" />
        <StatCard
          smallValue
          label="당기순이익"
          value={
            is.isPending ? "…" : is.isError || d == null ? "-" : (
              <span className={(d.net_income ?? 0) < 0 ? styles.mismatch : undefined}>
                {amt(d.net_income)}
              </span>
            )
          }
          foot="수익 − 비용"
        />
      </div>

      <Panel>
        <Toolbar>
          <PeriodFilter
            dateFrom={dateFrom}
            dateTo={dateTo}
            onFrom={setDateFrom}
            onTo={setDateTo}
          />
          <Spacer />
        </Toolbar>
      </Panel>

      <Panel>
        <PanelHead title="수익" />
        <FinancialTable
          rows={d?.revenues}
          totalLabel="수익 합계"
          total={d?.total_revenue}
          loading={is.isPending}
          error={is.isError ? is.error : null}
        />
      </Panel>
      <Panel>
        <PanelHead title="비용" />
        <FinancialTable
          rows={d?.expenses}
          totalLabel="비용 합계"
          total={d?.total_expense}
          loading={is.isPending}
          error={is.isError ? is.error : null}
        />
      </Panel>
    </>
  );
}

// ==================== 재무상태표 ====================

function BalanceSheetView() {
  const [dateTo, setDateTo] = useState("");

  const bs = useQuery({
    queryKey: ["gl", "balance-sheet", { to: dateTo }],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/gl/balance-sheet", {
          params: { query: { ...(dateTo ? { date_to: dateTo } : {}) } },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  const d = bs.data as BalanceSheetOut | undefined;
  const stat = (v: number | undefined) => (bs.isPending ? "…" : bs.isError || d == null ? "-" : amt(v ?? 0));

  // 자본 섹션 = 자본계정 + 당기순이익(미마감 잔여손익) 합성 행
  const equityRows = useMemo(() => {
    if (!d) return undefined;
    const ni: FinancialRow = {
      account_code: "__ni__",
      account_name: "당기순이익 (미마감)",
      amount: d.net_income,
    };
    return [...d.equity, ni];
  }, [d]);

  return (
    <>
      <div className={styles.cards}>
        <StatCard label="총자산" value={stat(d?.total_assets)} foot="자산 계정 합" />
        <StatCard label="총부채" value={stat(d?.total_liabilities)} foot="부채 계정 합" />
        <StatCard
          label="자본 + 당기순이익"
          value={stat(d ? d.total_equity + d.net_income : undefined)}
          foot={d && !bs.isError ? `자본 ${amt(d.total_equity)} · 순이익 ${amt(d.net_income)}` : "자본 · 순이익"}
        />
        <StatCard
          smallValue
          label="대차 평형"
          value={
            bs.isPending ? "…" : bs.isError || d == null ? "-" : d.balanced ? (
              "일치"
            ) : (
              <span className={styles.mismatch}>불일치!</span>
            )
          }
          foot={
            d && !bs.isError
              ? `자산 ${amt(d.total_assets)} = 부채+자본 ${amt(d.total_liabilities_and_equity)}`
              : "자산 = 부채 + 자본 + 순이익"
          }
        />
      </div>

      <Panel>
        <Toolbar>
          <span className={styles.muted}>기준일</span>
          <PeriodFilter dateFrom="" dateTo={dateTo} onTo={setDateTo} />
          <Spacer />
        </Toolbar>
      </Panel>

      <Panel>
        <PanelHead title="자산" />
        <FinancialTable
          rows={d?.assets}
          totalLabel="자산 총계"
          total={d?.total_assets}
          loading={bs.isPending}
          error={bs.isError ? bs.error : null}
        />
      </Panel>
      <Panel>
        <PanelHead title="부채" />
        <FinancialTable
          rows={d?.liabilities}
          totalLabel="부채 총계"
          total={d?.total_liabilities}
          loading={bs.isPending}
          error={bs.isError ? bs.error : null}
        />
      </Panel>
      <Panel>
        <PanelHead title="자본" />
        <FinancialTable
          rows={equityRows}
          totalLabel="자본 총계 (당기순이익 포함)"
          total={d ? d.total_equity + d.net_income : undefined}
          loading={bs.isPending}
          error={bs.isError ? bs.error : null}
        />
      </Panel>
    </>
  );
}
