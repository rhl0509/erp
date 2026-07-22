"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";

import Button from "@/components/ui/Button";
import Modal, { FormFull, FormGrid } from "@/components/ui/Modal";
import Field from "@/components/ui/Field";
import { Panel } from "@/components/ui/Panel";
import { useToast } from "@/components/ui/Toast";
import PasswordStrength from "@/components/auth/PasswordStrength";
import { client, unwrap } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import { ME_QUERY_KEY, useAuth, type Me } from "@/lib/auth/AuthProvider";
import { applyServerFieldErrors } from "@/lib/forms";
import { usePasswordPolicy, validatePasswordClient } from "@/lib/auth/password";

import styles from "./page.module.css";

/**
 * 내 정보 — 프로필(이름·이메일·부서) 수정, 비밀번호 변경, 2단계 인증(TOTP) 설정,
 * 역할·보유 권한 조회. 데이터는 useAuth 의 me(GET /api/auth/me 캐시)를 쓰고,
 * 변경 후에는 ME_QUERY_KEY 를 invalidate 해 화면·상단 내비가 함께 갱신되게 한다.
 */
export default function MePage() {
  const { me } = useAuth();
  const [modal, setModal] = useState<"profile" | "password" | "totp" | null>(null);

  // (app) 레이아웃 가드가 me 확정 전에는 children 을 렌더하지 않지만 타입 방어
  if (!me) return null;

  const lastLogin = me.last_login_at
    ? new Date(me.last_login_at).toLocaleString("ko-KR")
    : "-";

  return (
    <section>
      <div className={styles.pageHead}>
        <div>
          <h1 className={styles.title}>내 정보</h1>
          <p className={styles.subtitle}>
            계정 정보와 보안 설정(비밀번호·2단계 인증)을 관리합니다.
          </p>
        </div>
        <Button onClick={() => setModal("profile")}>프로필 수정</Button>
      </div>

      <Panel className={styles.panel}>
        <dl className={styles.kv}>
          <dt>아이디</dt>
          <dd>{me.username}</dd>
          <dt>이름</dt>
          <dd>{me.full_name || "-"}</dd>
          <dt>이메일</dt>
          <dd>{me.email || "-"}</dd>
          <dt>부서</dt>
          <dd>{me.department || "-"}</dd>
          <dt>역할</dt>
          <dd>{me.roles.map((r) => r.name).join(", ") || "-"}</dd>
          <dt>최근 로그인</dt>
          <dd>{lastLogin}</dd>
        </dl>

        <div className={styles.permsBlock}>
          <div className={styles.permsHead}>보유 권한</div>
          {me.permissions.length > 0 ? (
            <div className={styles.chips}>
              {me.permissions.map((code) => (
                <span key={code} className={styles.chip}>
                  {code}
                </span>
              ))}
            </div>
          ) : (
            <span className={styles.muted}>없음</span>
          )}
        </div>

        <div className={styles.pwSection}>
          <div className={styles.secRow}>
            <div>
              <div className={styles.secTitle}>비밀번호</div>
              <div className={styles.secDesc}>
                변경하면 다른 기기의 기존 로그인 세션은 모두 로그아웃됩니다.
              </div>
            </div>
            <Button onClick={() => setModal("password")}>비밀번호 변경</Button>
          </div>

          <div className={styles.secRow}>
            <div>
              <div className={styles.secTitle}>
                2단계 인증 {me.totp_enabled ? "· 사용 중" : "· 사용 안 함"}
              </div>
              <div className={styles.secDesc}>
                {me.totp_enabled
                  ? "로그인 시 인증 앱의 6자리 코드를 함께 입력합니다."
                  : "인증 앱(Google Authenticator 등)으로 로그인을 한 단계 더 보호합니다."}
              </div>
            </div>
            <Button
              variant={me.totp_enabled ? "ghost" : "primary"}
              onClick={() => setModal("totp")}
            >
              {me.totp_enabled ? "2단계 인증 해제" : "2단계 인증 켜기"}
            </Button>
          </div>
        </div>
      </Panel>

      {modal === "profile" && <ProfileModal me={me} onClose={() => setModal(null)} />}
      {modal === "password" && <PasswordModal onClose={() => setModal(null)} />}
      {modal === "totp" &&
        (me.totp_enabled ? (
          <TotpDisableModal onClose={() => setModal(null)} />
        ) : (
          <TotpSetupModal onClose={() => setModal(null)} />
        ))}
    </section>
  );
}

// ==================== 프로필 수정 ====================

type ProfileValues = { full_name: string; email: string; department: string };

const PROFILE_FIELDS = ["full_name", "email", "department"] as const;

