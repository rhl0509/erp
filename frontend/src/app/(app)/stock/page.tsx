"use client";

import { useEffect, useState } from "react";
import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useForm } from "react-hook-form";

import Button from "@/components/ui/Button";
import DataTable, { Pager, type Column } from "@/components/ui/DataTable";
import Field, { SelectField } from "@/components/ui/Field";
import Modal, { FormFull, FormGrid } from "@/components/ui/Modal";
import { Panel, PanelHead, Spacer, Toolbar } from "@/components/ui/Panel";
import Tag, { type TagVariant } from "@/components/ui/Tag";
import { useConfirm } from "@/components/ui/ConfirmProvider";
import { useToast } from "@/components/ui/Toast";
import { client, unwrap } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { useAuth } from "@/lib/auth/AuthProvider";
import { applyServerFieldErrors } from "@/lib/forms";
import { dateOnly, won } from "@/lib/format";

import styles from "./page.module.css";

type StockLevel = components["schemas"]["StockLevelOut"];
type StockMovement = components["schemas"]["StockMovementOut"];
type StockTransferResult = components["schemas"]["StockTransferOut"];
type Warehouse = components["schemas"]["WarehouseOut"];

/** 레거시 페이지당 10건 패리티 */
const PAGE_SIZE = 10;

/** 레거시 ITYPE 패리티 */
const ITEM_TYPE_LABEL: Record<string, string> = {
  product: "제품",
  material: "원자재",
  service: "서비스",
};

/** 레거시 MTYPE_TAG 패리티 — 입출고 구분 태그(변형, 라벨) */
const MTYPE_TAG: Record<string, [TagVariant, string]> = {
  IN: ["cust", "입고"],
  OUT: ["supp", "출고"],
  ADJUST: ["gray", "조정"],
  TRANSFER: ["gray", "이전"],
};

const MOVEMENT_TYPE_FILTER_OPTIONS = [
  ["IN", "입고"],
  ["OUT", "출고"],
  ["ADJUST", "조정"],
  ["TRANSFER", "이전"],
] as const;

/** 레거시 창고 셀렉트 옵션 라벨 패리티 — "이름 (코드) · 기본" */
const whLabel = (w: Warehouse) =>
  `${w.name} (${w.code})${w.is_default ? " · 기본" : ""}`;

function movementTag(movementType: string) {
  const entry = MTYPE_TAG[movementType];
  return entry ? (
    <Tag variant={entry[0]}>{entry[1]}</Tag>
  ) : (
    <Tag variant="gray">{movementType}</Tag>
  );
}

/** 재고/입출고 화면 공용 — 활성 창고 목록(필터·등록 모달 셀렉트 채우기) */
function useActiveWarehouses() {
  return useQuery({
    queryKey: ["warehouses", "active"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/warehouses", {
          params: { query: { active_only: true, page: 1, page_size: 100 } },
        }),
      ),
  });
}

/**
 * 재고 관리(슬라이스 5a) — 레거시 #stock 패리티(패널 3단 스택):
 * (1) 재고 알림(안전재고 미달) (2) 현재고 현황(검색·재고없음·창고 필터)
 * (3) 입출고 내역(검색·구분·창고 필터, 등록/창고간 이전 모달, 수기 이동만 삭제).
 */
