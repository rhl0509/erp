"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
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
import { useConfirm } from "@/components/ui/ConfirmProvider";
import { useToast } from "@/components/ui/Toast";
import { client, unwrap } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { useAuth } from "@/lib/auth/AuthProvider";
import { applyServerFieldErrors } from "@/lib/forms";
import { won } from "@/lib/format";

import styles from "./page.module.css";

type So = components["schemas"]["SalesOrderOut"];
type TaxInvoice = components["schemas"]["TaxInvoiceOut"];

/** 레거시 페이지당 10건 패리티 */
const PAGE_SIZE = 10;

/** 레거시 SO_STATUS 패리티 — 상태 라벨(레거시는 전부 기본 태그) */
const SO_STATUS_TAG: OrderStatusMap = {
  draft: ["default", "작성중"],
  confirmed: ["default", "확정"],
  partial: ["default", "부분출고"],
  shipped: ["default", "출고완료"],
  cancelled: ["gray", "취소"],
};

/** 레거시 #soStatus 셀렉트 패리티 */
const SO_STATUS_FILTER_OPTIONS = [
  ["draft", "작성중"],
  ["confirmed", "확정"],
  ["partial", "부분출고"],
  ["shipped", "출고완료"],
  ["cancelled", "취소"],
] as const;

/**
 * 상태전이 노출 규칙 — 레거시 orderActions/viewOrder 패리티(sales.py 전이와 일치).
 * 서버가 최종 판정하므로(400 detail — 여신 초과·재고 부족 포함) 여기서는 노출만 제어한다.
 */
const canConfirm = (so: So) => so.status === "draft";
const canShip = (so: So) =>
  so.status === "confirmed" || so.status === "partial";
const canCancel = (so: So) =>
  so.status !== "shipped" && so.status !== "cancelled";
const canDelete = (so: So) => so.status === "draft";
const canReturn = (so: So) =>
  (so.status === "partial" || so.status === "shipped") &&
  so.lines.some((l) => l.returnable_qty > 0);
/** 세금계산서 발행 가능 문서(레거시 issueInvoiceFromDoc 노출 조건) */
const canIssueInvoice = (so: So) =>
  so.status !== "draft" && so.status !== "cancelled";

/**
 * 수주(판매) — 슬라이스 5b-2. 레거시 #sales 패리티(발주 페이지 형제 구현):
 * 수주번호 검색(300ms 디바운스)·상태 필터·페이지네이션 목록, 수주 등록 모달
 * (고객 + 동적 명세), 상세 모달(헤더·라인·상태전이 액션·세금계산서),
 * 확정/출고(라인별 수량, OUT 이동)/매출반품/취소/삭제. 발주와 다른 점:
 * 출고는 재고를 차감하고 여신한도 초과 시 서버가 400 detail 로 차단한다
 * (그 detail 을 그대로 토스트). 출고·반품은 재고에도 반영되므로
 * ["stock"] 도 함께 invalidate 한다(재고 알림 배지 연동).
 */
