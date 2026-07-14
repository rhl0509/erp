"use client";

import { useState } from "react";
import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useForm } from "react-hook-form";

import Button from "@/components/ui/Button";
import ConfirmModal from "@/components/ui/ConfirmModal";
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
import { usePasswordPolicy, validatePasswordClient } from "@/lib/auth/password";
import { applyServerFieldErrors } from "@/lib/forms";

import styles from "./page.module.css";

type UserRow = components["schemas"]["UserOut"];
type Role = components["schemas"]["RoleOut"];

/** 레거시 페이지당 10건 패리티 */
const PAGE_SIZE = 10;

const STATUS_LABEL: Record<string, string> = {
  active: "활성",
  pending: "승인 대기",
  rejected: "거절",
};

/**
 * 회원 관리 — 상태 필터(승인 대기/활성/거절) 목록, 상세(가입 정보·역할·보유 권한),
 * 승인(역할 부여 + is_active=true) · 거절(사유 기록) · 역할 변경 · 비활성 ·
 * 임시 비밀번호 발급(비번 분실 구제) · 2단계 인증 해제(인증 앱 분실 구제) · 회원 등록.
 *
 * 변경 성공 시 ["users"] invalidate — 목록과 TopNav 승인 대기 배지
 * (["users","pending","count"])가 함께 갱신된다.
 * 확인이 필요한 동작은 window.confirm 이 아니라 앱 내 ConfirmModal 을 쓴다.
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
  const [deactivateTarget, setDeactivateTarget] = useState<UserRow | null>(null);
  const [rejectTarget, setRejectTarget] = useState<UserRow | null>(null);
  const [tempPwTarget, setTempPwTarget] = useState<UserRow | null>(null);
  const [totpTarget, setTotpTarget] = useState<UserRow | null>(null);

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

  // 비활성화 = 로그인 차단 + 발급된 세션 즉시 무효화(서버 token_version)
  const deactivateUser = async (u: UserRow) => {
    try {
      unwrap(
        await client.PUT("/api/users/{user_id}", {
          params: { path: { user_id: u.id } },
          body: { is_active: false },
        }),
      );
      toast("비활성화했습니다. 해당 사용자의 기존 세션도 종료됩니다.");
      setDeactivateTarget(null);
      invalidateUsers();
    } catch (err) {
      toast(errorMessage(err), true);
    }
  };

  const disableTotp = async (u: UserRow) => {
    try {
      unwrap(
        await client.POST("/api/users/{user_id}/2fa/disable", {
          params: { path: { user_id: u.id } },
        }),
      );
      toast("2단계 인증을 해제했습니다.");
      setTotpTarget(null);
      invalidateUsers();
    } catch (err) {
      toast(errorMessage(err), true);
    }
  };

  const columns: Column<UserRow>[] = [
    { key: "username", header: "아이디", code: true },
    { key: "full_name", header: "이름", render: (u) => u.full_name || "-" },
    { key: "email", header: "이메일", render: (u) => u.email || "-" },
    { key: "department", header: "부서", render: (u) => u.department || "-" },
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
      key: "status",
      header: "상태",
      render: (u) => (
        <span className={styles.roleTags}>
          {u.status === "active" && <Tag variant="both">활성</Tag>}
          {u.status === "pending" && <Tag variant="supp">승인 대기</Tag>}
          {u.status === "rejected" && <Tag variant="gray">거절</Tag>}
          {u.totp_enabled && <Tag variant="gray">2FA</Tag>}
        </span>
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
            <option value="rejected">거절</option>
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
              {canWrite && u.status === "pending" && (
                <>
                  <Button
                    size="sm"
                    onClick={(e) => {
                      e.stopPropagation();
                      setRoleTarget(u);
                    }}
                  >
                    승인
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={(e) => {
                      e.stopPropagation();
                      setRejectTarget(u);
                    }}
                  >
                    거절
                  </Button>
                </>
              )}
              {canWrite && u.status === "rejected" && (
                <Button
                  size="sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    setRoleTarget(u);
                  }}
                >
                  승인으로 되돌리기
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
                    variant="ghost"
                    size="sm"
                    onClick={(e) => {
                      e.stopPropagation();
                      setTempPwTarget(u);
                    }}
                  >
                    임시비번
                  </Button>
                  {u.totp_enabled && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        setTotpTarget(u);
                      }}
                    >
                      2FA 해제
                    </Button>
                  )}
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={(e) => {
                      e.stopPropagation();
                      setDeactivateTarget(u);
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

      {deactivateTarget && (
        <ConfirmModal
          title="계정 비활성화"
          danger
          confirmText="비활성화"
          message={
            <>
              <b>{deactivateTarget.username}</b> 계정을 비활성화할까요? 해당 사용자는
              로그인할 수 없게 되고, <b>이미 로그인된 세션도 즉시 종료</b>됩니다.
            </>
          }
          onConfirm={() => deactivateUser(deactivateTarget)}
          onClose={() => setDeactivateTarget(null)}
        />
      )}

      {totpTarget && (
        <ConfirmModal
          title="2단계 인증 해제"
          confirmText="해제"
          message={
            <>
              <b>{totpTarget.username}</b> 계정의 2단계 인증을 해제할까요? 인증 앱을
              분실해 로그인하지 못하는 사용자를 구제하는 용도입니다. 해제 후에는 비밀번호만으로
              로그인합니다.
            </>
          }
          onConfirm={() => disableTotp(totpTarget)}
          onClose={() => setTotpTarget(null)}
        />
      )}

      {rejectTarget && (
        <RejectModal
          user={rejectTarget}
          onClose={() => setRejectTarget(null)}
          onSaved={() => {
            setRejectTarget(null);
            invalidateUsers();
          }}
        />
      )}

      {tempPwTarget && (
        <TempPasswordModal
          user={tempPwTarget}
          onClose={() => setTempPwTarget(null)}
          onIssued={invalidateUsers}
        />
      )}
    </section>
  );
}

// ==================== 가입 거절 ====================

/**
 * 거절은 계정을 지우지 않고 '거절' 상태로 표시한다(이력 보존 + 같은 아이디 재가입 방지).
 * 사유는 로그인 시도 시 본인에게 안내된다.
 */
