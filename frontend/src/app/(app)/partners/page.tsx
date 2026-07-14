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
import { Panel, Spacer, Toolbar } from "@/components/ui/Panel";
import StatCard from "@/components/ui/StatCard";
import Tag, { type TagVariant } from "@/components/ui/Tag";
import { useConfirm } from "@/components/ui/ConfirmProvider";
import { useToast } from "@/components/ui/Toast";
import { client, downloadFile, unwrap } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { useAuth } from "@/lib/auth/AuthProvider";
import { applyServerFieldErrors } from "@/lib/forms";
import { dateOnly, won } from "@/lib/format";

import styles from "./page.module.css";

type Partner = components["schemas"]["PartnerOut"];
type PartnerTxn = components["schemas"]["PartnerTxnOut"];

/** 레거시 페이지당 10건 패리티 */
const PAGE_SIZE = 10;

/** 레거시 TYPE_TAG 패리티 — 거래처 유형 태그(변형, 라벨) */
const TYPE_TAG: Record<string, [TagVariant, string]> = {
  customer: ["cust", "고객"],
  supplier: ["supp", "공급처"],
  both: ["both", "고객+공급"],
};

const PARTNER_TYPE_OPTIONS = [
  ["customer", "고객"],
  ["supplier", "공급처"],
  ["both", "고객+공급"],
] as const;

function typeTag(partnerType: string) {
  const entry = TYPE_TAG[partnerType];
  return entry ? (
    <Tag variant={entry[0]}>{entry[1]}</Tag>
  ) : (
    <Tag variant="gray">{partnerType}</Tag>
  );
}

/**
 * 거래처 관리(슬라이스 4) — 레거시 #partners 패리티:
 * 검색(300ms 디바운스)·유형 필터·페이지네이션 목록, 등록/수정 모달(RHF+서버 422),
 * 삭제(확인 후), 거래처 매입/매출 내역(원장) 모달.
 */
