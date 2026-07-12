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

/** 전표 출처 Tag — 백엔드 source_type(MOVEMENT/PAYMENT/MANUAL) */
const SOURCE_TAG: Record<string, [TagVariant, string]> = {
  MOVEMENT: ["supp", "재고이동"],
  PAYMENT: ["cust", "결제"],
  MANUAL: ["gray", "수동"],
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

type GlTab = "trial" | "journal" | "ledger";

const TABS: [GlTab, string][] = [
  ["trial", "시산표"],
  ["journal", "분개장"],
  ["ledger", "계정별원장"],
];

/**
 * 총계정원장(GL, 슬라이스 5) — 복식부기 전표 조회 3뷰를 탭으로 묶는다:
 * 시산표(+재대사 상태·재전기), 분개장(전표 목록·상세 모달), 계정별원장(러닝밸런스).
 * 조회는 payment:read(nav 게이팅과 동일), 재전기 버튼만 payment:write.
 */
export default function GlPage() {
  const [tab, setTab] = useState<GlTab>("trial");

  return (
    <section>
      <div className={styles.pageHead}>
        <div>
          <h2 className={styles.title}>총계정원장</h2>
          <p className={styles.subtitle}>
            복식부기 전표(GL) — 시산표 · 분개장 · 계정별원장
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
    </section>
  );
}

// ==================== 시산표 + 재대사 + 재전기 ====================

function TrialBalanceView() {
  const { can } = useAuth();
  const toast = useToast();
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
    if (
      !window.confirm(
        "GL 전표 전체를 삭제하고 원천(재고이동·결제)에서 다시 전기합니다. 계속하시겠습니까?",
      )
    )
      return;
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
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [sourceType, setSourceType] = useState("");
  const [accountCode, setAccountCode] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  const [detailId, setDetailId] = useState<number | null>(null);

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
        <Button variant="ghost" onClick={onClose}>
          닫기
        </Button>
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
