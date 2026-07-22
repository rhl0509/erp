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
import Modal, { FormGrid } from "@/components/ui/Modal";
import { Panel, Spacer, Toolbar } from "@/components/ui/Panel";
import Tag from "@/components/ui/Tag";
import { useConfirm } from "@/components/ui/ConfirmProvider";
import { useToast } from "@/components/ui/Toast";
import { client, unwrap } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { useAuth } from "@/lib/auth/AuthProvider";
import { applyServerFieldErrors } from "@/lib/forms";
import { won } from "@/lib/format";

import styles from "./page.module.css";

type Item = components["schemas"]["ItemOut"];

/** 레거시 페이지당 10건 패리티 */
const PAGE_SIZE = 10;

/** 레거시 ITYPE 패리티 */
const ITEM_TYPE_LABEL: Record<string, string> = {
  product: "제품",
  material: "원자재",
  service: "서비스",
};

const ITEM_TYPE_OPTIONS = [
  ["product", "제품"],
  ["material", "원자재"],
  ["service", "서비스"],
] as const;

const TAX_TYPE_OPTIONS = [
  ["taxable", "과세(10%)"],
  ["exempt", "면세"],
] as const;

/** 활성 토글 — 레거시 창고 폼의 상태 select 관례(["true","활성"],["false","비활성"]) */
const ACTIVE_OPTIONS = [
  ["true", "활성"],
  ["false", "비활성"],
] as const;

/**
 * 품목 관리(슬라이스 4) — 레거시 #items 패리티:
 * 검색(300ms 디바운스)·유형 필터·페이지네이션 목록, 등록/수정 모달(RHF+서버 422),
 * 삭제(확인 후) + 활성 토글(수정 모달 상태 select).
 */
