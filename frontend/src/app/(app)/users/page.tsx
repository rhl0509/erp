"use client";

import { useState } from "react";
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
import Tag from "@/components/ui/Tag";
import { useToast } from "@/components/ui/Toast";
import { client, unwrap } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { useAuth } from "@/lib/auth/AuthProvider";
import { applyServerFieldErrors } from "@/lib/forms";

import styles from "./page.module.css";

type UserRow = components["schemas"]["UserOut"];
type Role = components["schemas"]["RoleOut"];

/** 레거시 페이지당 10건 패리티 */
const PAGE_SIZE = 10;

/**
 * 회원 관리(슬라이스 7) — 레거시 #users 패리티:
 * 상태 필터(승인 대기/활성) 목록(아이디·이름·이메일·역할 태그·상태), 상세(역할·
 * 보유 권한 칩), 승인 워크플로(역할 부여 + is_active=true), 역할 변경, 비활성 토글,
 * 회원 등록. 변경 성공 시 ["users"] invalidate — 목록과 TopNav 승인 대기 배지
 * (["users","pending","count"])가 함께 갱신된다.
 */
export default function UsersPage() {
  const { can } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();
  const canWrite = can("user:write");

  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);

  const [detailTarget, setDetailTarget] = useState<UserRow | null>(null);
  /** 승인(pending) 또는 역할 변경(active) 모달 대상 */
  const [roleTarget, setRoleTarget] = useState<UserRow | null>(null);
  const [formOpen, setFormOpen] = useState(false);

  const list = useQuery({
    queryKey: ["users", "list", { page, status }],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/users", {
          params: {
            query: {
              page,
              page_size: PAGE_SIZE,
              ...(status ? { status } : {}),
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  // 목록 + 승인 대기 수(TopNav 배지) 동시 갱신
  const invalidateUsers = () => {
    queryClient.invalidateQueries({ queryKey: ["users"] });
  };

  // 레거시 deactivateUser 패리티 — 활성 토글(비활성화)
  const deactivateUser = async (u: UserRow) => {
    if (
      !window.confirm(
        `"${u.username}" 계정을 비활성화할까요? 해당 사용자는 로그인할 수 없게 됩니다.`,
      )
    )
      return;
    try {
      unwrap(
        await client.PUT("/api/users/{user_id}", {
          params: { path: { user_id: u.id } },
          body: { is_active: false },
        }),
      );
      toast("비활성화했습니다.");
      invalidateUsers();
    } catch (err) {
      toast(errorMessage(err), true);
    }
  };

  const columns: Column<UserRow>[] = [
    { key: "username", header: "아이디", code: true },
    { key: "full_name", header: "이름", render: (u) => u.full_name || "-" },
    { key: "email", header: "이메일", render: (u) => u.email || "-" },
    {
      key: "roles",
      header: "역할",
      render: (u) =>
        u.roles.length > 0 ? (
          <span className={styles.roleTags}>
            {u.roles.map((r) => (
              <Tag key={r.id} variant="gray">
                {r.name}
              </Tag>
            ))}
          </span>
        ) : (
          <span className={styles.muted}>없음</span>
        ),
    },
    {
      key: "is_active",
      header: "상태",
      render: (u) =>
        u.is_active ? (
          <Tag variant="both">활성</Tag>
        ) : (
          <Tag variant="supp">승인 대기</Tag>
        ),
    },
  ];

  return (
    <section>
      <div className={styles.pageHead}>
        <div>
          <h2 className={styles.title}>회원 관리</h2>
          <p className={styles.subtitle}>
            시스템 사용자와 역할·권한을 조회합니다.
          </p>
        </div>
      </div>

      <Panel>
        <Toolbar>
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
            aria-label="상태 필터"
          >
            <option value="">전체 상태</option>
            <option value="pending">승인 대기</option>
            <option value="active">활성</option>
          </select>
          <Spacer />
          {canWrite && (
            <Button onClick={() => setFormOpen(true)}>+ 회원 등록</Button>
          )}
        </Toolbar>

        <DataTable
          columns={columns}
          rows={list.isError ? [] : list.data?.items}
          rowKey={(u) => u.id}
          loading={list.isPending}
          emptyText={
            list.isError ? errorMessage(list.error) : "데이터가 없습니다."
          }
          rowClassName={(u) => (u.is_active ? undefined : styles.pendingRow)}
          onRowClick={(u) => setDetailTarget(u)}
          actions={(u) => (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={(e) => {
                  e.stopPropagation();
                  setDetailTarget(u);
                }}
              >
                상세
              </Button>
              {canWrite && !u.is_active && (
                <Button
                  size="sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    setRoleTarget(u);
                  }}
                >
                  승인
                </Button>
              )}
              {canWrite && u.is_active && (
                <>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={(e) => {
                      e.stopPropagation();
                      setRoleTarget(u);
                    }}
                  >
                    역할
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={(e) => {
                      e.stopPropagation();
                      void deactivateUser(u);
                    }}
                  >
                    비활성
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

      {detailTarget && (
        <UserDetailModal
          user={detailTarget}
          onClose={() => setDetailTarget(null)}
        />
      )}

      {roleTarget && (
        <RoleModal
          user={roleTarget}
          onClose={() => setRoleTarget(null)}
          onSaved={() => {
            setRoleTarget(null);
            invalidateUsers();
          }}
        />
      )}

      {formOpen && (
        <UserFormModal
          onClose={() => setFormOpen(false)}
          onSaved={() => {
            setFormOpen(false);
            invalidateUsers();
          }}
        />
      )}
    </section>
  );
}

// ==================== 역할 옵션(승인/역할변경/등록 공용) ====================

function useRoleOptions() {
  return useQuery({
    queryKey: ["roles", "options"],
    queryFn: async () => unwrap(await client.GET("/api/roles")),
  });
}

/** 레거시 openForm role_ids 셀렉트 패리티 — 단일 선택("" = 역할 없음) */
function roleSelectOptions(roles: Role[]): readonly (readonly [string, string])[] {
  return [
    ["", "(역할 없음)"] as const,
    ...roles.map((r) => [String(r.id), r.name] as const),
  ];
}

// ==================== 회원 상세 모달(읽기 전용) ====================

/** 레거시 viewUser 패리티 — 역할과 통합 권한 칩을 한눈에 */
function UserDetailModal({
  user,
  onClose,
}: {
  user: UserRow;
  onClose: () => void;
}) {
  const perms = [
    ...new Set(user.roles.flatMap((r) => r.permissions.map((p) => p.code))),
  ].sort();

  return (
    <Modal
      title={`회원 상세 · ${user.username}`}
      onClose={onClose}
      footer={
        <Button variant="ghost" onClick={onClose}>
          닫기
        </Button>
      }
    >
      <dl className={styles.kv}>
        <dt>아이디</dt>
        <dd>{user.username}</dd>
        <dt>이름</dt>
        <dd>{user.full_name || "-"}</dd>
        <dt>이메일</dt>
        <dd>{user.email || "-"}</dd>
        <dt>상태</dt>
        <dd>{user.is_active ? "활성" : "비활성"}</dd>
        <dt>역할</dt>
        <dd>{user.roles.map((r) => r.name).join(", ") || "-"}</dd>
      </dl>
      <div className={styles.permsHead}>보유 권한</div>
      {perms.length > 0 ? (
        <div className={styles.chips}>
          {perms.map((code) => (
            <span key={code} className={styles.chip}>
              {code}
            </span>
          ))}
        </div>
      ) : (
        <span className={styles.muted}>없음</span>
      )}
    </Modal>
  );
}

// ==================== 승인 / 역할 변경 모달 ====================

type RoleFormValues = { role_id: string };

/**
 * 레거시 approveUser 패리티 + 역할 변경.
 * 승인 대기 회원이면 "회원 승인"(역할 부여 + is_active=true),
 * 활성 회원이면 "역할 변경"(role_ids 만 갱신). 서버 계약: PUT /api/users/{id}.
 */
function RoleModal({
  user,
  onClose,
  onSaved,
}: {
  user: UserRow;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const roles = useRoleOptions();
  const approving = !user.is_active;
  const title = approving
    ? `회원 승인 · ${user.username}`
    : `역할 변경 · ${user.username}`;

  const { register, handleSubmit, formState } = useForm<RoleFormValues>({
    defaultValues: { role_id: String(user.roles[0]?.id ?? "") },
  });
  const { isSubmitting } = formState;

  const onSubmit = handleSubmit(async (values) => {
    try {
      unwrap(
        await client.PUT("/api/users/{user_id}", {
          params: { path: { user_id: user.id } },
          body: {
            role_ids: values.role_id ? [Number(values.role_id)] : [],
            ...(approving ? { is_active: true } : {}),
          },
        }),
      );
      toast(approving ? "승인했습니다." : "역할을 변경했습니다.");
      onSaved();
    } catch (err) {
      toast(errorMessage(err), true);
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
          <Button onClick={onSubmit} disabled={isSubmitting || roles.isPending}>
            {isSubmitting ? "저장 중…" : "저장"}
          </Button>
        </>
      }
    >
      {roles.isPending ? (
        <div className={styles.modalNote}>불러오는 중…</div>
      ) : roles.isError ? (
        <div className={styles.modalNote}>{errorMessage(roles.error)}</div>
      ) : (
        <form onSubmit={onSubmit} noValidate>
          <FormGrid>
            <FormFull>
              <SelectField
                label="역할 부여"
                options={roleSelectOptions(roles.data)}
                {...register("role_id")}
              />
            </FormFull>
          </FormGrid>
          <button type="submit" hidden />
        </form>
      )}
    </Modal>
  );
}

// ==================== 회원 등록 모달 ====================

type UserFormValues = {
  username: string;
  password: string;
  full_name: string;
  email: string;
  role_id: string;
};

const USER_FORM_FIELD_NAMES = [
  "username",
  "password",
  "full_name",
  "email",
] as const;

/** 레거시 openUserForm 패리티 — 아이디/비밀번호/이름/이메일/역할(단일) */
function UserFormModal({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const roles = useRoleOptions();
  const { register, handleSubmit, setError, formState } =
    useForm<UserFormValues>({
      defaultValues: {
        username: "",
        password: "",
        full_name: "",
        email: "",
        role_id: "",
      },
    });
  const { errors, isSubmitting } = formState;

  const onSubmit = handleSubmit(async (values) => {
    try {
      unwrap(
        await client.POST("/api/users", {
          body: {
            username: values.username.trim(),
            password: values.password,
            full_name: values.full_name.trim(),
            email: values.email.trim(),
            role_ids: values.role_id ? [Number(values.role_id)] : [],
          },
        }),
      );
      toast("회원을 등록했습니다.");
      onSaved();
    } catch (err) {
      const applied = applyServerFieldErrors<UserFormValues>(
        err,
        USER_FORM_FIELD_NAMES,
        setError,
      );
      // 아이디 중복(409) 등은 detail 토스트
      if (!applied) toast(errorMessage(err), true);
    }
  });

  return (
    <Modal
      title="회원 등록"
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
            label="아이디 *"
            autoComplete="off"
            error={errors.username?.message}
            {...register("username", { required: "아이디를 입력하세요." })}
          />
          <Field
            label="비밀번호 *"
            type="password"
            autoComplete="new-password"
            error={errors.password?.message}
            {...register("password", { required: "비밀번호를 입력하세요." })}
          />
          <Field
            label="이름"
            error={errors.full_name?.message}
            {...register("full_name")}
          />
          <Field
            label="이메일"
            type="email"
            error={errors.email?.message}
            {...register("email")}
          />
          <FormFull>
            <SelectField
              label="역할"
              options={roles.data ? roleSelectOptions(roles.data) : [["", "(역할 없음)"]]}
              {...register("role_id")}
            />
          </FormFull>
        </FormGrid>
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}
