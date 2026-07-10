"use client";

import { useEffect, useState } from "react";
import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useForm } from "react-hook-form";

import LineItemsEditor, {
  toOrderLinesPayload,
  type OrderFormValues,
} from "@/components/orders/LineItemsEditor";
import OrderStatusTag, {
  type OrderStatusMap,
} from "@/components/orders/OrderStatusTag";
import QtyLinesModal, {
  type QtyLinePayload,
  type QtyLineRow,
} from "@/components/orders/QtyLinesModal";
import Button from "@/components/ui/Button";
import DataTable, { Pager, type Column } from "@/components/ui/DataTable";
import Field, { SelectField } from "@/components/ui/Field";
import Modal, { FormFull, FormGrid } from "@/components/ui/Modal";
import { Panel, Spacer, Toolbar } from "@/components/ui/Panel";
import { useToast } from "@/components/ui/Toast";
import { client, unwrap } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { useAuth } from "@/lib/auth/AuthProvider";
import { applyServerFieldErrors } from "@/lib/forms";
import { won } from "@/lib/format";

import styles from "./page.module.css";

type Po = components["schemas"]["PurchaseOrderOut"];
type TaxInvoice = components["schemas"]["TaxInvoiceOut"];

/** 레거시 페이지당 10건 패리티 */
const PAGE_SIZE = 10;

/** 레거시 PO_STATUS 패리티 — 상태 라벨(레거시는 전부 기본 태그) */
const PO_STATUS_TAG: OrderStatusMap = {
  draft: ["default", "작성중"],
  confirmed: ["default", "확정"],
  partial: ["default", "부분입고"],
  received: ["default", "입고완료"],
  cancelled: ["gray", "취소"],
};

/** 레거시 #poStatus 셀렉트 패리티 */
const PO_STATUS_FILTER_OPTIONS = [
  ["draft", "작성중"],
  ["confirmed", "확정"],
  ["partial", "부분입고"],
  ["received", "입고완료"],
  ["cancelled", "취소"],
] as const;

/**
 * 상태전이 노출 규칙 — 레거시 orderActions/viewOrder 패리티.
 * 서버가 최종 판정하므로(400 detail) 여기서는 노출만 제어한다.
 */
const canConfirm = (po: Po) => po.status === "draft";
const canReceive = (po: Po) =>
  po.status === "confirmed" || po.status === "partial";
const canCancel = (po: Po) =>
  po.status !== "received" && po.status !== "cancelled";
const canDelete = (po: Po) => po.status === "draft";
const canReturn = (po: Po) =>
  (po.status === "partial" || po.status === "received") &&
  po.lines.some((l) => l.returnable_qty > 0);
/** 세금계산서 발행 가능 문서(레거시 issueInvoiceFromDoc 노출 조건) */
const canIssueInvoice = (po: Po) =>
  po.status !== "draft" && po.status !== "cancelled";

/**
 * 발주(구매) — 슬라이스 5b. 레거시 #purchase 패리티:
 * 발주번호 검색(300ms 디바운스)·상태 필터·페이지네이션 목록, 발주 등록 모달
 * (공급처 + 동적 명세), 상세 모달(헤더·라인·상태전이 액션·세금계산서),
 * 확정/입고(라인별 수량)/매입반품/취소/삭제. 입고·반품은 재고에도 반영되므로
 * ["stock"] 도 함께 invalidate 한다(재고 알림 배지 연동).
 */
