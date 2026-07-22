"use client";

import type { ReactNode } from "react";

import Button from "@/components/ui/Button";
import { errorMessage } from "@/lib/api/errors";
import { won } from "@/lib/format";

import styles from "./DataTable.module.css";

export type SortOrder = "asc" | "desc";
export type SortState = { key: string; order: SortOrder };

export type Column<T> = {
  /** 열 식별자. render 가 없으면 row[key] 값을 그대로 출력한다. */
  key: string;
  header: ReactNode;
  render?: (row: T) => ReactNode;
  /** 숫자 열 — 우측 정렬 + tabular-nums (레거시 .num) */
  num?: boolean;
  /** 코드 열 — 굵게 + tabular-nums (레거시 td.code) */
  code?: boolean;
  /**
   * 서버사이드 정렬 파라미터 키. onSortChange 와 함께 지정하면 헤더 클릭으로
   * sort/order 를 토글한다(레거시 th.sortable). 백엔드가 정렬을 지원하는
   * 목록에서만 지정할 것.
   */
  sortKey?: string;
};

type DataTableProps<T> = {
  columns: Column<T>[];
  /** 로딩 중이면 undefined 허용 */
  rows: T[] | undefined;
  rowKey: (row: T) => string | number;
  loading?: boolean;
  /** 빈 목록 문구(레거시 emptyRow 기본 문구) — 순수 "비어 있음"에만 쓴다 */
  emptyText?: string;
  /**
   * 조회 실패. 지정하면 빈 상태가 아니라 오류 상태로 렌더한다.
   * (에러를 emptyText 에 넣으면 "데이터 없음"과 구분되지 않는다)
   */
  error?: unknown;
  /** 오류 상태의 "다시 시도" — 보통 query.refetch */
  onRetry?: () => void;
  /**
   * 이미 표시된 데이터를 유지한 채 재조회 중(keepPreviousData).
   * 페이지 이동·필터 변경이 먹혔는지 알 수 있게 표시하고 중복 클릭을 막는다.
   */
  busy?: boolean;
  /** 행 우측 액션 슬롯(레거시 .row-actions) — 지정 시 빈 헤더 열이 추가된다 */
  actions?: (row: T) => ReactNode;
  onRowClick?: (row: T) => void;
  /** 행 강조 클래스(레거시 tr.below-safety) — 페이지 CSS 모듈 클래스를 반환 */
  rowClassName?: (row: T) => string | undefined;
  /** 현재 서버사이드 정렬 상태(page 컴포넌트가 소유) */
  sort?: SortState;
  /** 헤더 클릭 시 다음 정렬 상태 통지 — 같은 키면 asc↔desc 토글 */
  onSortChange?: (next: SortState) => void;
};

/**
 * 레거시 index.html <table> 목록 패리티의 공용 테이블.
 * 정렬은 서버사이드 전제(정렬 상태만 표시·통지, 데이터 정렬은 하지 않음).
 * 페이지네이션은 <Pager> 로 Page[T] {page, pages, total} 를 소비한다.
 */