export default function PartnersPage() {
  const { can } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const canWrite = can("partner:write");

  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [page, setPage] = useState(1);

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Partner | null>(null);
  const [ledgerPartner, setLedgerPartner] = useState<Partner | null>(null);

  // 레거시 debounce(…, 300) 패리티 — 입력 후 300ms 뒤 검색 적용 + 1페이지로
  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(qInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [qInput]);

  const list = useQuery({
    queryKey: ["partners", "list", { page, q, type }],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/partners", {
          params: {
            query: {
              page,
              page_size: PAGE_SIZE,
              ...(q ? { q } : {}),
              ...(type ? { partner_type: type } : {}),
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  const openCreate = () => {
    setEditing(null);
    setFormOpen(true);
  };

  const openEdit = (partner: Partner) => {
    setEditing(partner);
    setFormOpen(true);
  };

  const removePartner = async (partner: Partner) => {
    const ok = await confirm({
      title: "거래처 삭제",
      message: (
        <>
          거래처 <b>{partner.name}</b> 을(를) 삭제할까요? 거래 이력이 있으면 삭제되지 않습니다.
        </>
      ),
      confirmText: "삭제",
      danger: true,
    });
    if (!ok) return;
    try {
      unwrap(
        await client.DELETE("/api/partners/{partner_id}", {
          params: { path: { partner_id: partner.id } },
        }),
      );
      toast("삭제했습니다.");
      queryClient.invalidateQueries({ queryKey: ["partners"] });
    } catch (err) {
      // 참조 이력이 있으면 서버가 409 + 안내 문구를 준다
      toast(errorMessage(err), true);
    }
  };

  const columns: Column<Partner>[] = [
    { key: "code", header: "코드", code: true },
    { key: "name", header: "이름" },
    {
      key: "partner_type",
      header: "유형",
      render: (p) => typeTag(p.partner_type),
    },
    { key: "ceo_name", header: "대표자", render: (p) => p.ceo_name || "-" },
    { key: "phone", header: "연락처", render: (p) => p.phone || "-" },
    {
      key: "business_no",
      header: "사업자번호",
      render: (p) => p.business_no || "-",
    },
  ];

  return (
    <section>
      <div className={styles.pageHead}>
        <div>
          <h2 className={styles.title}>거래처 관리</h2>
          <p className={styles.subtitle}>
            고객·공급처 마스터를 등록하고 관리합니다.
          </p>
        </div>
      </div>

      <Panel>
        <Toolbar>
          <input
            type="text"
            placeholder="이름 또는 코드 검색"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            aria-label="거래처 검색"
          />
          <select
            value={type}
            onChange={(e) => {
              setType(e.target.value);
              setPage(1);
            }}
            aria-label="유형 필터"
          >
            <option value="">전체 유형</option>
            {PARTNER_TYPE_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <Spacer />
          {canWrite && <Button onClick={openCreate}>+ 거래처 등록</Button>}
        </Toolbar>

        <DataTable
          columns={columns}
          rows={list.isError ? [] : list.data?.items}
          rowKey={(p) => p.id}
          loading={list.isPending}
          emptyText={
            list.isError ? errorMessage(list.error) : "데이터가 없습니다."
          }
          actions={(p) => (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setLedgerPartner(p)}
              >
                내역
              </Button>
              {canWrite && (
                <>
                  <Button variant="ghost" size="sm" onClick={() => openEdit(p)}>
                    수정
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={() => removePartner(p)}
                  >
                    삭제
                  </Button>
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
        <PartnerFormModal
          partner={editing}
          onClose={() => setFormOpen(false)}
          onSaved={() => {
            setFormOpen(false);
            queryClient.invalidateQueries({ queryKey: ["partners"] });
          }}
        />
      )}

      {ledgerPartner && (
        <PartnerLedgerModal
          partner={ledgerPartner}
          onClose={() => setLedgerPartner(null)}
        />
      )}
    </section>
  );
}

// ==================== 등록/수정 모달 ====================

type PartnerFormValues = {
  name: string;
  partner_type: string;
  ceo_name: string;
  business_no: string;
  phone: string;
  email: string;
  credit_limit: number;
  address: string;
};

const PARTNER_FIELD_NAMES = [
  "name",
  "partner_type",
  "ceo_name",
  "business_no",
  "phone",
  "email",
  "credit_limit",
  "address",
] as const;

/** 레거시 openPartnerForm 패리티 — RHF + 서버 422 fields → 필드 에러 매핑 */
function PartnerFormModal({
  partner,
  onClose,
  onSaved,
}: {
  partner: Partner | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const { register, handleSubmit, setError, formState } =
    useForm<PartnerFormValues>({
      defaultValues: partner
        ? {
            name: partner.name,
            partner_type: partner.partner_type,
            ceo_name: partner.ceo_name,
            business_no: partner.business_no,
            phone: partner.phone,
            email: partner.email,
            credit_limit: partner.credit_limit,
            address: partner.address,
          }
        : {
            name: "",
            partner_type: "customer",
            ceo_name: "",
            business_no: "",
            phone: "",
            email: "",
            credit_limit: 0,
            address: "",
          },
    });
  const { errors, isSubmitting } = formState;

  const onSubmit = handleSubmit(async (values) => {
    const base = {
      name: values.name.trim(),
      partner_type: values.partner_type,
      ceo_name: values.ceo_name.trim(),
      business_no: values.business_no.trim(),
      phone: values.phone.trim(),
      email: values.email.trim(),
      address: values.address.trim(),
      // 레거시 패리티: 음수/비숫자는 0으로 보정
      credit_limit: Math.max(0, Number(values.credit_limit) || 0),
    };
    try {
      if (partner) {
        unwrap(
          await client.PUT("/api/partners/{partner_id}", {
            params: { path: { partner_id: partner.id } },
            body: base,
          }),
        );
      } else {
        unwrap(
          await client.POST("/api/partners", {
            body: { ...base, is_active: true },
          }),
        );
      }
      toast(partner ? "수정했습니다." : "등록했습니다.");
      onSaved();
    } catch (err) {
      const applied = applyServerFieldErrors<PartnerFormValues>(
        err,
        PARTNER_FIELD_NAMES,
        setError,
      );
      if (!applied) toast(errorMessage(err), true);
    }
  });

  return (
    <Modal
      title={partner ? "거래처 수정" : "거래처 등록"}
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
          <Field
            label="이름 *"
            error={errors.name?.message}
            {...register("name", { required: "이름을 입력하세요." })}
          />
          <SelectField
            label="유형"
            options={PARTNER_TYPE_OPTIONS}
            error={errors.partner_type?.message}
            {...register("partner_type")}
          />
          <Field
            label="대표자"
            error={errors.ceo_name?.message}
            {...register("ceo_name")}
          />
          <Field
            label="사업자등록번호"
            error={errors.business_no?.message}
            {...register("business_no")}
          />
          <Field
            label="연락처"
            error={errors.phone?.message}
            {...register("phone")}
          />
          <Field
            label="이메일"
            error={errors.email?.message}
            {...register("email")}
          />
          <Field
            label="여신한도(0=무제한)"
            type="number"
            min={0}
            error={errors.credit_limit?.message}
            {...register("credit_limit", { valueAsNumber: true })}
          />
          <FormFull>
            <Field
              label="주소"
              error={errors.address?.message}
              {...register("address")}
            />
          </FormFull>
        </FormGrid>
        {/* Enter 제출용(footer 버튼은 form 밖) */}
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}

// ==================== 매입/매출 내역(원장) 모달 ====================

/** 레거시 KIND_TAG 패리티 */
const KIND_TAG: Record<string, [TagVariant, string]> = {
  purchase: ["supp", "매입"],
  sales: ["cust", "매출"],
};

/**
 * 레거시 openPartnerLedger/loadPartnerLedger 패리티 — 총 매입/총 매출 요약(stat 2장),
 * 구분 필터, 내역 표 + 페이지네이션, 엑셀 내보내기(stock:read).
 */
function PartnerLedgerModal({
  partner,
  onClose,
}: {
  partner: Partner;
  onClose: () => void;
}) {
  const { can } = useAuth();
  const toast = useToast();
  const [page, setPage] = useState(1);
  const [kind, setKind] = useState("");

  const summary = useQuery({
    queryKey: ["partners", "ledger", partner.id, "summary"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/partners/{partner_id}/summary", {
          params: { path: { partner_id: partner.id } },
        }),
      ),
  });

  const txns = useQuery({
    queryKey: ["partners", "ledger", partner.id, "transactions", { page, kind }],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/partners/{partner_id}/transactions", {
          params: {
            path: { partner_id: partner.id },
            query: {
              page,
              page_size: PAGE_SIZE,
              ...(kind ? { kind } : {}),
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  const exportLedger = () => {
    const qs = new URLSearchParams({ partner_id: String(partner.id) });
    if (kind) qs.set("kind", kind);
    void downloadFile(
      `/api/reports/transactions/export?${qs.toString()}`,
      `partner_${partner.id}_매입매출.xlsx`,
    )
      .then(() => toast("엑셀 파일을 내려받았습니다."))
      .catch((err) => toast(errorMessage(err), true));
  };

  const columns: Column<PartnerTxn>[] = [
    { key: "created_at", header: "일자", render: (t) => dateOnly(t.created_at) },
    { key: "movement_no", header: "번호", code: true },
    {
      key: "kind",
      header: "구분",
      render: (t) => {
        const entry = KIND_TAG[t.kind];
        return entry ? (
          <Tag variant={entry[0]}>{entry[1]}</Tag>
        ) : (
          <Tag variant="gray">{t.kind}</Tag>
        );
      },
    },
    {
      key: "item_name",
      header: "품목",
      render: (t) => `${t.item_name} (${t.item_code})`,
    },
    { key: "quantity", header: "수량", num: true, render: (t) => won(t.quantity) },
    {
      key: "unit_price",
      header: "단가",
      num: true,
      render: (t) => won(t.unit_price),
    },
    { key: "amount", header: "금액", num: true, render: (t) => won(t.amount) },
  ];

  const summaryValue = (count?: number, amount?: number) =>
    summary.isPending
      ? "…"
      : summary.isError
        ? "-"
        : `${won(count)}건 · ₩${won(amount)}`;

  return (
    <Modal
      wide
      title={`매입/매출 내역 · ${partner.name}`}
      onClose={onClose}
      footer={
        <Button variant="ghost" onClick={onClose}>
          닫기
        </Button>
      }
    >
      <div className={styles.ledgerStats}>
        <StatCard
          label="총 매입"
          smallValue
          value={summaryValue(
            summary.data?.purchase_count,
            summary.data?.purchase_amount,
          )}
        />
        <StatCard
          label="총 매출"
          smallValue
          value={summaryValue(
            summary.data?.sales_count,
            summary.data?.sales_amount,
          )}
        />
      </div>

      <div className={styles.ledgerBar}>
        <select
          value={kind}
          onChange={(e) => {
            setKind(e.target.value);
            setPage(1);
          }}
          aria-label="구분 필터"
        >
          <option value="">전체</option>
          <option value="purchase">매입</option>
          <option value="sales">매출</option>
        </select>
        <div className={styles.ledgerSpacer} />
        {can("stock:read") && (
          <Button variant="ghost" size="sm" onClick={exportLedger}>
            엑셀 내보내기
          </Button>
        )}
      </div>

      <DataTable
        columns={columns}
        rows={txns.isError ? [] : txns.data?.items}
        rowKey={(t) => t.id}
        loading={txns.isPending}
        emptyText={txns.isError ? errorMessage(txns.error) : "내역이 없습니다."}
      />
      {txns.data && (
        <Pager
          page={txns.data.page}
          pages={txns.data.pages}
          total={txns.data.total}
          onPageChange={setPage}
        />
      )}
    </Modal>
  );
}
