"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";

import Button from "@/components/ui/Button";
import Field from "@/components/ui/Field";
import PasswordStrength from "@/components/auth/PasswordStrength";
import { useToast } from "@/components/ui/Toast";
import { client, unwrap } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import { applyServerFieldErrors } from "@/lib/forms";
import { ME_QUERY_KEY, useAuth } from "@/lib/auth/AuthProvider";
import { usePasswordPolicy, validatePasswordClient } from "@/lib/auth/password";

import styles from "@/components/auth/auth.module.css";

type Values = { current_password: string; new_password: string; confirm: string };

/**
 * 비밀번호 강제 변경 — 임시비밀번호로 로그인했거나, 정책에 미달하는 비밀번호를 쓰는 계정.
 * 서버가 업무 API 를 403 으로 막고((app) 셸도 여기로 보낸다) 변경해야 풀린다.
 * 변경 성공 시 서버가 새 세션 쿠키를 심어 주므로 재로그인 없이 대시보드로 간다.
 */
export default function ChangePasswordPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const toast = useToast();
  const { me, isLoading, logout } = useAuth();
  const policy = usePasswordPolicy();
  const minLength = policy?.min_length ?? 10;

  const { register, control, handleSubmit, setError, getValues, formState } =
    useForm<Values>({
      defaultValues: { current_password: "", new_password: "", confirm: "" },
    });
  const newPassword = useWatch({ control, name: "new_password" });

  useEffect(() => {
    if (isLoading) return;
    if (!me) router.replace("/login");
    // 강제 변경 대상이 아닌데 들어온 경우(직접 URL 입력) — 업무 화면으로 돌려보낸다.
    else if (!me.must_change_password) router.replace("/dashboard");
  }, [isLoading, me, router]);

  if (isLoading || !me) return <div className={styles.wrap} aria-busy="true" />;

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
      await queryClient.invalidateQueries({ queryKey: ME_QUERY_KEY });
      toast("비밀번호를 변경했습니다.");
      router.replace("/dashboard");
    } catch (err) {
      const applied = applyServerFieldErrors<Values>(
        err,
        ["current_password", "new_password"],
        setError,
      );
      if (!applied) toast(errorMessage(err), true);
    }
  });

  return (
    <div className={styles.wrap}>
      <form className={styles.card} onSubmit={onSubmit} noValidate>
        <h1>비밀번호 변경이 필요합니다</h1>
        <p className={styles.sub}>
          {me.username} 님, 계속하려면 새 비밀번호를 설정하세요.
        </p>

        <div className={styles.notice}>
          임시 비밀번호이거나 현재 비밀번호가 보안 정책에 미달합니다. 변경 전에는 업무
          화면을 사용할 수 없습니다.
        </div>

        <Field
          label="현재 비밀번호 *"
          type="password"
          autoComplete="current-password"
          error={formState.errors.current_password?.message}
          {...register("current_password", {
            required: "현재(임시) 비밀번호를 입력하세요.",
          })}
        />
        <Field
          label="새 비밀번호 *"
          type="password"
          autoComplete="new-password"
          placeholder={policy?.text ?? `${minLength}자 이상, 영문+숫자`}
          error={formState.errors.new_password?.message}
          {...register("new_password", {
            required: "새 비밀번호를 입력하세요.",
            validate: (value) =>
              validatePasswordClient(value, minLength, me.username) ?? true,
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
          {formState.isSubmitting ? "변경 중…" : "변경하고 계속"}
        </Button>
        <div className={`${styles.hint} ${styles.hintTight}`}>
          <button type="button" className={styles.linkBtn} onClick={logout}>
            로그아웃
          </button>
        </div>
      </form>
    </div>
  );
}
