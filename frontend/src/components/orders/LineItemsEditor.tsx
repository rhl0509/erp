"use client";

import {
  useFieldArray,
  useWatch,
  type Control,
  type UseFormRegister,
} from "react-hook-form";

import Button from "@/components/ui/Button";
import DataTable, { type Column } from "@/components/ui/DataTable";
import { won } from "@/lib/format";

import styles from "./orders.module.css";

/** 명세 한 행 — select 값은 문자열로 관리하고 전송 시 숫자 변환 */
export type OrderLineValues = {
  item_id: string;
  qty: number;
  unit_price: number;
};

/**
 * 주문문서(발주/수주) 등록 폼 공용 값 — 레거시 openOrderForm 필드 패리티.
 * 발주·수주가 같은 형태(거래처 + 일자 + 비고 + 명세)를 공유한다.
 */
export type OrderFormValues = {
  partner_id: string;
  order_date: string;
  note: string;
  lines: OrderLineValues[];
};

/**
 * 레거시 _collectLines 패리티 — 숫자 변환 후 item_id 없음/수량 0 이하 행 제외.
 * 서버 OrderLineIn[] 형태를 돌려준다.
 */
export function toOrderLinesPayload(lines: OrderLineValues[]) {
  return lines
    .map((l) => ({
      item_id: Number(l.item_id),
      qty: Number(l.qty) || 0,
      unit_price: Number(l.unit_price) || 0,
    }))
    .filter((l) => l.item_id && l.qty > 0);
}

type EditorRow = { fieldId: string; index: number };

/**
 * 동적 명세(라인아이템) 편집기 — 레거시 _lineRow/#lnAdd 이식.
 * 품목 select + 수량 + 단가 행을 추가/삭제하고 공급가액 합계를 보여준다.
 * RHF useFieldArray 기반: 부모 폼(OrderFormValues)의 `lines` 필드를 편집한다.
 */
export default function LineItemsEditor({
  control,
  register,
  itemOptions,
}: {
  control: Control<OrderFormValues>;
  register: UseFormRegister<OrderFormValues>;
  /** [value, label] — 레거시 `${name} (${code})` 옵션 관례 */
  itemOptions: readonly (readonly [string, string])[];
}) {
  const { fields, append, remove } = useFieldArray({ control, name: "lines" });
  const lines = useWatch({ control, name: "lines" });

  // 합계(공급가액) — 부가세는 품목 과세구분에 따라 서버가 계산하므로 여기선 공급가액만
  const total = (lines ?? []).reduce(
    (sum, l) => sum + (Number(l?.qty) || 0) * (Number(l?.unit_price) || 0),
    0,
  );

  const rows: EditorRow[] = fields.map((f, index) => ({
    fieldId: f.id,
    index,
  }));

  const columns: Column<EditorRow>[] = [
    {
      key: "item",
      header: "품목",
      render: (r) => (
        <select
          className={styles.lnItem}
          aria-label={`명세 ${r.index + 1} 품목`}
          {...register(`lines.${r.index}.item_id`)}
        >
          {itemOptions.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      ),
    },
    {
      key: "qty",
      header: "수량",
      render: (r) => (
        <input
          className={styles.lnQty}
          type="number"
          min={1}
          aria-label={`명세 ${r.index + 1} 수량`}
          {...register(`lines.${r.index}.qty`, { valueAsNumber: true })}
        />
      ),
    },
    {
      key: "unit_price",
      header: "단가",
      render: (r) => (
        <input
          className={styles.lnPrice}
          type="number"
          min={0}
          aria-label={`명세 ${r.index + 1} 단가`}
          {...register(`lines.${r.index}.unit_price`, { valueAsNumber: true })}
        />
      ),
    },
  ];

  return (
    <div>
      <div className={styles.lnLabel}>명세</div>
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.fieldId}
        emptyText="행을 추가하세요."
        actions={(r) => (
          <Button variant="ghost" size="sm" onClick={() => remove(r.index)}>
            ×
          </Button>
        )}
      />
      <div className={styles.lnFoot}>
        <Button
          variant="ghost"
          size="sm"
          onClick={() =>
            append({
              item_id: itemOptions[0]?.[0] ?? "",
              qty: 1,
              unit_price: 0,
            })
          }
        >
          + 행 추가
        </Button>
        <div className={styles.lnTotal}>
          합계(공급가액) <b>₩{won(total)}</b>
        </div>
      </div>
    </div>
  );
}