export default function ItemsPage() {
  const { can } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const canWrite = can("item:write");

  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [page, setPage] = useState(1);

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Item | null>(null);

  // 레거시 debounce(…, 300) 패리티
  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(qInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [qInput]);

  const list = useQuery({
    queryKey: ["items", "list", { page, q, type }],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/items", {
          params: {
            query: {
              page,
              page_size: PAGE_SIZE,
              ...(q ? { q } : {}),
              ...(type ? { item_type: type } : {}),
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

  const openEdit = (item: Item) => {
    setEditing(item);
    setFormOpen(true);
  };

  const removeItem = async (item: Item) => {
    const ok = await confirm({
      title: "품목 삭제",
      message: (
        <>
          품목 <b>{item.name}</b> 을(를) 삭제할까요? 거래·재고 이력이 있으면 삭제 대신
          비활성화해야 합니다.
        </>
      ),
      confirmText: "삭제",
      danger: true,
    });
    if (!ok) return;
    try {
      unwrap(
        await client.DELETE("/api/items/{item_id}", {
          params: { path: { item_id: item.id } },
        }),
      );
      toast("삭제했습니다.");
      queryClient.invalidateQueries({ queryKey: ["items"] });
    } catch (err) {
      // 참조 이력이 있으면 서버가 409 + "대신 비활성화하세요" 안내를 준다
      toast(errorMessage(err), true);
    }
  };

  const columns: Column<Item>[] = [
    { key: "code", header: "코드", code: true },
    {
      key: "name",
      header: "이름",
      render: (it) =>
        it.is_active ? (
          it.name
        ) : (
          <span className={styles.nameCell}>
            {it.name}
            <Tag variant="gray">비활성</Tag>
          </span>
        ),
    },
    { key: "spec", header: "규격", render: (it) => it.spec || "-" },
    { key: "unit", header: "단위" },
    {
      key: "item_type",
      header: "유형",
      render: (it) => (
        <Tag variant="gray">{ITEM_TYPE_LABEL[it.item_type] || it.item_type}</Tag>
      ),
    },
    {
      key: "purchase_price",
      header: "매입가",
      num: true,
      render: (it) => won(it.purchase_price),
    },
    {
      key: "sales_price",
      header: "매출가",
      num: true,
      render: (it) => won(it.sales_price),
    },
    {
      key: "safety_stock",
      header: "안전재고",
      num: true,
      render: (it) => (it.safety_stock ? won(it.safety_stock) : "-"),
    },
  ];

  return (
    <section>
      <div className={styles.pageHead}>
        <div>
          <h1 className={styles.title}>품목 관리</h1>
          <p className={styles.subtitle}>
            제품·원자재·서비스 품목 마스터를 관리합니다.
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
            aria-label="품목 검색"
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
            {ITEM_TYPE_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <Spacer />
          {canWrite && <Button onClick={openCreate}>+ 품목 등록</Button>}
        </Toolbar>

        <DataTable
          columns={columns}
          rows={list.data?.items}
          error={list.error}
          onRetry={() => void list.refetch()}
          busy={list.isFetching && !list.isPending}
          rowKey={(it) => it.id}
          loading={list.isPending}
          emptyText={"데이터가 없습니다."}
          actions={
            canWrite
              ? (it) => (
                  <>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => openEdit(it)}
                    >
                      수정
                    </Button>
                    <Button
                      variant="danger"
                      size="sm"
                      onClick={() => removeItem(it)}
                    >
                      삭제
                    </Button>
                  </>
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
        <ItemFormModal
          item={editing}
          onClose={() => setFormOpen(false)}
          onSaved={() => {
            setFormOpen(false);
            queryClient.invalidateQueries({ queryKey: ["items"] });
          }}
        />
      )}
    </section>
  );
}

// ==================== 등록/수정 모달 ====================

type ItemFormValues = {
  name: string;
  item_type: string;
  tax_type: string;
  spec: string;
  unit: string;
  purchase_price: number;
  sales_price: number;
  safety_stock: number;
  /** select 값이라 문자열("true"/"false") — 제출 시 boolean 변환 */
  is_active: string;
};

const ITEM_FIELD_NAMES = [
  "name",
  "item_type",
  "tax_type",
  "spec",
  "unit",
  "purchase_price",
  "sales_price",
  "safety_stock",
  "is_active",
] as const;

/** 레거시 openItemForm 패리티 + 활성 토글(상태 select) */
function ItemFormModal({
  item,
  onClose,
  onSaved,
}: {
  item: Item | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const { register, handleSubmit, setError, formState } =
    useForm<ItemFormValues>({
      defaultValues: item
        ? {
            name: item.name,
            item_type: item.item_type,
            tax_type: item.tax_type,
            spec: item.spec,
            unit: item.unit,
            purchase_price: item.purchase_price,
            sales_price: item.sales_price,
            safety_stock: item.safety_stock,
            is_active: String(item.is_active),
          }
        : {
            name: "",
            item_type: "product",
            tax_type: "taxable",
            spec: "",
            unit: "EA",
            purchase_price: 0,
            sales_price: 0,
            safety_stock: 0,
            is_active: "true",
          },
    });
  const { errors, isSubmitting } = formState;

  const onSubmit = handleSubmit(async (values) => {
    // 레거시 패리티: 가격은 숫자 보정, 안전재고는 음수 불가
    const payload = {
      name: values.name.trim(),
      item_type: values.item_type,
      tax_type: values.tax_type,
      spec: values.spec.trim(),
      unit: values.unit.trim() || "EA",
      purchase_price: Number(values.purchase_price) || 0,
      sales_price: Number(values.sales_price) || 0,
      safety_stock: Math.max(0, Number(values.safety_stock) || 0),
      is_active: values.is_active === "true",
    };
    try {
      if (item) {
        unwrap(
          await client.PUT("/api/items/{item_id}", {
            params: { path: { item_id: item.id } },
            body: payload,
          }),
        );
      } else {
        unwrap(await client.POST("/api/items", { body: payload }));
      }
      toast(item ? "수정했습니다." : "등록했습니다.");
      onSaved();
    } catch (err) {
      const applied = applyServerFieldErrors<ItemFormValues>(
        err,
        ITEM_FIELD_NAMES,
        setError,
      );
      if (!applied) toast(errorMessage(err), true);
    }
  });

  return (
    <Modal
      title={item ? "품목 수정" : "품목 등록"}
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
            options={ITEM_TYPE_OPTIONS}
            error={errors.item_type?.message}
            {...register("item_type")}
          />
          <SelectField
            label="부가세"
            options={TAX_TYPE_OPTIONS}
            error={errors.tax_type?.message}
            {...register("tax_type")}
          />
          <Field
            label="규격"
            error={errors.spec?.message}
            {...register("spec")}
          />
          <Field
            label="단위"
            error={errors.unit?.message}
            {...register("unit")}
          />
          <Field
            label="매입가"
            type="number"
            min={0}
            error={errors.purchase_price?.message}
            {...register("purchase_price", { valueAsNumber: true })}
          />
          <Field
            label="매출가"
            type="number"
            min={0}
            error={errors.sales_price?.message}
            {...register("sales_price", { valueAsNumber: true })}
          />
          <Field
            label="안전재고(0=미설정)"
            type="number"
            min={0}
            error={errors.safety_stock?.message}
            {...register("safety_stock", { valueAsNumber: true })}
          />
          <SelectField
            label="상태"
            options={ACTIVE_OPTIONS}
            error={errors.is_active?.message}
            {...register("is_active")}
          />
        </FormGrid>
        {/* Enter 제출용(footer 버튼은 form 밖) */}
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}