function RejectModal({
  user,
  onClose,
  onSaved,
}: {
  user: UserRow;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const { register, handleSubmit, formState } = useForm<{ reason: string }>({
    defaultValues: { reason: "" },
  });

  const onSubmit = handleSubmit(async (values) => {
    try {
      unwrap(
        await client.POST("/api/users/{user_id}/reject", {
          params: { path: { user_id: user.id } },
          body: { reason: values.reason.trim() },
        }),
      );
      toast("가입을 거절했습니다.");
      onSaved();
    } catch (err) {
      toast(errorMessage(err), true);
    }
  });

  return (
    <Modal
      title={`가입 거절 · ${user.username}`}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button variant="danger" onClick={onSubmit} disabled={formState.isSubmitting}>
            {formState.isSubmitting ? "처리 중…" : "거절"}
          </Button>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate>
        <FormGrid>
          <FormFull>
            <Field
              label="거절 사유"
              placeholder="로그인 시도 시 본인에게 안내됩니다"
              error={formState.errors.reason?.message}
              {...register("reason")}
            />
          </FormFull>
        </FormGrid>
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}

// ==================== 임시 비밀번호 발급 ====================

/**
 * 비밀번호 분실 구제(SMTP 미설정 환경의 재설정 경로). 평문은 응답에 딱 한 번 나오므로
 * 화면에 표시해 관리자가 본인에게 전달한다. 발급 즉시 기존 세션이 끊기고, 사용자는 다음
 * 로그인에서 비밀번호 변경을 강제당한다(/change-password).
 */
function TempPasswordModal({
  user,
  onClose,
  onIssued,
}: {
  user: UserRow;
  onClose: () => void;
  onIssued: () => void;
}) {
  const toast = useToast();
  const [issued, setIssued] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const issue = async () => {
    setBusy(true);
    try {
      const res = unwrap(
        await client.POST("/api/users/{user_id}/temp-password", {
          params: { path: { user_id: user.id } },
        }),
      );
      setIssued(res.temp_password);
      onIssued();
    } catch (err) {
      toast(errorMessage(err), true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={`임시 비밀번호 발급 · ${user.username}`}
      onClose={onClose}
      footer={
        issued ? (
          <Button onClick={onClose}>닫기</Button>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose}>
              취소
            </Button>
            <Button onClick={() => void issue()} disabled={busy}>
              {busy ? "발급 중…" : "발급"}
            </Button>
          </>
        )
      }
    >
      {issued ? (
        <div className={styles.tempPw}>
          <p>
            아래 임시 비밀번호를 본인에게 전달하세요. <b>지금 화면을 닫으면 다시 볼 수 없습니다.</b>
          </p>
          <code className={styles.tempPwCode}>{issued}</code>
          <p className={styles.muted}>
            사용자는 이 비밀번호로 로그인한 뒤 새 비밀번호를 설정해야 업무 화면을 쓸 수 있습니다.
            기존 로그인 세션과 옛 비밀번호는 즉시 무효화되었습니다.
          </p>
        </div>
      ) : (
        <div className={styles.tempPw}>
          <p>
            <b>{user.username}</b> 계정의 비밀번호를 임시 비밀번호로 재설정할까요?
          </p>
          <p className={styles.muted}>
            기존 비밀번호와 로그인 세션이 즉시 무효화됩니다. 본인이 비밀번호를 잊었을 때만
            사용하세요.
          </p>
        </div>
      )}
    </Modal>
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
        <dt>부서</dt>
        <dd>{user.department || "-"}</dd>
        <dt>가입 사유</dt>
        <dd>{user.signup_reason || "-"}</dd>
        <dt>상태</dt>
        <dd>{STATUS_LABEL[user.status] ?? user.status}</dd>
        {user.status === "rejected" && (
          <>
            <dt>거절 사유</dt>
            <dd>{user.reject_reason || "-"}</dd>
          </>
        )}
        <dt>2단계 인증</dt>
        <dd>{user.totp_enabled ? "사용 중" : "사용 안 함"}</dd>
        <dt>최근 로그인</dt>
        <dd>
          {user.last_login_at
            ? new Date(user.last_login_at).toLocaleString("ko-KR")
            : "-"}
        </dd>
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
  const policy = usePasswordPolicy();
  const minLength = policy?.min_length ?? 10;
  const { register, handleSubmit, setError, getValues, formState } =
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
            placeholder={policy?.text}
            error={errors.password?.message}
            {...register("password", {
              required: "비밀번호를 입력하세요.",
              validate: (value) =>
                validatePasswordClient(value, minLength, getValues("username")) ?? true,
            })}
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