export default function PurchasePage() {
  const { can } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();
  const canWrite = can("purchase:write");

  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);

  const [formOpen, setFormOpen] = useState(false);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [receiveTarget, setReceiveTarget] = useState<Po | null>(null);
  const [returnTarget, setReturnTarget] = useState<Po | null>(null);

  // 레거시 debounce(…, 300) 패리티 — 발주번호 검색
  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(qInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [qInput]);

  const list = useQuery({
    queryKey: ["purchase", "list", { page, q, status }],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/purchase-orders", {
          params: {
            query: {
              page,
              page_size: PAGE_SIZE,
              ...(q ? { q } : {}),
              ...(status ? { status } : {}),
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  const invalidatePurchase = () => {
    queryClient.invalidateQueries({ queryKey: ["purchase"] });
  };
  // 입고/반품은 재고 이동을 만든다 — 현재고·내역·알림(+네비 배지)도 갱신
  const invalidateStock = () => {
    queryClient.invalidateQueries({ queryKey: ["stock"] });
  };

  // ---- 상태전이 액션(목록·상세 공용) ----

  const confirmPo = async (po: Po) => {
    try {
      unwrap(
        await client.POST("/api/purchase-orders/{po_id}/confirm", {
          params: { path: { po_id: po.id } },
        }),
      );
      toast("확정했습니다.");
      invalidatePurchase();
    } catch (err) {
      toast(errorMessage(err), true);
    }
  };

  const cancelPo = async (po: Po) => {
    // 레거시 cancelOrder confirm 패리티
    if (!window.confirm("이 문서를 취소하시겠습니까?")) return;
    try {
      unwrap(
        await client.POST("/api/purchase-orders/{po_id}/cancel", {
          params: { path: { po_id: po.id } },
        }),
      );
      toast("취소했습니다.");
      invalidatePurchase();
    } catch (err) {
      // 입고/지급 이력 있는 발주 취소 거부 등은 서버 400 detail
      toast(errorMessage(err), true);
    }
  };

  const deletePo = async (po: Po) => {
    if (!window.confirm("이 문서를 삭제하시겠습니까?")) return;
    try {
      unwrap(
        await client.DELETE("/api/purchase-orders/{po_id}", {
          params: { path: { po_id: po.id } },
        }),
      );
      toast("삭제했습니다.");
      setDetailId(null);
      invalidatePurchase();
    } catch (err) {
      toast(errorMessage(err), true);
    }
  };

  const submitReceive = async (po: Po, lines: QtyLinePayload[]) => {
    try {
      unwrap(
        await client.POST("/api/purchase-orders/{po_id}/receive", {
          params: { path: { po_id: po.id } },
          body: { lines },
        }),
      );
      toast("입고 처리했습니다.");
      setReceiveTarget(null);
      invalidatePurchase();
      invalidateStock();
    } catch (err) {
      // 잔여 초과 등은 서버 400 detail 토스트(모달은 열어 둔다)
      toast(errorMessage(err), true);
    }
  };

  const submitReturn = async (po: Po, lines: QtyLinePayload[]) => {
    try {
      unwrap(
        await client.POST("/api/purchase-orders/{po_id}/return", {
          params: { path: { po_id: po.id } },
          body: { lines },
        }),
      );
      toast("반품 처리했습니다.");
      setReturnTarget(null);
      invalidatePurchase();
      invalidateStock();
    } catch (err) {
      // 반품가능 초과·재고 부족(StockError) 등은 서버 400 detail 토스트
      toast(errorMessage(err), true);
    }
  };

  const columns: Column<Po>[] = [
    { key: "po_no", header: "발주번호", code: true },
    { key: "partner_name", header: "공급처" },
    {
      key: "status",
      header: "상태",
      render: (po) => <OrderStatusTag map={PO_STATUS_TAG} status={po.status} />,
    },
    {
      key: "total_amount",
      header: "합계",
      num: true,
      render: (po) => won(po.total_amount),
    },
    { key: "order_date", header: "일자" },
  ];

  return (
    <section>
      <div className={styles.pageHead}>
        <div>
          <h2 className={styles.title}>발주(구매)</h2>
          <p className={styles.subtitle}>
            공급처 발주서 작성·확정·입고 관리
          </p>
        </div>
      </div>

      <Panel>
        <Toolbar>
          <input
            type="text"
            placeholder="발주번호 검색"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            aria-label="발주번호 검색"
          />
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
            aria-label="상태 필터"
          >
            <option value="">전체 상태</option>
            {PO_STATUS_FILTER_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <Spacer />
          {canWrite && (
            <Button onClick={() => setFormOpen(true)}>+ 발주 등록</Button>
          )}
        </Toolbar>

        <DataTable
          columns={columns}
          rows={list.isError ? [] : list.data?.items}
          rowKey={(po) => po.id}
          loading={list.isPending}
          emptyText={
            list.isError ? errorMessage(list.error) : "데이터가 없습니다."
          }
          onRowClick={(po) => setDetailId(po.id)}
          actions={(po) => (
            // 행 클릭(상세 열기)과 겹치지 않게 버튼에서 전파를 끊는다
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={(e) => {
                  e.stopPropagation();
                  setDetailId(po.id);
                }}
              >
                상세
              </Button>
              {canWrite && (
                <>
                  {canConfirm(po) && (
                    <Button
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        void confirmPo(po);
                      }}
                    >
                      확정
                    </Button>
                  )}
                  {canReceive(po) && (
                    <Button
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        setReceiveTarget(po);
                      }}
                    >
                      입고
                    </Button>
                  )}
                  {canCancel(po) && (
                    <Button
                      variant="danger"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        void cancelPo(po);
                      }}
                    >
                      취소
                    </Button>
                  )}
                  {canDelete(po) && (
                    <Button
                      variant="danger"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        void deletePo(po);
                      }}
                    >
                      삭제
                    </Button>
                  )}
                </>
              )}
            </>
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

      {formOpen && (
        <PoFormModal
          onClose={() => setFormOpen(false)}
          onSaved={() => {
            setFormOpen(false);
            invalidatePurchase();
          }}
        />
      )}

      {detailId !== null && (
        <PoDetailModal
          poId={detailId}
          onClose={() => setDetailId(null)}
          onConfirm={confirmPo}
          onReceive={setReceiveTarget}
          onReturn={setReturnTarget}
          onCancel={cancelPo}
          onDelete={deletePo}
        />
      )}

      {receiveTarget && (
        <QtyLinesModal
          title={`입고 처리 · ${receiveTarget.po_no}`}
          doneHeader="입고누계"
          maxHeader="잔여"
          defaultToMax
          emptyError="처리할 수량이 없습니다."
          rows={receiveTarget.lines.map(
            (l): QtyLineRow => ({
              lineId: l.id,
              itemName: l.item_name,
              ordered: l.qty,
              done: l.received_qty,
              max: l.remaining_qty,
            }),
          )}
          onSubmit={(lines) => submitReceive(receiveTarget, lines)}
          onClose={() => setReceiveTarget(null)}
        />
      )}

      {returnTarget && (
        <QtyLinesModal
          title={`매입반품 · ${returnTarget.po_no}`}
          doneHeader="반품누계"
          maxHeader="반품가능"
          emptyError="반품할 수량이 없습니다."
          rows={returnTarget.lines.map(
            (l): QtyLineRow => ({
              lineId: l.id,
              itemName: l.item_name,
              ordered: l.qty,
              done: l.returned_qty,
              max: l.returnable_qty,
            }),
          )}
          onSubmit={(lines) => submitReturn(returnTarget, lines)}
          onClose={() => setReturnTarget(null)}
        />
      )}
    </section>
  );
}