function ProfileModal({ me, onClose }: { me: Me; onClose: () => void }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const { register, handleSubmit, setError, formState } = useForm<ProfileValues>({
    defaultValues: {
      full_name: me.full_name ?? "",
      email: me.email ?? "",
      department: me.department ?? "",
    },
  });

  const onSubmit = handleSubmit(async (values) => {
    try {
      unwrap(
        await client.PUT("/api/auth/me", {
          body: {
            full_name: values.full_name.trim(),
            email: values.email.trim(),
            department: values.department.trim(),
          },
        }),
      );
      await queryClient.invalidateQueries({ queryKey: ME_QUERY_KEY });
      toast("프로필을 수정했습니다.");
      onClose();
    } catch (err) {
      const applied = applyServerFieldErrors<ProfileValues>(err, PROFILE_FIELDS, setError);
      if (!applied) toast(errorMessage(err), true);
    }
  });

  return (
    <Modal
      title="프로필 수정"
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button onClick={onSubmit} disabled={formState.isSubmitting}>
            {formState.isSubmitting ? "저장 중…" : "저장"}
          </Button>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate>
        <FormGrid>
          <FormFull>
            <Field
              label="이름"
              error={formState.errors.full_name?.message}
              {...register("full_name")}
            />
          </FormFull>
          <FormFull>
            <Field
              label="이메일"
              type="email"
              placeholder="비밀번호 재설정에 사용됩니다"
              error={formState.errors.email?.message}
              {...register("email")}
            />
          </FormFull>
          <FormFull>
            <Field
              label="부서"
              error={formState.errors.department?.message}
              {...register("department")}
            />
          </FormFull>
        </FormGrid>
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}

// ==================== 비밀번호 변경 ====================

type PasswordFormValues = {
  current_password: string;
  new_password: string;
  confirm: string;
};

const PASSWORD_FIELD_NAMES = ["current_password", "new_password"] as const;

/**
 * PUT /api/auth/me/password(204). 정책(길이·영문+숫자·아이디 포함 금지)은 서버가 단일
 * 소스이며(GET /api/auth/password-policy), 폼은 같은 규칙을 미리 검사해 왕복을 줄인다.
 * 변경 성공 시 서버가 token_version 을 올려 다른 세션을 끊고 이 세션만 새 쿠키로 살린다.
 */
function PasswordModal({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const { me } = useAuth();
  const policy = usePasswordPolicy();
  const minLength = policy?.min_length ?? 10;
  const { register, control, handleSubmit, setError, getValues, formState } =
    useForm<PasswordFormValues>({
      defaultValues: { current_password: "", new_password: "", confirm: "" },
    });
  const { errors, isSubmitting } = formState;
  // useWatch: useForm().watch 는 React Compiler 가 메모이즈하지 못해(lint 경고) 쓰지 않는다.
  const newPassword = useWatch({ control, name: "new_password" });

  const onSubmit = handleSubmit(async (values) => {
    try {
      unwrap(
        await client.PUT("/api/auth/me/password", {
          body: {
            current_password: values.current_password,
            new_password: values.new_password,
          },
        }),
      );
      toast("비밀번호를 변경했습니다. 다른 기기의 세션은 로그아웃되었습니다.");
      onClose();
    } catch (err) {
      const applied = applyServerFieldErrors<PasswordFormValues>(
        err,
        PASSWORD_FIELD_NAMES,
        setError,
      );
      if (!applied) toast(errorMessage(err), true);
    }
  });

  return (
    <Modal
      title="비밀번호 변경"
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
            <Field
              label="현재 비밀번호 *"
              type="password"
              autoComplete="current-password"
              error={errors.current_password?.message}
              {...register("current_password", {
                required: "현재 비밀번호를 입력하세요.",
              })}
            />
          </FormFull>
          <FormFull>
            <Field
              label="새 비밀번호 *"
              type="password"
              autoComplete="new-password"
              placeholder={policy?.text}
              error={errors.new_password?.message}
              {...register("new_password", {
                required: "새 비밀번호를 입력하세요.",
                validate: (value) =>
                  validatePasswordClient(value, minLength, me?.username) ?? true,
              })}
            />
            <PasswordStrength password={newPassword} minLength={minLength} />
          </FormFull>
          <FormFull>
            <Field
              label="새 비밀번호 확인 *"
              type="password"
              autoComplete="new-password"
              error={errors.confirm?.message}
              {...register("confirm", {
                required: "새 비밀번호를 한 번 더 입력하세요.",
                validate: (value) =>
                  value === getValues("new_password") ||
                  "새 비밀번호가 일치하지 않습니다.",
              })}
            />
          </FormFull>
        </FormGrid>
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}

// ==================== 2단계 인증 설정 ====================

/**
 * 설정 시작(POST /api/auth/2fa/setup) → QR/비밀키를 인증 앱에 등록 → 코드 입력으로 확정
 * (POST /api/auth/2fa/enable). 코드 검증에 성공해야 켜지므로, 등록 실패로 계정이 잠기지 않는다.
 */
function TotpSetupModal({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [setup, setSetup] = useState<{ secret: string; qr_svg: string } | null>(null);
  const [starting, setStarting] = useState(false);
  const { register, handleSubmit, setError, formState } = useForm<{ code: string }>({
    defaultValues: { code: "" },
  });

  const start = async () => {
    setStarting(true);
    try {
      const res = unwrap(await client.POST("/api/auth/2fa/setup"));
      setSetup({ secret: res.secret, qr_svg: res.qr_svg });
    } catch (err) {
      toast(errorMessage(err), true);
      onClose();
    } finally {
      setStarting(false);
    }
  };

  const onSubmit = handleSubmit(async (values) => {
    try {
      unwrap(
        await client.POST("/api/auth/2fa/enable", {
          body: { code: values.code.trim() },
        }),
      );
      await queryClient.invalidateQueries({ queryKey: ME_QUERY_KEY });
      toast("2단계 인증을 켰습니다. 다음 로그인부터 코드가 필요합니다.");
      onClose();
    } catch (err) {
      const applied = applyServerFieldErrors<{ code: string }>(err, ["code"], setError);
      if (!applied) toast(errorMessage(err), true);
    }
  });

  return (
    <Modal
      title="2단계 인증 켜기"
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
          {setup ? (
            <Button onClick={onSubmit} disabled={formState.isSubmitting}>
              {formState.isSubmitting ? "확인 중…" : "확인하고 켜기"}
            </Button>
          ) : (
            <Button onClick={start} disabled={starting}>
              {starting ? "준비 중…" : "설정 시작"}
            </Button>
          )}
        </>
      }
    >
      {!setup ? (
        <p className={styles.secDesc}>
          인증 앱(Google Authenticator, Authy 등)을 준비한 뒤 &quot;설정 시작&quot;을
          누르세요. QR 코드를 스캔하고 6자리 코드를 입력하면 켜집니다.
        </p>
      ) : (
        <form onSubmit={onSubmit} noValidate>
          <div
            className={styles.qr}
            /* 서버(qrcode 라이브러리)가 만든 SVG — 사용자 입력이 아니다 */
            dangerouslySetInnerHTML={{ __html: setup.qr_svg }}
          />
          <p className={styles.secDesc}>
            QR 을 스캔할 수 없으면 이 키를 직접 입력하세요:
            <br />
            <code className={styles.secret}>{setup.secret}</code>
          </p>
          <FormGrid>
            <FormFull>
              <Field
                label="인증 앱의 6자리 코드 *"
                inputMode="numeric"
                autoComplete="one-time-code"
                error={formState.errors.code?.message}
                {...register("code", { required: "코드를 입력하세요." })}
              />
            </FormFull>
          </FormGrid>
          <button type="submit" hidden />
        </form>
      )}
    </Modal>
  );
}

function TotpDisableModal({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const { register, handleSubmit, setError, formState } = useForm<{ password: string }>({
    defaultValues: { password: "" },
  });

  const onSubmit = handleSubmit(async (values) => {
    try {
      unwrap(
        await client.POST("/api/auth/2fa/disable", {
          body: { password: values.password },
        }),
      );
      await queryClient.invalidateQueries({ queryKey: ME_QUERY_KEY });
      toast("2단계 인증을 해제했습니다.");
      onClose();
    } catch (err) {
      const applied = applyServerFieldErrors<{ password: string }>(
        err,
        ["password"],
        setError,
      );
      if (!applied) toast(errorMessage(err), true);
    }
  });

  return (
    <Modal
      title="2단계 인증 해제"
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button onClick={onSubmit} disabled={formState.isSubmitting}>
            {formState.isSubmitting ? "해제 중…" : "해제"}
          </Button>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate>
        <p className={styles.secDesc}>
          본인 확인을 위해 비밀번호를 입력하세요. 해제하면 로그인 시 코드를 묻지 않습니다.
        </p>
        <FormGrid>
          <FormFull>
            <Field
              label="비밀번호 *"
              type="password"
              autoComplete="current-password"
              error={formState.errors.password?.message}
              {...register("password", { required: "비밀번호를 입력하세요." })}
            />
          </FormFull>
        </FormGrid>
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}
