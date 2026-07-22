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

import styles from "./page.module.css";

type Warehouse = components["schemas"]["WarehouseOut"];

/** 레거시 페이지당 10건 패리티 */
const PAGE_SIZE = 10;

/** 활성 토글 — 레거시 창고 폼의 상태 select 관례 */
const ACTIVE_OPTIONS = [
  ["true", "활성"],
  ["false", "비활성"],
] as const;

/**
 * 창고 관리(슬라이스 5a) — 레거시 #warehouses 패리티:
 * 검색(300ms 디바운스)·활성 필터·페이지네이션 목록, 등록/수정 모달(RHF+서버 422),
 * 기본지정(교체 확인 후 PUT is_default:true). 기본창고 불변식(정확히 1개·해제 불가·
 * 비활성 불가)은 서버가 400 으로 최종 검증하고 UI 는 안내 문구 + 토스트로 표면화한다.
 */
export default function WarehousesPage() {
  const { can } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const canWrite = can("stock:write");

  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [active, setActive] = useState("");
  const [page, setPage] = useState(1);

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Warehouse | null>(null);

  // 레거시 debounce(…, 300) 패리티
  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(qInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [qInput]);

  const list = useQuery({
    queryKey: ["warehouses", "list", { page, q, active }],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/warehouses", {
          params: {
            query: {
              page,
              page_size: PAGE_SIZE,
              ...(q ? { q } : {}),
              ...(active ? { active_only: true } : {}),
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  // 창고 변경은 재고 화면의 창고 필터·입출고 창고 이름 표시와 연동된다
  const invalidateAfterChange = () => {
    queryClient.invalidateQueries({ queryKey: ["warehouses"] });
    queryClient.invalidateQueries({ queryKey: ["stock"] });
  };

  const openCreate = () => {
    setEditing(null);
    setFormOpen(true);
  };

  const openEdit = (wh: Warehouse) => {
    setEditing(wh);
    setFormOpen(true);
  };

  // 레거시 setDefaultWarehouse 패리티 — 지정만 가능(해제·불변식은 서버가 400 으로 검증)
  const setDefault = async (wh: Warehouse) => {
    const ok = await confirm({
      title: "기본창고 지정",
      message: (
        <>
          <b>{wh.name}</b> 창고를 기본창고로 지정할까요? 기존 기본창고는 일반 창고로 바뀝니다.
          창고를 지정하지 않은 입출고는 기본창고로 처리됩니다.
        </>
      ),
      confirmText: "지정",
    });
    if (!ok) return;
    try {
      unwrap(
        await client.PUT("/api/warehouses/{warehouse_id}", {
          params: { path: { warehouse_id: wh.id } },
          body: { is_default: true },
        }),
      );
      toast("기본창고를 교체했습니다.");
      invalidateAfterChange();
    } catch (err) {
      toast(errorMessage(err), true);
    }
  };

  const columns: Column<Warehouse>[] = [
    { key: "code", header: "코드", code: true },
    { key: "name", header: "이름" },
    {
      key: "is_default",
      header: "기본",
      render: (w) =>
        w.is_default ? (
          <Tag variant="both">기본</Tag>
        ) : (
          <span className={styles.muted}>-</span>
        ),
    },
    {
      key: "is_active",
      header: "상태",
      render: (w) =>
        w.is_active ? <Tag>활성</Tag> : <Tag variant="gray">비활성</Tag>,
    },
  ];

  return (
    <section>
      <div className={styles.pageHead}>
        <div>
          <h1 className={styles.title}>창고 관리</h1>
          <p className={styles.subtitle}>
            다중창고 마스터를 관리합니다. 기본창고는 항상 1개이며 비활성화할 수
            없고, 다른 창고를 기본으로 지정해 교체합니다.
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
            aria-label="창고 검색"
          />
          <select
            value={active}
            onChange={(e) => {
              setActive(e.target.value);
              setPage(1);
            }}
            aria-label="활성 필터"
          >
            <option value="">전체</option>
            <option value="1">활성만</option>
          </select>
          <Spacer />
          {canWrite && <Button onClick={openCreate}>+ 창고 등록</Button>}
        </Toolbar>

        <DataTable
          columns={columns}
          rows={list.data?.items}
          error={list.error}
          onRetry={() => void list.refetch()}
          busy={list.isFetching && !list.isPending}
          rowKey={(w) => w.id}
          loading={list.isPending}
          emptyText={"데이터가 없습니다."}
          actions={
            canWrite
              ? (w) => (
                  <>
                    {!w.is_default && w.is_active && (
                      <Button size="sm" onClick={() => setDefault(w)}>
                        기본지정
                      </Button>
                    )}
                    <Button variant="ghost" size="sm" onClick={() => openEdit(w)}>
                      수정
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
        <WarehouseFormModal
          warehouse={editing}
          onClose={() => setFormOpen(false)}
          onSaved={() => {
            setFormOpen(false);
            invalidateAfterChange();
          }}
        />
      )}
    </section>
  );
}

// ==================== 등록/수정 모달 ====================

type WarehouseFormValues = {
  code: string;
  name: string;
  /** select 값이라 문자열("true"/"false") — 제출 시 boolean 변환 */
  is_default: string;
  is_active: string;
};

const WAREHOUSE_FIELD_NAMES = [
  "code",
  "name",
  "is_default",
  "is_active",
] as const;

/**
 * 레거시 openWarehouseForm 패리티 — 등록: 코드(빈 값 자동 채번)·이름·상태.
 * 수정: 코드·이름·기본창고(현재 기본이면 해제 불가 안내만)·상태(기본창고는 비활성 불가).
 * 기본창고 지정은 변경됐을 때만 is_default:true 를 보낸다(해제 요청은 서버가 400).
 */
function WarehouseFormModal({
  warehouse,
  onClose,
  onSaved,
}: {
  warehouse: Warehouse | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const { register, handleSubmit, setError, formState } =
    useForm<WarehouseFormValues>({
      defaultValues: warehouse
        ? {
            code: warehouse.code,
            name: warehouse.name,
            is_default: String(warehouse.is_default),
            is_active: String(warehouse.is_active),
          }
        : { code: "", name: "", is_default: "false", is_active: "true" },
    });
  const { errors, isSubmitting } = formState;

  // 현재 기본창고면 해제 불가(다른 창고를 기본지정해서 교체) — 안내 옵션만 노출
  const defaultOptions = warehouse?.is_default
    ? ([["true", "기본창고 (해제는 다른 창고를 기본지정)"]] as const)
    : ([
        ["false", "일반"],
        ["true", "기본창고로 지정"],
      ] as const);

  const onSubmit = handleSubmit(async (values) => {
    const code = values.code.trim();
    const name = values.name.trim();
    const isActive = values.is_active === "true";
    try {
      if (warehouse) {
        const body: components["schemas"]["WarehouseUpdate"] = {
          name,
          is_active: isActive,
        };
        if (code) body.code = code; // 수정 시 코드는 비울 수 없다
        // 기본창고 지정은 변경됐을 때만 보낸다(기본 해제 요청은 서버가 400 으로 막는다)
        if (values.is_default === "true" && !warehouse.is_default)
          body.is_default = true;
        unwrap(
          await client.PUT("/api/warehouses/{warehouse_id}", {
            params: { path: { warehouse_id: warehouse.id } },
            body,
          }),
        );
      } else {
        unwrap(
          await client.POST("/api/warehouses", {
            body: { code, name, is_active: isActive },
          }),
        );
      }
      toast(warehouse ? "수정했습니다." : "등록했습니다.");
      onSaved();
    } catch (err) {
      // 409(코드 중복)·400(기본창고 불변식)은 detail 토스트, 422 는 필드 에러로
      const applied = applyServerFieldErrors<WarehouseFormValues>(
        err,
        WAREHOUSE_FIELD_NAMES,
        setError,
      );
      if (!applied) toast(errorMessage(err), true);
    }
  });

  return (
    <Modal
      title={warehouse ? "창고 수정" : "창고 등록"}
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
            label={warehouse ? "코드 *" : "코드 (빈 값이면 자동 채번)"}
            error={errors.code?.message}
            {...register("code", {
              required: warehouse ? "코드를 입력하세요." : false,
            })}
          />
          <Field
            label="이름 *"
            error={errors.name?.message}
            {...register("name", { required: "이름을 입력하세요." })}
          />
          {warehouse && (
            <SelectField
              label="기본창고"
              options={defaultOptions}
              error={errors.is_default?.message}
              {...register("is_default")}
            />
          )}
          <SelectField
            label={warehouse ? "상태 (기본창고는 비활성 불가)" : "상태"}
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