// ==================== 발주 상세 모달 ====================

type PoLineRow = components["schemas"]["PurchaseOrderLineOut"];

/**
 * 레거시 viewOrder 패리티 + 상태전이 액션 바.
 * 항상 서버에서 최신 상세를 조회한다(["purchase","detail",id] — 액션 성공 시
 * invalidate 로 함께 갱신). 세금계산서 여부는 invoice:read 가 있을 때만 조회.
 */
function PoDetailModal({
  poId,
  onClose,
  onConfirm,
  onReceive,
  onReturn,
  onCancel,
  onDelete,
}: {
  poId: number;
  onClose: () => void;
  onConfirm: (po: Po) => Promise<void>;
  onReceive: (po: Po) => void;
  onReturn: (po: Po) => void;
  onCancel: (po: Po) => Promise<void>;
  onDelete: (po: Po) => Promise<void>;
}) {
  const { can } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();
  const canWrite = can("purchase:write");
  const canInvoiceRead = can("invoice:read");
  const canInvoiceWrite = can("invoice:write");

  const detail = useQuery({
    queryKey: ["purchase", "detail", poId],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/purchase-orders/{po_id}", {
          params: { path: { po_id: poId } },
        }),
      ),
  });

  // 이 발주의 유효(발행) 세금계산서 — 문서당 1건만 허용되므로 첫 건이 전부다
  const invoice = useQuery({
    queryKey: ["purchase", "detail", poId, "invoice"],
    enabled: canInvoiceRead,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/tax-invoices", {
          params: {
            query: {
              ref_type: "PO",
              ref_id: poId,
              status: "issued",
              page: 1,
              page_size: 1,
            },
          },
        }),
      ),
  });
  const issuedInvoice: TaxInvoice | null = invoice.data?.items[0] ?? null;

  const invalidateDetail = () => {
    queryClient.invalidateQueries({ queryKey: ["purchase", "detail", poId] });
  };

  // 레거시 issueInvoiceFromDoc 패리티(발행 후 모달을 닫는 대신 상세를 갱신)
  const issueInvoice = async (po: Po) => {
    if (!window.confirm("이 발주로 세금계산서를 발행할까요?")) return;
    try {
      const inv = unwrap(
        await client.POST("/api/tax-invoices", {
          body: { ref_type: "PO", ref_id: po.id, issue_date: "", note: "" },
        }),
      );
      toast(`세금계산서 발행: ${inv.invoice_no} (합계 ₩${won(inv.total_amount)})`);
      invalidateDetail();
    } catch (err) {
      // 중복 발행은 서버 409, draft/취소 문서는 400 detail
      toast(errorMessage(err), true);
    }
  };

  const cancelInvoice = async (inv: TaxInvoice) => {
    if (!window.confirm(`세금계산서 ${inv.invoice_no} 을(를) 취소할까요?`))
      return;
    try {
      unwrap(
        await client.POST("/api/tax-invoices/{inv_id}/cancel", {
          params: { path: { inv_id: inv.id } },
        }),
      );
      toast("세금계산서를 취소했습니다.");
      invalidateDetail();
    } catch (err) {
      toast(errorMessage(err), true);
    }
  };

  if (detail.isPending || detail.isError) {
    return (
      <Modal
        title="발주 상세"
        onClose={onClose}
        footer={
          <Button variant="ghost" onClick={onClose}>
            닫기
          </Button>
        }
      >
        <div className={styles.modalNote}>
          {detail.isError ? errorMessage(detail.error) : "불러오는 중…"}
        </div>
      </Modal>
    );
  }

  const po = detail.data;

  const lineColumns: Column<PoLineRow>[] = [
    { key: "item_name", header: "품목" },
    { key: "qty", header: "주문", num: true, render: (l) => won(l.qty) },
    {
      key: "received_qty",
      header: "입고",
      num: true,
      render: (l) => won(l.received_qty),
    },
    {
      key: "remaining_qty",
      header: "잔여",
      num: true,
      render: (l) => won(l.remaining_qty),
    },
    {
      key: "unit_price",
      header: "단가",
      num: true,
      render: (l) => won(l.unit_price),
    },
    {
      key: "amount",
      header: "공급가액",
      num: true,
      render: (l) => won(l.amount),
    },
    {
      key: "tax_amount",
      header: "세액",
      num: true,
      render: (l) => won(l.tax_amount),
    },
  ];

  return (
    <Modal
      wide
      title={po.po_no}
      onClose={onClose}
      footer={
        <Button variant="ghost" onClick={onClose}>
          닫기
        </Button>
      }
    >
      <dl className={styles.kv}>
        <dt>거래처</dt>
        <dd>{po.partner_name}</dd>
        <dt>상태</dt>
        <dd>
          <OrderStatusTag map={PO_STATUS_TAG} status={po.status} />
        </dd>
        <dt>일자</dt>
        <dd>{po.order_date}</dd>
        <dt>공급가액</dt>
        <dd>{won(po.total_amount)}</dd>
        <dt>부가세</dt>
        <dd>{won(po.tax_amount)}</dd>
        <dt>합계</dt>
        <dd>
          <strong>{won(po.grand_total)}</strong>
        </dd>
        <dt>지급</dt>
        <dd>{won(po.paid_amount)}</dd>
        <dt>미지급</dt>
        <dd>
          <strong>{won(po.outstanding)}</strong>
        </dd>
        {canInvoiceRead && (
          <>
            <dt>세금계산서</dt>
            <dd>
              {invoice.isPending
                ? "…"
                : issuedInvoice
                  ? `발행 · ${issuedInvoice.invoice_no}`
                  : "미발행"}
            </dd>
          </>
        )}
      </dl>

      <DataTable
        columns={lineColumns}
        rows={po.lines}
        rowKey={(l) => l.id}
        emptyText="명세가 없습니다."
      />

      <div className={styles.actionBar}>
        {canWrite && canConfirm(po) && (
          <Button onClick={() => void onConfirm(po)}>확정</Button>
        )}
        {canWrite && canReceive(po) && (
          <Button onClick={() => onReceive(po)}>입고</Button>
        )}
        {canWrite && canReturn(po) && (
          <Button onClick={() => onReturn(po)}>매입반품</Button>
        )}
        {canWrite && canCancel(po) && (
          <Button variant="danger" onClick={() => void onCancel(po)}>
            취소
          </Button>
        )}
        {canWrite && canDelete(po) && (
          <Button variant="danger" onClick={() => void onDelete(po)}>
            삭제
          </Button>
        )}
        {canInvoiceWrite && canIssueInvoice(po) && !issuedInvoice && (
          <Button variant="ghost" onClick={() => void issueInvoice(po)}>
            세금계산서 발행
          </Button>
        )}
        {canInvoiceWrite && issuedInvoice && (
          <Button variant="ghost" onClick={() => void cancelInvoice(issuedInvoice)}>
            세금계산서 취소
          </Button>
        )}
      </div>
    </Modal>
  );
}

