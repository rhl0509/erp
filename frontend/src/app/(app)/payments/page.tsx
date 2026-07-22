"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
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
import StatCard from "@/components/ui/StatCard";
import { useConfirm } from "@/components/ui/ConfirmProvider";
import { useToast } from "@/components/ui/Toast";
import { client, unwrap } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { useAuth } from "@/lib/auth/AuthProvider";
import { applyServerFieldErrors } from "@/lib/forms";
import { won } from "@/lib/format";

import styles from "./page.module.css";

type Payment = components["schemas"]["PaymentOut"];
type PartnerOption = components["schemas"]["PartnerOut"];

/** 레거시 페이지당 10건 패리티 */
const PAGE_SIZE = 10;

/** 발주/수주 상세 → payFromDoc 프리필(레거시 payFromDoc 패리티, URL 쿼리로 전달) */
type PaymentPrefill = {
  partner_id: string;
  kind: "AP" | "AR";
  amount: number;
  ref_type: "PO" | "SO";
  ref_id: number;
};

/**
 * URL 쿼리(ref_type·ref_id·partner_id·amount) → payFromDoc 프리필.
 * 넷 중 문서 식별(ref_type/ref_id/partner_id)이 유효하지 않으면 null.
 * kind 는 백엔드 계약대로 문서 종류에서 파생한다(PO→AP, SO→AR).
 */
function parsePrefill(searchParams: URLSearchParams): PaymentPrefill | null {
  const refType = searchParams.get("ref_type");
  const refId = Number(searchParams.get("ref_id"));
  const partnerId = searchParams.get("partner_id");
  if (
    (refType !== "PO" && refType !== "SO") ||
    !Number.isInteger(refId) ||
    refId <= 0 ||
    !partnerId
  ) {
    return null;
  }
  return {
    partner_id: partnerId,
    kind: refType === "PO" ? "AP" : "AR",
    amount: Number(searchParams.get("amount")) || 0,
    ref_type: refType,
    ref_id: refId,
  };
}

/**
 * 결제 / 미수·미지급(슬라이스 7) — 레거시 #payments 패리티:
 * 거래처 잔액 패널(선택 거래처의 미지급/미수/여신 stat), 결제 목록(구분 AP/AR·
 * 거래처·기간 필터 + Pager), 결제/수금 등록 모달, 삭제.
 * 발주/수주 상세의 "지급/수금 등록"(payFromDoc)은 URL 쿼리로 넘어와 등록 모달을
 * 프리필로 연다 — 모달 열림이 URL 에서 파생되고, 닫으면 쿼리를 지운다.
 */
export default function PaymentsPage() {
  // Next 16: useSearchParams 는 Suspense 경계 안에서 사용(프리렌더 규칙)
  return (
    <Suspense fallback={null}>
      <PaymentsContent />
    </Suspense>
  );
}

