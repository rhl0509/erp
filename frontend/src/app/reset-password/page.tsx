"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useForm, useWatch } from "react-hook-form";

import Button from "@/components/ui/Button";
import Field from "@/components/ui/Field";
import PasswordStrength from "@/components/auth/PasswordStrength";
import { useToast } from "@/components/ui/Toast";
import { client, unwrap } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import { applyServerFieldErrors } from "@/lib/forms";
import { usePasswordPolicy, validatePasswordClient } from "@/lib/auth/password";

import styles from "@/components/auth/auth.module.css";

type Values = { new_password: string; confirm: string };

/**
 * 비밀번호 재설정 — 메일로 받은 링크(/reset-password?token=...)로 들어온다.
 * POST /api/auth/reset-password 성공 시 기존 세션은 서버에서 전부 무효화된다(token_version).
 */
export default function ResetPasswordPage() {
  // useSearchParams 는 Suspense 경계가 필요하다(정적 렌더 시 CSR bailout).
  return (
    <Suspense fallback={<div className={styles.wrap} aria-busy="true" />}>
      <ResetPasswordForm />
    </Suspense>
  );
}

function ResetPasswordForm() {
  const toast = useToast();
  const token = useSearchParams().get("token") ?? "";
  const policy = usePasswordPolicy();
  const minLength = policy?.min_length ?? 10;
  const [done, setDone] = useState(false);

  const { register, control, handleSubmit, setError, getValues, formState } =
    useForm<Values>({ defaultValues: { new_password: "", confirm: "" } });
  const newPassword = useWatch({ control, name: "new_password" });

  const onSubmit = handleSubmit(async (values) => {
    try {
      unwrap(
        await client.POST("/api/auth/reset-password", {
          body: { token, new_password: values.new_password },
        }),
      );
      setDone(true);
    } catch (err) {
      const applied = applyServerFieldErrors<Values>(err, ["new_password"], setError);
      // 400(만료·사용된 링크)은 토스트로 — 필드 문제가 아니다
      if (!applied) toast(errorMessage(err), true);
    }
  });

  if (!token) {
    return (
      <div className={styles.wrap}>
        <div className={styles.card}>
          <h1>링크가 올바르지 않습니다</h1>
          <p className={styles.sub}>
            재설정 링크가 잘렸거나 만료되었습니다. 다시 요청해 주세요.
          </p>
          <div className={styles.hint}>
            <Link href="/forgot-password" className={styles.linkBtn}>
              비밀번호 찾기
            </Link>
          </div>
        </div>
      </div>
    );
  }

  if (done) {
    return (
      <div className={styles.wrap}>
        <div className={styles.card}>
          <h1>변경 완료</h1>
          <p className={styles.sub}>새 비밀번호로 로그인하세요.</p>
          <div className={styles.notice} role="status">
            보안을 위해 다른 기기의 기존 로그인 세션은 모두 로그아웃되었습니다.
          </div>
          <Link href="/login">
            <Button block>로그인하러 가기</Button>
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.wrap}>
      <form className={styles.card} onSubmit={onSubmit} noValidate>
        <h1>새 비밀번호 설정</h1>
        <p className={styles.sub}>{policy?.text ?? "새 비밀번호를 입력하세요."}</p>

        <Field
          label="새 비밀번호 *"
          type="password"
          autoComplete="new-password"
          autoFocus
          error={formState.errors.new_password?.message}
          {...register("new_password", {
            required: "새 비밀번호를 입력하세요.",
            validate: (value) => validatePasswordClient(value, minLength) ?? true,
          })}
        />
        <PasswordStrength password={newPassword} minLength={minLength} />

        <Field
          label="새 비밀번호 확인 *"
          type="password"
          autoComplete="new-password"
          error={formState.errors.confirm?.message}
          {...register("confirm", {
            required: "새 비밀번호를 한 번 더 입력하세요.",
            validate: (value) =>
              value === getValues("new_password") || "새 비밀번호가 일치하지 않습니다.",
          })}
        />

        <Button block type="submit" disabled={formState.isSubmitting}>
          {formState.isSubmitting ? "변경 중…" : "비밀번호 변경"}
        </Button>
        <div className={`${styles.hint} ${styles.hintTight}`}>
          <Link href="/login" className={styles.linkBtn}>
            로그인으로 돌아가기
          </Link>
        </div>
      </form>
    </div>
  );
}