export default function SalesPage() {
  const { can } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const canWrite = can("sales:write");

  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);

  const [formOpen, setFormOpen] = useState(false);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [shipTarget, setShipTarget] = useState<So | null>(null);
  const [returnTarget, setReturnTarget] = useState<So | null>(null);

  // 레거시 debounce(…, 300) 패리티 — 수주번호 검색
  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(qInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [qInput]);

  const list = useQuery({
    queryKey: ["sales", "list", { page, q, status }],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/sales-orders", {
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

  const invalidateSales = () => {
    queryClient.invalidateQueries({ queryKey: ["sales"] });
  };
  // 출고/반품은 재고 이동을 만든다 — 현재고·내역·알림(+네비 배지)도 갱신
  const invalidateStock = () => {
    queryClient.invalidateQueries({ queryKey: ["stock"] });
  };

  // ---- 상태전이 액션(목록·상세 공용) ----

  const confirmSo = async (so: So) => {
    try {
      unwrap(
        await client.POST("/api/sales-orders/{so_id}/confirm", {
          params: { path: { so_id: so.id } },
        }),
      );
      toast("확정했습니다.");
      invalidateSales();
    } catch (err) {
      toast(errorMessage(err), true);
    }
  };

  const cancelSo = async (so: So) => {
    const ok = await confirm({
      title: "수주 취소",
      message: (
        <>
          수주 <b>{so.so_no}</b> 을(를) 취소할까요? 출고·수금 이력이 있으면 취소되지 않습니다.
        </>
      ),
      confirmText: "취소 처리",
      cancelText: "닫기",
      danger: true,
    });
    if (!ok) return;
    try {
      unwrap(
        await client.POST("/api/sales-orders/{so_id}/cancel", {
          params: { path: { so_id: so.id } },
        }),
      );
      toast("취소했습니다.");
      invalidateSales();
    } catch (err) {
      // 출고/수금 이력 있는 수주 취소 거부 등은 서버 400 detail
      toast(errorMessage(err), true);
    }
  };

  const deleteSo = async (so: So) => {
    const ok = await confirm({
      title: "수주 삭제",
      message: (
        <>
          수주 <b>{so.so_no}</b> 을(를) 삭제할까요? 되돌릴 수 없습니다.
        </>
      ),
      confirmText: "삭제",
      danger: true,
    });
    if (!ok) return;
    try {
      unwrap(
        await client.DELETE("/api/sales-orders/{so_id}", {
          params: { path: { so_id: so.id } },
        }),
      );
      toast("삭제했습니다.");
      setDetailId(null);
      invalidateSales();
    } catch (err) {
      toast(errorMessage(err), true);
    }
  };

  const submitShip = async (so: So, lines: QtyLinePayload[]) => {
    try {
      unwrap(
        await client.POST("/api/sales-orders/{so_id}/ship", {
          params: { path: { so_id: so.id } },
          body: { lines },
        }),
      );
      toast("출고 처리했습니다.");
      setShipTarget(null);
      invalidateSales();
      invalidateStock();
    } catch (err) {
      // 여신한도 초과("여신한도를 초과합니다 (한도 …, 현재 미수 …, 이번 출고 …)")·
      // 재고 부족·잔여 초과 등은 서버 400 detail 토스트(모달은 열어 둔다)
      toast(errorMessage(err), true);
    }
  };

  const submitReturn = async (so: So, lines: QtyLinePayload[]) => {
    try {
      unwrap(
        await client.POST("/api/sales-orders/{so_id}/return", {
          params: { path: { so_id: so.id } },
          body: { lines },
        }),
      );
      toast("반품 처리했습니다.");
      setReturnTarget(null);
      invalidateSales();
      invalidateStock();
    } catch (err) {
      // 반품가능 초과 등은 서버 400 detail 토스트
      toast(errorMessage(err), true);
    }
  };

  const columns: Column<So>[] = [
    { key: "so_no", header: "수주번호", code: true },
    { key: "partner_name", header: "고객" },
    {
      key: "status",
      header: "상태",
      render: (so) => <OrderStatusTag map={SO_STATUS_TAG} status={so.status} />,
    },
    {
      key: "total_amount",
      header: "합계",
      num: true,
      render: (so) => won(so.total_amount),
    },
    { key: "order_date", header: "일자" },
  ];

  return (
    <section>
      <div className={styles.pageHead}>
        <div>
          <h2 className={styles.title}>수주(판매)</h2>
          <p className={styles.subtitle}>
            고객 수주서 작성·확정·출고 관리
          </p>
        </div>
      </div>

      <Panel>
        <Toolbar>
          <input
            type="text"
            placeholder="수주번호 검색"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            aria-label="수주번호 검색"
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
            {SO_STATUS_FILTER_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <Spacer />
          {canWrite && (
            <Button onClick={() => setFormOpen(true)}>+ 수주 등록</Button>
          )}
        </Toolbar>

        <DataTable
          columns={columns}
          rows={list.isError ? [] : list.data?.items}
          rowKey={(so) => so.id}
          loading={list.isPending}
          emptyText={
            list.isError ? errorMessage(list.error) : "데이터가 없습니다."
          }
          onRowClick={(so) => setDetailId(so.id)}
          actions={(so) => (
            // 행 클릭(상세 열기)과 겹치지 않게 버튼에서 전파를 끊는다
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={(e) => {
                  e.stopPropagation();
                  setDetailId(so.id);
                }}
              >
                상세
              </Button>
              {canWrite && (
                <>
                  {canConfirm(so) && (
                    <Button
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        void confirmSo(so);
                      }}
                    >
                      확정
                    </Button>
                  )}
                  {canShip(so) && (
                    <Button
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        setShipTarget(so);
                      }}
                    >
                      출고
                    </Button>
                  )}
                  {canCancel(so) && (
                    <Button
                      variant="danger"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        void cancelSo(so);
                      }}
                    >
                      취소
                    </Button>
                  )}
                  {canDelete(so) && (
                    <Button
                      variant="danger"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        void deleteSo(so);
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
        <SoFormModal
          onClose={() => setFormOpen(false)}
          onSaved={() => {
            setFormOpen(false);
            invalidateSales();
          }}
        />
      )}

      {detailId !== null && (
        <SoDetailModal
          soId={detailId}
          onClose={() => setDetailId(null)}
          onConfirm={confirmSo}
          onShip={setShipTarget}
          onReturn={setReturnTarget}
          onCancel={cancelSo}
          onDelete={deleteSo}
        />
      )}

      {shipTarget && (
        <QtyLinesModal
          title={`출고 처리 · ${shipTarget.so_no}`}
          doneHeader="출고누계"
          maxHeader="잔여"
          defaultToMax
          emptyError="처리할 수량이 없습니다."
          rows={shipTarget.lines.map(
            (l): QtyLineRow => ({
              lineId: l.id,
              itemName: l.item_name,
              ordered: l.qty,
              done: l.shipped_qty,
              max: l.remaining_qty,
            }),
          )}
          onSubmit={(lines) => submitShip(shipTarget, lines)}
          onClose={() => setShipTarget(null)}
        />
      )}

      {returnTarget && (
        <QtyLinesModal
          title={`매출반품 · ${returnTarget.so_no}`}
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

// ==================== 수주 상세 모달 ====================

type SoLineRow = components["schemas"]["SalesOrderLineOut"];

/**
 * 레거시 viewOrder 패리티 + 상태전이 액션 바.
 * 항상 서버에서 최신 상세를 조회한다(["sales","detail",id] — 액션 성공 시
 * invalidate 로 함께 갱신). 세금계산서 여부는 invoice:read 가 있을 때만 조회.
 */
function SoDetailModal({
  soId,
  onClose,
  onConfirm,
  onShip,
  onReturn,
  onCancel,
  onDelete,
}: {
  soId: number;
  onClose: () => void;
  onConfirm: (so: So) => Promise<void>;
  onShip: (so: So) => void;
  onReturn: (so: So) => void;
  onCancel: (so: So) => Promise<void>;
  onDelete: (so: So) => Promise<void>;
}) {
  const { can } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const router = useRouter();
  const canWrite = can("sales:write");
  const canInvoiceRead = can("invoice:read");
  const canInvoiceWrite = can("invoice:write");
  const canPayWrite = can("payment:write");

  const detail = useQuery({
    queryKey: ["sales", "detail", soId],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/sales-orders/{so_id}", {
          params: { path: { so_id: soId } },
        }),
      ),
  });

  // 이 수주의 유효(발행) 세금계산서 — 문서당 1건만 허용되므로 첫 건이 전부다
  const invoice = useQuery({
    queryKey: ["sales", "detail", soId, "invoice"],
    enabled: canInvoiceRead,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/tax-invoices", {
          params: {
            query: {
              ref_type: "SO",
              ref_id: soId,
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
    queryClient.invalidateQueries({ queryKey: ["sales", "detail", soId] });
  };

  // 레거시 issueInvoiceFromDoc 패리티(발행 후 모달을 닫는 대신 상세를 갱신)
  const issueInvoice = async (so: So) => {
    const ok = await confirm({
      title: "세금계산서 발행",
      message: (
        <>
          수주 <b>{so.so_no}</b> 로 세금계산서를 발행할까요? 발행 시점의 금액이 그대로
          스냅샷으로 남습니다.
        </>
      ),
      confirmText: "발행",
    });
    if (!ok) return;
    try {
      const inv = unwrap(
        await client.POST("/api/tax-invoices", {
          body: { ref_type: "SO", ref_id: so.id, issue_date: "", note: "" },
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
    const ok = await confirm({
      title: "세금계산서 취소",
      message: (
        <>
          세금계산서 <b>{inv.invoice_no}</b> 을(를) 취소할까요?
        </>
      ),
      confirmText: "취소 처리",
      cancelText: "닫기",
      danger: true,
    });
    if (!ok) return;
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
        title="수주 상세"
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

  const so = detail.data;

  const lineColumns: Column<SoLineRow>[] = [
    { key: "item_name", header: "품목" },
    { key: "qty", header: "주문", num: true, render: (l) => won(l.qty) },
    {
      key: "shipped_qty",
      header: "출고",
      num: true,
      render: (l) => won(l.shipped_qty),
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
      title={so.so_no}
      onClose={onClose}
      footer={
        <Button variant="ghost" onClick={onClose}>
          닫기
        </Button>
      }
    >
      <dl className={styles.kv}>
        <dt>거래처</dt>
        <dd>{so.partner_name}</dd>
        <dt>상태</dt>
        <dd>
          <OrderStatusTag map={SO_STATUS_TAG} status={so.status} />
        </dd>
        <dt>일자</dt>
        <dd>{so.order_date}</dd>
        <dt>공급가액</dt>
        <dd>{won(so.total_amount)}</dd>
        <dt>부가세</dt>
        <dd>{won(so.tax_amount)}</dd>
        <dt>합계</dt>
        <dd>
          <strong>{won(so.grand_total)}</strong>
        </dd>
        <dt>수금</dt>
        <dd>{won(so.paid_amount)}</dd>
        <dt>미수</dt>
        <dd>
          <strong>{won(so.outstanding)}</strong>
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
        rows={so.lines}
        rowKey={(l) => l.id}
        emptyText="명세가 없습니다."
      />

      <div className={styles.actionBar}>
        {/* 레거시 payFromDoc 패리티 — 결제 화면으로 거래처·구분·문서·금액 프리필 이동 */}
        {canPayWrite && so.outstanding > 0 && (
          <Button
            onClick={() =>
              router.push(
                `/payments?ref_type=SO&ref_id=${so.id}&partner_id=${so.partner_id}&amount=${so.outstanding}`,
              )
            }
          >
            수금 등록
          </Button>
        )}
        {canWrite && canConfirm(so) && (
          <Button onClick={() => void onConfirm(so)}>확정</Button>
        )}
        {canWrite && canShip(so) && (
          <Button onClick={() => onShip(so)}>출고</Button>
        )}
        {canWrite && canReturn(so) && (
          <Button onClick={() => onReturn(so)}>매출반품</Button>
        )}
        {canWrite && canCancel(so) && (
          <Button variant="danger" onClick={() => void onCancel(so)}>
            취소
          </Button>
        )}
        {canWrite && canDelete(so) && (
          <Button variant="danger" onClick={() => void onDelete(so)}>
            삭제
          </Button>
        )}
        {canInvoiceWrite && canIssueInvoice(so) && !issuedInvoice && (
          <Button variant="ghost" onClick={() => void issueInvoice(so)}>
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

// ==================== 수주 등록 모달 ====================

const SO_FORM_FIELD_NAMES = ["partner_id", "order_date", "note"] as const;

/**
 * 레거시 openOrderForm("sales") 패리티 — 품목·거래처(공급처 전용 제외)를
 * 불러온 뒤 폼을 연다. 품목/고객이 없으면 안내만 표시한다.
 */
function SoFormModal({
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
  // 레거시 relevant 필터 패리티 — 수주는 공급처 전용(supplier)을 제외
  const customers =
    partners.data?.items.filter((p) => p.partner_type !== "supplier") ?? [];

  const note = pending
    ? "불러오는 중…"
    : error
      ? errorMessage(error)
      : items.data!.items.length === 0
        ? "먼저 품목을 등록하세요."
        : customers.length === 0
          ? "먼저 고객을 등록하세요."
          : null;

  if (note !== null) {
    return (
      <Modal
        title="수주 등록"
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
    <SoFormInner
      items={items.data!.items}
      customers={customers}
      onClose={onClose}
      onSaved={onSaved}
    />
  );
}

function SoFormInner({
  items,
  customers,
  onClose,
  onSaved,
}: {
  items: components["schemas"]["ItemOut"][];
  customers: components["schemas"]["PartnerOut"][];
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const { register, control, handleSubmit, setError, formState } =
    useForm<OrderFormValues>({
      defaultValues: {
        partner_id: String(customers[0].id),
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
  const partnerOptions = customers.map(
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
        await client.POST("/api/sales-orders", {
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
        SO_FORM_FIELD_NAMES,
        setError,
      );
      if (!applied) toast(errorMessage(err), true);
    }
  });

  return (
    <Modal
      wide
      title="수주 등록"
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
              label="고객 *"
              options={partnerOptions}
              error={errors.partner_id?.message}
              {...register("partner_id", { required: "고객을 선택하세요." })}
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