export default function DataTable<T>({
  columns,
  rows,
  rowKey,
  loading = false,
  emptyText = "데이터가 없습니다.",
  error,
  onRetry,
  busy = false,
  actions,
  onRowClick,
  rowClassName,
  sort,
  onSortChange,
}: DataTableProps<T>) {
  const colCount = columns.length + (actions ? 1 : 0);

  const cellValue = (row: T, col: Column<T>): ReactNode => {
    if (col.render) return col.render(row);
    const v = (row as Record<string, unknown>)[col.key];
    return v === null || v === undefined ? "" : String(v);
  };

  const handleSort = (col: Column<T>) => {
    if (!col.sortKey || !onSortChange) return;
    const next: SortState =
      sort?.key === col.sortKey
        ? { key: col.sortKey, order: sort.order === "asc" ? "desc" : "asc" }
        : { key: col.sortKey, order: "asc" };
    onSortChange(next);
  };

  return (
    <div
      className={styles.tableScroll}
      tabIndex={0}
      role="region"
      aria-label="데이터 표"
    >
      <table
        className={busy ? `${styles.table} ${styles.busy}` : styles.table}
        aria-busy={busy || undefined}
      >
      <thead>
        <tr>
          {columns.map((col) => {
            const sortable = !!col.sortKey && !!onSortChange;
            const activeOrder =
              sortable && sort && sort.key === col.sortKey
                ? sort.order
                : undefined;
            const thCls = [
              col.num ? styles.num : "",
              sortable ? styles.sortable : "",
            ]
              .filter(Boolean)
              .join(" ");
            return (
              <th
                key={col.key}
                className={thCls || undefined}
                aria-sort={
                  activeOrder === undefined
                    ? undefined
                    : activeOrder === "asc"
                      ? "ascending"
                      : "descending"
                }
              >
                {sortable ? (
                  // th 클릭은 키보드로 도달할 수 없다 — 실제 조작은 button 이 받는다
                  <button
                    type="button"
                    className={styles.sortBtn}
                    onClick={() => handleSort(col)}
                  >
                    {col.header}
                    <span className={styles.sortInd} aria-hidden="true">
                      {activeOrder === undefined
                        ? "↕"
                        : activeOrder === "asc"
                          ? "▲"
                          : "▼"}
                    </span>
                  </button>
                ) : (
                  col.header
                )}
              </th>
            );
          })}
          {actions && <th />}
        </tr>
      </thead>
      <tbody>
        {error !== undefined && error !== null ? (
          <tr>
            <td className={styles.errorCell} colSpan={colCount}>
              <div className={styles.errorMsg}>⚠ {errorMessage(error)}</div>
              {onRetry && (
                <Button variant="ghost" size="sm" onClick={onRetry}>
                  다시 시도
                </Button>
              )}
            </td>
          </tr>
        ) : loading && !rows ? (
          <tr>
            <td className={styles.loading} colSpan={colCount}>
              불러오는 중…
            </td>
          </tr>
        ) : !rows || rows.length === 0 ? (
          <tr>
            <td className={styles.empty} colSpan={colCount}>
              {emptyText}
            </td>
          </tr>
        ) : (
          rows.map((row) => {
            const trCls = [
              onRowClick ? styles.clickable : "",
              rowClassName?.(row) ?? "",
            ]
              .filter(Boolean)
              .join(" ");
            return (
            <tr
              key={rowKey(row)}
              className={trCls || undefined}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            >
              {columns.map((col) => {
                const tdCls = [
                  col.num ? styles.num : "",
                  col.code ? styles.code : "",
                ]
                  .filter(Boolean)
                  .join(" ");
                return (
                  <td key={col.key} className={tdCls || undefined}>
                    {cellValue(row, col)}
                  </td>
                );
              })}
              {actions && (
                <td>
                  <div className={styles.rowActions}>{actions(row)}</div>
                </td>
              )}
            </tr>
            );
          })
        )}
      </tbody>
      </table>
    </div>
  );
}

/**
 * 레거시 renderPager 패리티 — Page[T] 의 {page, pages, total} 소비.
 * "전체 N건 · p/pages 페이지" + 이전/다음.
 */
export function Pager({
  page,
  pages,
  total,
  onPageChange,
  busy = false,
}: {
  page: number;
  pages: number;
  total: number;
  onPageChange: (page: number) => void;
  /** 재조회 중 — 이중 클릭으로 페이지를 건너뛰지 않게 막는다 */
  busy?: boolean;
}) {
  return (
    <div className={styles.pager}>
      <span className={styles.pinfo}>
        전체 {won(total)}건 · {page}/{Math.max(pages, 1)} 페이지
      </span>
      <Button
        variant="ghost"
        size="sm"
        disabled={busy || page <= 1}
        onClick={() => onPageChange(Math.max(1, page - 1))}
      >
        이전
      </Button>
      <Button
        variant="ghost"
        size="sm"
        disabled={busy || page >= pages}
        onClick={() => onPageChange(page + 1)}
      >
        다음
      </Button>
    </div>
  );
}
