"use client";

import { useState } from "react";

import Button from "@/components/ui/Button";
import DataTable, { type Column } from "@/components/ui/DataTable";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { won } from "@/lib/format";

import styles from "./orders.module.css";

/** 표시용 라인 — 발주 입고/반품, (다음 슬라이스) 수주 출고/반품이 같은 형태를 쓴다 */
export type QtyLineRow = {
  lineId: number;
  itemName: string;
  /** 주문 수량 */
  ordered: number;
  /** 누계(입고/출고/반품) */
  done: number;
  /** 이번에 처리 가능한 최대 수량(잔여/반품가능) */
  max: number;
};

/** 서버 DocQtyLineIn 형태 */
export type QtyLinePayload = { line_id: number; qty: number };

/**
 * 라인별 수량 입력 모달 — 레거시 openFulfill/openReturn 공용 골격 이식.
 * 품목·주문·누계·가능 열을 보여주고 "이번" 수량을 입력받는다.
 * 성공 토스트·invalidate·모달 닫기는 onSubmit(호출부) 책임이며, 서버 400
 * (잔여 초과 등)도 호출부 catch 에서 detail 토스트한다.
 */
export default function QtyLinesModal({
  title,
  doneHeader,
  maxHeader,
  rows,
  defaultToMax = false,
  emptyError,
  onSubmit,
  onClose,
}: {
  title: string;
  /** 누계 열 머리(입고누계/반품누계/출고누계) */
  doneHeader: string;
  /** 가능 열 머리(잔여/반품가능) */
  maxHeader: string;
  rows: QtyLineRow[];
  /** true 면 입력 기본값을 잔여 전량으로(입고/출고), false 면 0(반품) */
  defaultToMax?: boolean;
  /** 입력 수량이 하나도 없을 때 토스트 문구(레거시 throw Error 패리티) */
  emptyError: string;
  onSubmit: (lines: QtyLinePayload[]) => Promise<void>;
  onClose: () => void;
}) {
  const toast = useToast();
  const [values, setValues] = useState<Record<number, string>>(() =>
    Object.fromEntries(
      rows.map((r) => [
        r.lineId,
        defaultToMax ? String(Math.max(r.max, 0)) : "0",
      ]),
    ),
  );
  const [submitting, setSubmitting] = useState(false);

  const columns: Column<QtyLineRow>[] = [
    { key: "itemName", header: "품목" },
    { key: "ordered", header: "주문", num: true, render: (r) => won(r.ordered) },
    { key: "done", header: doneHeader, num: true, render: (r) => won(r.done) },
    { key: "max", header: maxHeader, num: true, render: (r) => won(r.max) },
    {
      key: "qty",
      header: "이번",
      render: (r) => (
        <input
          className={styles.lnQty}
          type="number"
          min={0}
          max={r.max}
          disabled={r.max <= 0}
          value={values[r.lineId] ?? ""}
          onChange={(e) =>
            setValues((prev) => ({ ...prev, [r.lineId]: e.target.value }))
          }
          aria-label={`${r.itemName} 이번 수량`}
        />
      ),
    },
  ];

  const submit = async () => {
    const lines: QtyLinePayload[] = rows
      .map((r) => ({ line_id: r.lineId, qty: Number(values[r.lineId]) || 0 }))
      .filter((l) => l.qty > 0);
    if (lines.length === 0) {
      toast(emptyError, true);
      return;
    }
    setSubmitting(true);
    try {
      await onSubmit(lines);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      wide
      title={title}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button onClick={submit} disabled={submitting}>
            {submitting ? "저장 중…" : "저장"}
          </Button>
        </>
      }
    >
      <DataTable columns={columns} rows={rows} rowKey={(r) => r.lineId} />
    </Modal>
  );
}
