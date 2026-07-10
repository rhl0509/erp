"use client";

import { useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import Button from "@/components/ui/Button";
import DataTable, { Pager, type Column } from "@/components/ui/DataTable";
import Modal from "@/components/ui/Modal";
import { Panel, Spacer, Toolbar } from "@/components/ui/Panel";
import Tag, { type TagVariant } from "@/components/ui/Tag";
import { client, unwrap } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";

import styles from "./page.module.css";

type AuditLog = components["schemas"]["AuditLogOut"];

/** 레거시 페이지당 20건 패리티 */
const PAGE_SIZE = 20;

/** 레거시 ACTION_TAG 패리티 — 작업별 태그 색/라벨 */
const ACTION_TAG: Record<string, [TagVariant, string]> = {
  CREATE: ["both", "등록"],
  UPDATE: ["gray", "수정"],
  DELETE: ["supp", "삭제"],
};

/**
 * entity 필터 옵션 — 백엔드 _ENTITIES 계약(app/api/audit.py)과 일치.
 * (레거시 셀렉트의 warehouse 는 백엔드가 거부하는 값이라 제외한다.)
 */
const ENTITY_OPTIONS = [
  ["user", "회원"],
  ["partner", "거래처"],
  ["item", "품목"],
  ["stock", "재고"],
  ["purchase_order", "발주"],
  ["sales_order", "수주"],
  ["payment", "결제"],
  ["tax_invoice", "세금계산서"],
] as const;

/** 표시는 과거 로그의 레거시 대상까지 포함(레거시 ENTITY_LABEL 패리티) */
const ENTITY_LABEL: Record<string, string> = {
  ...Object.fromEntries(ENTITY_OPTIONS),
  warehouse: "창고",
  stock_transfer: "창고이전",
};

/** 레거시 fmtDateTime 패리티 — YYYY-MM-DD HH:mm (로컬) */
function fmtDateTime(s: string): string {
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function actionTag(action: string) {
  const [variant, label] = ACTION_TAG[action] ?? ["gray", action];
  return <Tag variant={variant}>{label}</Tag>;
}

/**
 * 감사 로그(슬라이스 7) — 레거시 #audit 패리티:
 * 작업/대상/기간 필터 + 행위자·대상ID 검색(300ms 디바운스) + Pager,
 * 행 클릭(또는 상세 버튼) 시 변경 전/후 JSON 을 읽기 전용 모달로 표시.
 */
export default function AuditPage() {
  const [action, setAction] = useState("");
  const [entity, setEntity] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState<AuditLog | null>(null);

  // 레거시 debounce(…, 300) 패리티 — 행위자·대상ID 검색
  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(qInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [qInput]);

  const list = useQuery({
    queryKey: [
      "audit",
      "list",
      { page, action, entity, dateFrom, dateTo, q },
    ],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/audit", {
          params: {
            query: {
              page,
              page_size: PAGE_SIZE,
              ...(action ? { action } : {}),
              ...(entity ? { entity } : {}),
              ...(dateFrom ? { date_from: dateFrom } : {}),
              ...(dateTo ? { date_to: dateTo } : {}),
              ...(q ? { q } : {}),
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  const columns: Column<AuditLog>[] = [
    {
      key: "created_at",
      header: "시각",
      render: (a) => fmtDateTime(a.created_at),
    },
    {
      key: "username",
      header: "행위자",
      render: (a) => a.username || <span className={styles.muted}>-</span>,
    },
    { key: "action", header: "작업", render: (a) => actionTag(a.action) },
    {
      key: "entity",
      header: "대상",
      render: (a) => ENTITY_LABEL[a.entity] ?? a.entity,
    },
    {
      key: "entity_id",
      header: "대상ID",
      code: true,
      render: (a) => a.entity_id || "-",
    },
  ];

  return (
    <section>
      <div className={styles.pageHead}>
        <div>
          <h2 className={styles.title}>감사 로그</h2>
          <p className={styles.subtitle}>
            등록·수정·삭제·승인 등 변경 이력을 조회합니다.
          </p>
        </div>
      </div>

      <Panel>
        <Toolbar>
          <select
            value={action}
            onChange={(e) => {
              setAction(e.target.value);
              setPage(1);
            }}
            aria-label="작업 필터"
          >
            <option value="">전체 작업</option>
            <option value="CREATE">등록(CREATE)</option>
            <option value="UPDATE">수정(UPDATE)</option>
            <option value="DELETE">삭제(DELETE)</option>
          </select>
          <select
            value={entity}
            onChange={(e) => {
              setEntity(e.target.value);
              setPage(1);
            }}
            aria-label="대상 필터"
          >
            <option value="">전체 대상</option>
            {ENTITY_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
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
          <input
            type="search"
            placeholder="행위자·대상ID 검색"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            aria-label="행위자·대상ID 검색"
          />
          <Spacer />
        </Toolbar>

        <DataTable
          columns={columns}
          rows={list.isError ? [] : list.data?.items}
          rowKey={(a) => a.id}
          loading={list.isPending}
          emptyText={
            list.isError ? errorMessage(list.error) : "데이터가 없습니다."
          }
          onRowClick={(a) => setDetail(a)}
          actions={(a) => (
            <Button
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                setDetail(a);
              }}
            >
              상세
            </Button>
          )}
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

      {detail && (
        <AuditDetailModal log={detail} onClose={() => setDetail(null)} />
      )}
    </section>
  );
}

// ==================== 감사 로그 상세 모달(읽기 전용) ====================

/** 레거시 _prettyJson 패리티 — JSON 이면 들여쓰기, 아니면 원문 그대로 */
function PrettyJson({ value }: { value: string | null | undefined }) {
  if (value === null || value === undefined) {
    return <span className={styles.muted}>없음</span>;
  }
  let text = value;
  try {
    text = JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    /* JSON 이 아니면 원문 그대로 */
  }
  return <pre className={styles.json}>{text}</pre>;
}

/** 레거시 viewAudit 패리티 — 시각/행위자/작업/대상 + 변경 전/후 JSON */
function AuditDetailModal({
  log,
  onClose,
}: {
  log: AuditLog;
  onClose: () => void;
}) {
  return (
    <Modal
      title={`감사 로그 · #${log.id}`}
      onClose={onClose}
      footer={
        <Button variant="ghost" onClick={onClose}>
          닫기
        </Button>
      }
    >
      <dl className={styles.kv}>
        <dt>시각</dt>
        <dd>{fmtDateTime(log.created_at)}</dd>
        <dt>행위자</dt>
        <dd>
          {log.username || "-"}
          {log.user_id != null && (
            <span className={styles.muted}> (#{log.user_id})</span>
          )}
        </dd>
        <dt>작업</dt>
        <dd>{actionTag(log.action)}</dd>
        <dt>대상</dt>
        <dd>
          {ENTITY_LABEL[log.entity] ?? log.entity}{" "}
          <span className={styles.muted}>#{log.entity_id || "-"}</span>
        </dd>
      </dl>
      <div className={styles.jsonHead}>변경 전</div>
      <PrettyJson value={log.before} />
      <div className={styles.jsonHead}>변경 후</div>
      <PrettyJson value={log.after} />
    </Modal>
  );
}