// ==================== 발주 등록 모달 ====================

const PO_FORM_FIELD_NAMES = ["partner_id", "order_date", "note"] as const;

/**
 * 레거시 openOrderForm("purchase") 패리티 — 품목·거래처(고객 전용 제외)를
 * 불러온 뒤 폼을 연다. 품목/공급처가 없으면 안내만 표시한다.
 */
function PoFormModal({
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

  const pending = items.isPending || partners.isPending;
  const error = items.error ?? partners.error;
  // 레거시 relevant 필터 패리티 — 발주는 고객 전용(customer)을 제외
  const suppliers =
    partners.data?.items.filter((p) => p.partner_type !== "customer") ?? [];

  const note = pending
    ? "불러오는 중…"
    : error
      ? errorMessage(error)
      : items.data!.items.length === 0
        ? "먼저 품목을 등록하세요."
        : suppliers.length === 0
          ? "먼저 공급처를 등록하세요."
          : null;

  if (note !== null) {
    return (
      <Modal
        title="발주 등록"
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
    <PoFormInner
      items={items.data!.items}
      suppliers={suppliers}
      onClose={onClose}
      onSaved={onSaved}
    />
  );
}

function PoFormInner({
  items,
  suppliers,
  onClose,
  onSaved,
}: {
  items: components["schemas"]["ItemOut"][];
  suppliers: components["schemas"]["PartnerOut"][];
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const { register, control, handleSubmit, setError, formState } =
    useForm<OrderFormValues>({
      defaultValues: {
        partner_id: String(suppliers[0].id),
        order_date: "",
        note: "",
        lines: [
          { item_id: String(items[0].id), qty: 1, unit_price: 0 },
        ],
      },
    });
  const { errors, isSubmitting } = formState;

  const itemOptions = items.map(
    (i) => [String(i.id), `${i.name} (${i.code})`] as const,
  );
  const partnerOptions = suppliers.map(
    (p) => [String(p.id), `${p.name} (${p.code})`] as const,
  );

  const onSubmit = handleSubmit(async (values) => {
    const lines = toOrderLinesPayload(values.lines);
    // 레거시 패리티 — 유효한 명세 행이 없으면 전송하지 않는다
    if (lines.length === 0) {
      toast("명세를 1행 이상 입력하세요.", true);
      return;
    }
    try {
      unwrap(
        await client.POST("/api/purchase-orders", {
          body: {
            partner_id: Number(values.partner_id),
            order_date: values.order_date,
            note: values.note.trim(),
            lines,
          },
        }),
      );
      toast("등록했습니다.");
      onSaved();
    } catch (err) {
      const applied = applyServerFieldErrors<OrderFormValues>(
        err,
        PO_FORM_FIELD_NAMES,
        setError,
      );
      if (!applied) toast(errorMessage(err), true);
    }
  });

  return (
    <Modal
      wide
      title="발주 등록"
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
          <FormFull>
            <SelectField
              label="공급처 *"
              options={partnerOptions}
              error={errors.partner_id?.message}
              {...register("partner_id", { required: "공급처를 선택하세요." })}
            />
          </FormFull>
          <Field
            label="일자"
            type="date"
            error={errors.order_date?.message}
            {...register("order_date")}
          />
          <FormFull>
            <Field
              label="비고"
              error={errors.note?.message}
              {...register("note")}
            />
          </FormFull>
          <FormFull>
            <LineItemsEditor
              control={control}
              register={register}
              itemOptions={itemOptions}
            />
          </FormFull>
        </FormGrid>
        {/* Enter 제출용(footer 버튼은 form 밖) */}
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}
