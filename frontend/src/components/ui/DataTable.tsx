"use client";

import type { ReactNode } from "react";

import Button from "@/components/ui/Button";
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
  /** 빈 목록 문구(레거시 emptyRow 기본 문구) */
  emptyText?: string;
  /** 행 우측 액션 슬롯(레거시 .row-actions) — 지정 시 빈 헤더 열이 추가된다 */
  actions?: (row: T) => ReactNode;
  onRowClick?: (row: T) => void;
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
  actions,
  onRowClick,
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
    <table className={styles.table}>
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
                onClick={sortable ? () => handleSort(col) : undefined}
                aria-sort={
                  activeOrder === undefined
                    ? undefined
                    : activeOrder === "asc"
                      ? "ascending"
                      : "descending"
                }
              >
                {col.header}
                {sortable && (
                  <span className={styles.sortInd} aria-hidden="true">
                    {activeOrder === undefined
                      ? ""
                      : activeOrder === "asc"
                        ? "▲"
                        : "▼"}
                  </span>
                )}
              </th>
            );
          })}
          {actions && <th />}
        </tr>
      </thead>
      <tbody>
        {loading && !rows ? (
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
          rows.map((row) => (
            <tr
              key={rowKey(row)}
              className={onRowClick ? styles.clickable : undefined}
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
          ))
        )}
      </tbody>
    </table>
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
}: {
  page: number;
  pages: number;
  total: number;
  onPageChange: (page: number) => void;
}) {
  return (
    <div className={styles.pager}>
      <span className={styles.pinfo}>
        전체 {won(total)}건 · {page}/{Math.max(pages, 1)} 페이지
      </span>
      <Button
        variant="ghost"
        size="sm"
        disabled={page <= 1}
        onClick={() => onPageChange(Math.max(1, page - 1))}
      >
        이전
      </Button>
      <Button
        variant="ghost"
        size="sm"
        disabled={page >= pages}
        onClick={() => onPageChange(page + 1)}
      >
        다음
      </Button>
    </div>
  );
}