function PaymentsContent() {
  const { can } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const router = useRouter();
  const searchParams = useSearchParams();
  const canWrite = can("payment:write");

  const [kind, setKind] = useState("");
  const [partnerId, setPartnerId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  const [balPartnerId, setBalPartnerId] = useState("");
  const [manualFormOpen, setManualFormOpen] = useState(false);

  // 발주/수주 상세에서 넘어온 프리필(레거시 payFromDoc 패리티) — URL 에서 파생
  const prefill = parsePrefill(searchParams);
  const formOpen = manualFormOpen || prefill !== null;

  const closeForm = () => {
    setManualFormOpen(false);
    // 프리필로 열린 모달이면 쿼리를 지워 닫힘 상태를 URL 에 반영한다
    if (prefill) router.replace("/payments");
  };

  // 거래처 옵션(잔액 선택·필터·등록 폼 공용 — 다른 페이지와 캐시 공유)
  const partnerOptions = useQuery({
    queryKey: ["partners", "options"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/partners", {
          params: { query: { page: 1, page_size: 100 } },
        }),
      ),
  });
  const partners = partnerOptions.data?.items ?? [];

  // 선택 거래처 잔액(레거시 showBalance 패리티)
  const balance = useQuery({
    queryKey: ["partners", "balance", balPartnerId],
    enabled: !!balPartnerId,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/partners/{partner_id}/balance", {
          params: { path: { partner_id: Number(balPartnerId) } },
        }),
      ),
  });

  const list = useQuery({
    queryKey: [
      "payments",
      "list",
      { page, kind, partnerId, dateFrom, dateTo },
    ],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/payments", {
          params: {
            query: {
              page,
              page_size: PAGE_SIZE,
              ...(kind ? { kind } : {}),
              ...(partnerId ? { partner_id: Number(partnerId) } : {}),
              ...(dateFrom ? { date_from: dateFrom } : {}),
              ...(dateTo ? { date_to: dateTo } : {}),
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  // 등록/삭제 후 갱신 — 목록 + 거래처 잔액, 문서 귀속 결제면 해당 문서 목록/상세도
  // (발주/수주의 지급·미지급 표시가 바뀐다 — 레거시 prefill.reload 패리티)
  const invalidateAfterChange = (refType?: string) => {
    queryClient.invalidateQueries({ queryKey: ["payments"] });
    queryClient.invalidateQueries({ queryKey: ["partners", "balance"] });
    if (refType === "PO")
      queryClient.invalidateQueries({ queryKey: ["purchase"] });
    if (refType === "SO")
      queryClient.invalidateQueries({ queryKey: ["sales"] });
  };

  const deletePayment = async (p: Payment) => {
    const ok = await confirm({
      title: "결제 내역 삭제",
      message: (
        <>
          이 결제 내역을 삭제할까요? 거래처 잔액과 연결된 회계 전표도 함께 되돌아갑니다.
        </>
      ),
      confirmText: "삭제",
      danger: true,
    });
    if (!ok) return;
    try {
      unwrap(
        await client.DELETE("/api/payments/{payment_id}", {
          params: { path: { payment_id: p.id } },
        }),
      );
      toast("삭제했습니다.");
      invalidateAfterChange(p.ref_type);
    } catch (err) {
      toast(errorMessage(err), true);
    }
  };

  const columns: Column<Payment>[] = [
    { key: "payment_no", header: "번호", code: true },
    { key: "partner_name", header: "거래처" },
    {
      key: "kind",
      header: "구분",
      render: (p) => (
        <>
          {p.kind === "AP" ? "지급" : "수금"}
          {p.ref_type && (
            <span className={styles.muted}>
              {" "}
              {p.ref_type}#{p.ref_id}
            </span>
          )}
        </>
      ),
    },
    { key: "amount", header: "금액", num: true, render: (p) => won(p.amount) },
    { key: "method", header: "수단" },
    { key: "pay_date", header: "일자" },
  ];

  const b = balance.data;

  return (
    <section>
      <div className={styles.pageHead}>
        <div>
          <h1 className={styles.title}>결제 / 미수·미지급</h1>
          <p className={styles.subtitle}>지급(AP)·수금(AR) 기록과 거래처 잔액</p>
        </div>
      </div>

      <Panel className={styles.stack}>
        <PanelHead title="거래처 잔액" />
        <Toolbar>
          <select
            value={balPartnerId}
            onChange={(e) => setBalPartnerId(e.target.value)}
            aria-label="잔액 조회 거래처"
          >
            <option value="">거래처 선택…</option>
            {partners.map((p) => (
              <option key={p.id} value={String(p.id)}>
                {p.name} ({p.code})
              </option>
            ))}
          </select>
        </Toolbar>
        {balPartnerId &&
          (balance.isPending ? (
            <div className={styles.balNote}>불러오는 중…</div>
          ) : balance.isError ? (
            <div className={styles.balNote}>{errorMessage(balance.error)}</div>
          ) : (
            b && (
              <div className={styles.cards}>
                <StatCard
                  smallValue
                  label={`미지급 (매입 ${won(b.purchase_amount)} − 지급 ${won(b.ap_paid)})`}
                  value={won(b.ap_outstanding)}
                />
                <StatCard
                  smallValue
                  label={`미수 (매출 ${won(b.sales_amount)} − 수금 ${won(b.ar_collected)})`}
                  value={won(b.ar_outstanding)}
                />
                {b.credit_limit > 0 && (
                  <StatCard
                    smallValue
                    label={`여신 (한도 ${won(b.credit_limit)})`}
                    value={
                      <span
                        className={
                          b.credit_available < 0 ? styles.negative : undefined
                        }
                      >
                        가용 {won(b.credit_available)}
                      </span>
                    }
                  />
                )}
              </div>
            )
          ))}
      </Panel>

      <Panel>
        <Toolbar>
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              setPage(1);
            }}
            aria-label="구분 필터"
          >
            <option value="">전체 구분</option>
            <option value="AP">지급</option>
            <option value="AR">수금</option>
          </select>
          <select
            value={partnerId}
            onChange={(e) => {
              setPartnerId(e.target.value);
              setPage(1);
            }}
            aria-label="거래처 필터"
          >
            <option value="">전체 거래처</option>
            {partners.map((p) => (
              <option key={p.id} value={String(p.id)}>
                {p.name} ({p.code})
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
            <Button onClick={() => setManualFormOpen(true)}>
              + 결제/수금 등록
            </Button>
          )}
        </Toolbar>

        <DataTable
          columns={columns}
          rows={list.data?.items}
          error={list.error}
          onRetry={() => void list.refetch()}
          busy={list.isFetching && !list.isPending}
          rowKey={(p) => p.id}
          loading={list.isPending}
          emptyText={"데이터가 없습니다."}
          actions={
            canWrite
              ? (p) => (
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={() => void deletePayment(p)}
                  >
                    삭제
                  </Button>
                )
              : undefined
          }
        />
        {list.data && (
          <Pager
            page={list.data.page}
            pages={list.data.pages}
            total={list.data.total}
            onPageChange={setPage}
            busy={list.isFetching && !list.isPending}
          />
        )}
      </Panel>

      {formOpen && (
        <PaymentFormModal
          partners={partners}
          partnersPending={partnerOptions.isPending}
          partnersError={partnerOptions.error}
          prefill={prefill}
          onClose={closeForm}
          onSaved={(refType) => {
            closeForm();
            invalidateAfterChange(refType);
          }}
        />
      )}
    </section>
  );
}

// ==================== 결제/수금 등록 모달 ====================

type PaymentFormValues = {
  partner_id: string;
  kind: string;
  amount: number | string;
  method: string;
  pay_date: string;
  note: string;
};

const PAYMENT_FORM_FIELD_NAMES = [
  "partner_id",
  "kind",
  "amount",
  "method",
  "pay_date",
  "note",
] as const;

/** 레거시 openPaymentForm 수단 셀렉트 패리티 */
const METHOD_OPTIONS = [
  ["", "(선택)"],
  ["계좌이체", "계좌이체"],
  ["현금", "현금"],
  ["카드", "카드"],
  ["기타", "기타"],
] as const;

/**
 * 레거시 openPaymentForm 패리티 — 거래처·구분·금액·수단·일자·비고.
 * prefill(payFromDoc)이 있으면 거래처·구분·금액을 채우고 문서 귀속
 * (ref_type/ref_id)을 페이로드에 실어 보낸다. 문서·거래처·구분 정합과
 * 과지급 차단은 서버가 최종 판정한다(400 detail 토스트).
 */
function PaymentFormModal({
  partners,
  partnersPending,
  partnersError,
  prefill,
  onClose,
  onSaved,
}: {
  partners: PartnerOption[];
  partnersPending: boolean;
  partnersError: unknown;
  prefill: PaymentPrefill | null;
  onClose: () => void;
  onSaved: (refType?: string) => void;
}) {
  const title = prefill ? "문서 결제/수금 등록" : "결제/수금 등록";

  const note = partnersPending
    ? "불러오는 중…"
    : partnersError
      ? errorMessage(partnersError)
      : partners.length === 0
        ? "먼저 거래처를 등록하세요."
        : null;

  if (note !== null) {
    return (
      <Modal
        title={title}
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
    <PaymentFormInner
      title={title}
      partners={partners}
      prefill={prefill}
      onClose={onClose}
      onSaved={onSaved}
    />
  );
}

function PaymentFormInner({
  title,
  partners,
  prefill,
  onClose,
  onSaved,
}: {
  title: string;
  partners: PartnerOption[];
  prefill: PaymentPrefill | null;
  onClose: () => void;
  onSaved: (refType?: string) => void;
}) {
  const toast = useToast();
  const { register, handleSubmit, setError, formState } =
    useForm<PaymentFormValues>({
      defaultValues: {
        partner_id: prefill?.partner_id ?? String(partners[0].id),
        kind: prefill?.kind ?? "AP",
        amount: prefill?.amount ?? 0,
        method: "",
        pay_date: "",
        note: "",
      },
    });
  const { errors, isSubmitting } = formState;

  const partnerSelectOptions = partners.map(
    (p) => [String(p.id), `${p.name} (${p.code})`] as const,
  );

  const onSubmit = handleSubmit(async (values) => {
    try {
      unwrap(
        await client.POST("/api/payments", {
          body: {
            partner_id: Number(values.partner_id),
            kind: values.kind,
            amount: Number(values.amount) || 0,
            method: values.method,
            pay_date: values.pay_date,
            note: values.note.trim(),
            ref_type: prefill?.ref_type ?? "",
            ref_id: prefill?.ref_id ?? null,
          },
        }),
      );
      toast("등록했습니다.");
      onSaved(prefill?.ref_type);
    } catch (err) {
      const applied = applyServerFieldErrors<PaymentFormValues>(
        err,
        PAYMENT_FORM_FIELD_NAMES,
        setError,
      );
      if (!applied) toast(errorMessage(err), true);
    }
  });

  return (
    <Modal
      title={title}
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
      {prefill && (
        <div className={styles.refNote}>
          연계 문서: {prefill.ref_type}#{prefill.ref_id}
        </div>
      )}
      <form onSubmit={onSubmit} noValidate>
        <FormGrid>
          <SelectField
            label="거래처 *"
            options={partnerSelectOptions}
            error={errors.partner_id?.message}
            {...register("partner_id", { required: "거래처를 선택하세요." })}
          />
          <SelectField
            label="구분"
            options={[
              ["AP", "지급 (미지급 차감)"],
              ["AR", "수금 (미수 차감)"],
            ]}
            error={errors.kind?.message}
            {...register("kind")}
          />
          <Field
            label="금액 *"
            type="number"
            error={errors.amount?.message}
            {...register("amount", { required: "금액을 입력하세요." })}
          />
          <SelectField
            label="수단"
            options={METHOD_OPTIONS}
            error={errors.method?.message}
            {...register("method")}
          />
          <Field
            label="일자"
            type="date"
            error={errors.pay_date?.message}
            {...register("pay_date")}
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