export default function StockPage() {
  const { can } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const canWrite = can("stock:write");

  // ---- 재고 알림 ----
  const [saPage, setSaPage] = useState(1);

  // ---- 현재고 현황 ----
  const [slQInput, setSlQInput] = useState("");
  const [slQ, setSlQ] = useState("");
  const [slLow, setSlLow] = useState("");
  const [slWh, setSlWh] = useState("");
  const [slPage, setSlPage] = useState(1);

  // ---- 입출고 내역 ----
  const [smQInput, setSmQInput] = useState("");
  const [smQ, setSmQ] = useState("");
  const [smType, setSmType] = useState("");
  const [smWh, setSmWh] = useState("");
  const [smPage, setSmPage] = useState(1);

  const [movementFormOpen, setMovementFormOpen] = useState(false);
  const [transferFormOpen, setTransferFormOpen] = useState(false);
  const [transferResult, setTransferResult] =
    useState<StockTransferResult | null>(null);

  // 레거시 debounce(…, 300) 패리티 — 현재고 검색
  useEffect(() => {
    const timer = setTimeout(() => {
      setSlQ(slQInput.trim());
      setSlPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [slQInput]);

  // 레거시 debounce(…, 300) 패리티 — 입출고 검색
  useEffect(() => {
    const timer = setTimeout(() => {
      setSmQ(smQInput.trim());
      setSmPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [smQInput]);

  const warehouses = useActiveWarehouses();
  const warehouseOptions = warehouses.data?.items ?? [];

  const alerts = useQuery({
    queryKey: ["stock", "alerts", { page: saPage }],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/stock/alerts", {
          params: { query: { page: saPage, page_size: PAGE_SIZE } },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  const levels = useQuery({
    queryKey: [
      "stock",
      "levels",
      { page: slPage, q: slQ, low: slLow, warehouseId: slWh },
    ],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/stock/levels", {
          params: {
            query: {
              page: slPage,
              page_size: PAGE_SIZE,
              ...(slQ ? { q: slQ } : {}),
              ...(slLow ? { low_only: true } : {}),
              ...(slWh ? { warehouse_id: Number(slWh) } : {}),
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  const movements = useQuery({
    queryKey: [
      "stock",
      "movements",
      { page: smPage, q: smQ, type: smType, warehouseId: smWh },
    ],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/stock/movements", {
          params: {
            query: {
              page: smPage,
              page_size: PAGE_SIZE,
              ...(smQ ? { q: smQ } : {}),
              ...(smType ? { movement_type: smType } : {}),
              ...(smWh ? { warehouse_id: Number(smWh) } : {}),
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  // 입출고·이전·삭제는 현재고/내역/알림(+네비 배지 count)을 한꺼번에 갱신한다
  const invalidateStock = () => {
    queryClient.invalidateQueries({ queryKey: ["stock"] });
  };

  // 수기(MANUAL) 이동만 서버가 삭제를 허용한다(문서 생성 이동·이동평균 정합은 400)
  const removeMovement = async (m: StockMovement) => {
    const ok = await confirm({
      title: "입출고 내역 삭제",
      message: (
        <>
          입출고 내역 <b>{m.movement_no}</b> 을(를) 삭제할까요? 재고 잔고와 연결된 회계
          전표도 함께 되돌아갑니다.
        </>
      ),
      confirmText: "삭제",
      danger: true,
    });
    if (!ok) return;
    try {
      unwrap(
        await client.DELETE("/api/stock/movements/{movement_id}", {
          params: { path: { movement_id: m.id } },
        }),
      );
      toast("삭제했습니다.");
      invalidateStock();
    } catch (err) {
      // 문서 생성 이동·이동평균 정합 위반은 서버가 400 + 안내 문구를 준다
      toast(errorMessage(err), true);
    }
  };

  const alertColumns: Column<StockLevel>[] = [
    { key: "item_code", header: "코드", code: true },
    { key: "item_name", header: "품목명" },
    { key: "unit", header: "단위" },
    { key: "on_hand", header: "현재고", num: true, render: (x) => won(x.on_hand) },
    {
      key: "safety_stock",
      header: "안전재고",
      num: true,
      render: (x) => won(x.safety_stock),
    },
    {
      key: "shortage",
      header: "부족",
      num: true,
      render: (x) => won(x.shortage),
    },
  ];

  const levelColumns: Column<StockLevel>[] = [
    { key: "item_code", header: "코드", code: true },
    { key: "item_name", header: "품목명" },
    { key: "unit", header: "단위" },
    {
      key: "item_type",
      header: "유형",
      render: (x) => (
        <Tag variant="gray">{ITEM_TYPE_LABEL[x.item_type] || x.item_type}</Tag>
      ),
    },
    { key: "on_hand", header: "현재고", num: true, render: (x) => won(x.on_hand) },
    {
      key: "safety_stock",
      header: "안전재고",
      num: true,
      render: (x) => (x.safety_stock ? won(x.safety_stock) : "-"),
    },
    {
      key: "shortage",
      header: "부족",
      num: true,
      render: (x) => (x.shortage > 0 ? won(x.shortage) : "-"),
    },
  ];

  const movementColumns: Column<StockMovement>[] = [
    { key: "movement_no", header: "번호", code: true },
    { key: "item_name", header: "품목" },
    {
      key: "movement_type",
      header: "구분",
      render: (m) => movementTag(m.movement_type),
    },
    { key: "quantity", header: "수량", num: true, render: (m) => won(m.quantity) },
    {
      key: "partner_name",
      header: "거래처",
      render: (m) => m.partner_name || "-",
    },
    {
      key: "warehouse_name",
      header: "창고",
      render: (m) => m.warehouse_name || "-",
    },
    {
      key: "unit_price",
      header: "단가",
      num: true,
      render: (m) => won(m.unit_price),
    },
    { key: "created_at", header: "일자", render: (m) => dateOnly(m.created_at) },
  ];

  const warehouseFilter = (
    value: string,
    onChange: (v: string) => void,
    ariaLabel: string,
  ) => (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      aria-label={ariaLabel}
      title="창고 필터"
    >
      <option value="">전체 창고</option>
      {warehouseOptions.map((w) => (
        <option key={w.id} value={String(w.id)}>
          {whLabel(w)}
        </option>
      ))}
    </select>
  );

  return (
    <section>
      <div className={styles.pageHead}>
        <div>
          <h2 className={styles.title}>재고 관리</h2>
          <p className={styles.subtitle}>
            품목별 현재고와 입출고 내역을 관리합니다.
          </p>
        </div>
      </div>

      {/* ==================== 재고 알림 ==================== */}
      <Panel className={styles.stack}>
        <PanelHead
          title={
            <>
              재고 알림{" "}
              <Tag variant="gray">
                {alerts.data ? won(alerts.data.total) : 0}건
              </Tag>
            </>
          }
          actions={
            <div className={styles.panelCaption}>
              안전재고 미달 품목입니다. 품목별 안전재고는 품목 등록/수정에서
              설정합니다.
            </div>
          }
        />
        <DataTable
          columns={alertColumns}
          rows={alerts.isError ? [] : alerts.data?.items}
          rowKey={(x) => x.item_id}
          loading={alerts.isPending}
          emptyText={
            alerts.isError
              ? errorMessage(alerts.error)
              : "안전재고 미달 품목이 없습니다."
          }
          rowClassName={() => styles.belowSafety}
        />
        {alerts.data && (
          <Pager
            page={alerts.data.page}
            pages={alerts.data.pages}
            total={alerts.data.total}
            onPageChange={setSaPage}
          />
        )}
      </Panel>

      {/* ==================== 현재고 현황 ==================== */}
      <Panel className={styles.stack}>
        <PanelHead title="현재고 현황" />
        <Toolbar>
          <input
            type="text"
            placeholder="품목 이름 또는 코드 검색"
            value={slQInput}
            onChange={(e) => setSlQInput(e.target.value)}
            aria-label="현재고 검색"
          />
          <select
            value={slLow}
            onChange={(e) => {
              setSlLow(e.target.value);
              setSlPage(1);
            }}
            aria-label="재고없음 필터"
          >
            <option value="">전체</option>
            <option value="1">재고 없음만</option>
          </select>
          {warehouseFilter(
            slWh,
            (v) => {
              setSlWh(v);
              setSlPage(1);
            },
            "현재고 창고 필터",
          )}
        </Toolbar>
        <DataTable
          columns={levelColumns}
          rows={levels.isError ? [] : levels.data?.items}
          rowKey={(x) => x.item_id}
          loading={levels.isPending}
          emptyText={
            levels.isError ? errorMessage(levels.error) : "데이터가 없습니다."
          }
          rowClassName={(x) =>
            x.shortage > 0 ? styles.belowSafety : undefined
          }
        />
        {levels.data && (
          <Pager
            page={levels.data.page}
            pages={levels.data.pages}
            total={levels.data.total}
            onPageChange={setSlPage}
          />
        )}
      </Panel>

      {/* ==================== 입출고 내역 ==================== */}
      <Panel>
        <PanelHead title="입출고 내역" />
        <Toolbar>
          <input
            type="text"
            placeholder="번호 또는 품목 검색"
            value={smQInput}
            onChange={(e) => setSmQInput(e.target.value)}
            aria-label="입출고 검색"
          />
          <select
            value={smType}
            onChange={(e) => {
              setSmType(e.target.value);
              setSmPage(1);
            }}
            aria-label="구분 필터"
          >
            <option value="">전체 구분</option>
            {MOVEMENT_TYPE_FILTER_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          {warehouseFilter(
            smWh,
            (v) => {
              setSmWh(v);
              setSmPage(1);
            },
            "입출고 창고 필터",
          )}
          <Spacer />
          {canWrite && (
            <>
              <Button variant="ghost" onClick={() => setTransferFormOpen(true)}>
                창고간 이전
              </Button>
              <Button onClick={() => setMovementFormOpen(true)}>
                + 입출고 등록
              </Button>
            </>
          )}
        </Toolbar>
        <DataTable
          columns={movementColumns}
          rows={movements.isError ? [] : movements.data?.items}
          rowKey={(m) => m.id}
          loading={movements.isPending}
          emptyText={
            movements.isError
              ? errorMessage(movements.error)
              : "데이터가 없습니다."
          }
          actions={
            canWrite
              ? (m) =>
                  // 레거시 패리티 — TRANSFER 는 삭제 버튼을 노출하지 않는다
                  // (문서 생성 이동은 서버가 400 으로 최종 차단)
                  m.movement_type !== "TRANSFER" ? (
                    <Button
                      variant="danger"
                      size="sm"
                      onClick={() => removeMovement(m)}
                    >
                      삭제
                    </Button>
                  ) : null
              : undefined
          }
        />
        {movements.data && (
          <Pager
            page={movements.data.page}
            pages={movements.data.pages}
            total={movements.data.total}
            onPageChange={setSmPage}
          />
        )}
      </Panel>

      {movementFormOpen && (
        <MovementFormModal
          onClose={() => setMovementFormOpen(false)}
          onSaved={() => {
            setMovementFormOpen(false);
            invalidateStock();
          }}
        />
      )}

      {transferFormOpen && (
        <TransferFormModal
          onClose={() => setTransferFormOpen(false)}
          onSaved={(result) => {
            setTransferFormOpen(false);
            invalidateStock();
            // 레거시 showTransferResult 패리티 — 등록 모달이 닫힌 뒤 결과 모달
            setTransferResult(result);
          }}
        />
      )}

      {transferResult && (
        <TransferResultModal
          result={transferResult}
          onClose={() => setTransferResult(null)}
        />
      )}
    </section>
  );
}

// ==================== 입출고 등록 모달 ====================

type MovementFormValues = {
  item_id: string;
  movement_type: string;
  quantity: number;
  partner_id: string;
  warehouse_id: string;
  unit_price: number;
  note: string;
};

const MOVEMENT_FIELD_NAMES = [
  "item_id",
  "movement_type",
  "quantity",
  "partner_id",
  "warehouse_id",
  "unit_price",
  "note",
] as const;

/**
 * 레거시 openStockForm 패리티 — 품목/거래처/활성창고를 불러온 뒤 폼을 연다.
 * 창고는 기본창고 프리선택. 재고 부족 등 StockError 는 서버 400 detail 토스트.
 */
function MovementFormModal({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const items = useQuery({
    queryKey: ["items", "options"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/items", {
          params: { query: { page: 1, page_size: 100 } },
        }),
      ),
  });
  const partners = useQuery({
    queryKey: ["partners", "options"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/partners", {
          params: { query: { page: 1, page_size: 100 } },
        }),
      ),
  });
  const warehouses = useActiveWarehouses();

  const pending = items.isPending || partners.isPending || warehouses.isPending;
  const error = items.error ?? partners.error ?? warehouses.error;

  if (pending || error) {
    return (
      <Modal
        title="입출고 등록"
        onClose={onClose}
        footer={
          <Button variant="ghost" onClick={onClose}>
            닫기
          </Button>
        }
      >
        <div className={styles.modalNote}>
          {error ? errorMessage(error) : "불러오는 중…"}
        </div>
      </Modal>
    );
  }

  return (
    <MovementFormInner
      items={items.data!.items}
      partners={partners.data!.items}
      warehouses={warehouses.data!.items}
      onClose={onClose}
      onSaved={onSaved}
    />
  );
}

function MovementFormInner({
  items,
  partners,
  warehouses,
  onClose,
  onSaved,
}: {
  items: components["schemas"]["ItemOut"][];
  partners: components["schemas"]["PartnerOut"][];
  warehouses: Warehouse[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  // 레거시 패리티 — 기본창고(없으면 첫 활성창고) 프리선택
  const defWh = warehouses.find((w) => w.is_default) ?? warehouses[0];
  const { register, handleSubmit, setError, formState } =
    useForm<MovementFormValues>({
      defaultValues: {
        item_id: items[0] ? String(items[0].id) : "",
        movement_type: "IN",
        quantity: 1,
        partner_id: "",
        warehouse_id: defWh ? String(defWh.id) : "",
        unit_price: 0,
        note: "",
      },
    });
  const { errors, isSubmitting } = formState;

  const itemOptions = items.map(
    (i) => [String(i.id), `${i.name} (${i.code})`] as const,
  );
  const partnerOptions = [
    ["", "(선택 안 함)"] as const,
    ...partners.map((p) => [String(p.id), `${p.name} (${p.code})`] as const),
  ];
  const warehouseOptions = warehouses.map(
    (w) => [String(w.id), whLabel(w)] as const,
  );

  const onSubmit = handleSubmit(async (values) => {
    try {
      unwrap(
        await client.POST("/api/stock/movements", {
          body: {
            item_id: Number(values.item_id),
            movement_type: values.movement_type,
            quantity: Number(values.quantity) || 0,
            unit_price: Number(values.unit_price) || 0,
            partner_id: values.partner_id ? Number(values.partner_id) : null,
            // 미지정 시 서버가 기본창고로 기록(레거시 패리티)
            warehouse_id: values.warehouse_id
              ? Number(values.warehouse_id)
              : null,
            note: values.note.trim(),
          },
        }),
      );
      toast("등록했습니다.");
      onSaved();
    } catch (err) {
      // 재고 부족(StockError→400) 등은 detail 토스트, 422 는 필드 에러로
      const applied = applyServerFieldErrors<MovementFormValues>(
        err,
        MOVEMENT_FIELD_NAMES,
        setError,
      );
      if (!applied) toast(errorMessage(err), true);
    }
  });

  return (
    <Modal
      title="입출고 등록"
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button onClick={onSubmit} disabled={isSubmitting}>
            {isSubmitting ? "저장 중…" : "저장"}
          </Button>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate>
        <FormGrid>
          <SelectField
            label="품목 *"
            options={itemOptions}
            error={errors.item_id?.message}
            {...register("item_id", { required: "품목을 선택하세요." })}
          />
          <SelectField
            label="구분"
            options={[
              ["IN", "입고"],
              ["OUT", "출고"],
              ["ADJUST", "조정"],
            ]}
            error={errors.movement_type?.message}
            {...register("movement_type")}
          />
          <Field
            label="수량 *"
            type="number"
            error={errors.quantity?.message}
            {...register("quantity", {
              required: "수량을 입력하세요.",
              valueAsNumber: true,
            })}
          />
          <SelectField
            label="거래처"
            options={partnerOptions}
            error={errors.partner_id?.message}
            {...register("partner_id")}
          />
          <SelectField
            label="창고"
            options={warehouseOptions}
            error={errors.warehouse_id?.message}
            {...register("warehouse_id")}
          />
          <Field
            label="단가"
            type="number"
            min={0}
            error={errors.unit_price?.message}
            {...register("unit_price", { valueAsNumber: true })}
          />
          <FormFull>
            <Field
              label="비고"
              error={errors.note?.message}
              {...register("note")}
            />
          </FormFull>
        </FormGrid>
        {/* Enter 제출용(footer 버튼은 form 밖) */}
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}

// ==================== 창고간 이전 모달 ====================

type TransferFormValues = {
  item_id: string;
  quantity: number;
  from_warehouse_id: string;
  to_warehouse_id: string;
  note: string;
};

const TRANSFER_FIELD_NAMES = [
  "item_id",
  "quantity",
  "from_warehouse_id",
  "to_warehouse_id",
  "note",
] as const;

/**
 * 레거시 openTransferForm 패리티 — 품목 + 전체 창고를 불러와 폼을 연다.
 * 출고는 비활성 창고도 허용(폐쇄 창고의 잔여 재고 반출), 입고는 활성 창고만.
 * 품목 없음/창고 2개 미만이면 안내만 표시한다.
 */
function TransferFormModal({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: (result: StockTransferResult) => void;
}) {
  const items = useQuery({
    queryKey: ["items", "options"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/items", {
          params: { query: { page: 1, page_size: 100 } },
        }),
      ),
  });
  // 출고창고 후보는 비활성 포함 전체 창고(레거시 패리티)
  const warehouses = useQuery({
    queryKey: ["warehouses", "options"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/warehouses", {
          params: { query: { page: 1, page_size: 100 } },
        }),
      ),
  });

  const pending = items.isPending || warehouses.isPending;
  const error = items.error ?? warehouses.error;
  const note = pending
    ? "불러오는 중…"
    : error
      ? errorMessage(error)
      : items.data!.items.length === 0
        ? "먼저 품목을 등록하세요."
        : warehouses.data!.items.length < 2
          ? "창고간 이전에는 창고가 2개 이상 필요합니다. 창고 화면에서 먼저 등록하세요."
          : null;

  if (note !== null) {
    return (
      <Modal
        title="창고간 이전"
        onClose={onClose}
        footer={
          <Button variant="ghost" onClick={onClose}>
            닫기
          </Button>
        }
      >
        <div className={styles.modalNote}>{note}</div>
      </Modal>
    );
  }

  return (
    <TransferFormInner
      items={items.data!.items}
      warehouses={warehouses.data!.items}
      onClose={onClose}
      onSaved={onSaved}
    />
  );
}

function TransferFormInner({
  items,
  warehouses,
  onClose,
  onSaved,
}: {
  items: components["schemas"]["ItemOut"][];
  warehouses: Warehouse[];
  onClose: () => void;
  onSaved: (result: StockTransferResult) => void;
}) {
  const toast = useToast();
  const active = warehouses.filter((w) => w.is_active);
  const defWh = warehouses.find((w) => w.is_default);
  const { register, handleSubmit, setError, formState } =
    useForm<TransferFormValues>({
      defaultValues: {
        item_id: items[0] ? String(items[0].id) : "",
        quantity: 1,
        from_warehouse_id: defWh ? String(defWh.id) : "",
        to_warehouse_id: active[0] ? String(active[0].id) : "",
        note: "",
      },
    });
  const { errors, isSubmitting } = formState;

  const itemOptions = items.map(
    (i) => [String(i.id), `${i.name} (${i.code})`] as const,
  );
  const whOpt = (w: Warehouse) => [String(w.id), whLabel(w)] as const;

  const onSubmit = handleSubmit(async (values) => {
    if (values.from_warehouse_id === values.to_warehouse_id) {
      setError("to_warehouse_id", {
        message: "출고창고와 입고창고가 같습니다. 서로 다른 창고를 선택하세요.",
      });
      return;
    }
    try {
      const result = unwrap(
        await client.POST("/api/stock/transfer", {
          body: {
            item_id: Number(values.item_id),
            from_warehouse_id: Number(values.from_warehouse_id),
            to_warehouse_id: Number(values.to_warehouse_id),
            quantity: Number(values.quantity) || 0,
            note: values.note.trim(),
          },
        }),
      );
      toast("창고간 이전을 등록했습니다.");
      onSaved(result);
    } catch (err) {
      // 출고창고 재고 부족 등 StockError 는 서버 400 detail 토스트
      const applied = applyServerFieldErrors<TransferFormValues>(
        err,
        TRANSFER_FIELD_NAMES,
        setError,
      );
      if (!applied) toast(errorMessage(err), true);
    }
  });

  return (
    <Modal
      title="창고간 이전"
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button onClick={onSubmit} disabled={isSubmitting}>
            {isSubmitting ? "저장 중…" : "저장"}
          </Button>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate>
        <FormGrid>
          <SelectField
            label="품목 *"
            options={itemOptions}
            error={errors.item_id?.message}
            {...register("item_id", { required: "품목을 선택하세요." })}
          />
          <Field
            label="수량 *"
            type="number"
            error={errors.quantity?.message}
            {...register("quantity", {
              required: "수량을 입력하세요.",
              valueAsNumber: true,
            })}
          />
          <SelectField
            label="출고창고 *"
            options={warehouses.map(whOpt)}
            error={errors.from_warehouse_id?.message}
            {...register("from_warehouse_id", {
              required: "출고창고를 선택하세요.",
            })}
          />
          <SelectField
            label="입고창고 *"
            options={active.map(whOpt)}
            error={errors.to_warehouse_id?.message}
            {...register("to_warehouse_id", {
              required: "입고창고를 선택하세요.",
            })}
          />
          <FormFull>
            <Field
              label="비고"
              error={errors.note?.message}
              {...register("note")}
            />
          </FormFull>
        </FormGrid>
        {/* Enter 제출용(footer 버튼은 form 밖) */}
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}

// ==================== 이전 결과 모달 ====================

/** 레거시 showTransferResult 패리티 — 이동번호 페어·이전원가 읽기 전용 표시 */
function TransferResultModal({
  result,
  onClose,
}: {
  result: StockTransferResult;
  onClose: () => void;
}) {
  return (
    <Modal
      title="창고간 이전 완료"
      onClose={onClose}
      footer={
        <Button variant="ghost" onClick={onClose}>
          닫기
        </Button>
      }
    >
      <dl className={styles.kv}>
        <dt>출고 이동번호</dt>
        <dd className={styles.code}>{result.out_movement_no}</dd>
        <dt>입고 이동번호</dt>
        <dd className={styles.code}>{result.in_movement_no}</dd>
        <dt>수량</dt>
        <dd>{won(result.quantity)}</dd>
        <dt>이전원가</dt>
        <dd>
          ₩{won(Math.round(result.cost))}{" "}
          <span className={styles.muted}>/ 단위 (출고창고 이동평균)</span>
        </dd>
      </dl>
    </Modal>
  );
}
