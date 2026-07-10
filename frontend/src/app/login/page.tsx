"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { useForm, type FieldValues, type Path, type UseFormSetError } from "react-hook-form";

import Button from "@/components/ui/Button";
import Field from "@/components/ui/Field";
import { useToast } from "@/components/ui/Toast";
import { client, loginRequest, unwrap } from "@/lib/api/client";
import { ApiRequestError, errorMessage } from "@/lib/api/errors";
import { ME_QUERY_KEY } from "@/lib/auth/AuthProvider";

import styles from "./page.module.css";

type LoginValues = { username: string; password: string };
type RegisterValues = {
  username: string;
  password: string;
  full_name: string;
  email: string;
};

/**
 * 서버 422 {fields} → RHF 필드 에러 매핑. 폼에 없는 필드/그 외 오류는
 * 호출부 toast 로 처리하도록 "매핑했는지" 여부를 반환한다.
 */
function applyServerFieldErrors<T extends FieldValues>(
  err: unknown,
  fieldNames: readonly Path<T>[],
  setError: UseFormSetError<T>,
): boolean {
  if (!(err instanceof ApiRequestError) || !err.fields) return false;
  let applied = false;
  for (const name of fieldNames) {
    const message = err.fields[name];
    if (message) {
      setError(name, { type: "server", message });
      applied = true;
    }
  }
  return applied;
}

/**
 * 로그인/회원가입 — 레거시 index.html loginView/registerForm 패리티.
 * 로그인은 form-encoded(loginRequest 헬퍼), 회원가입은 JSON(POST /api/auth/register).
 * 비보호 라우트: (app) 셸 밖이라 인증 가드를 타지 않는다.
 */
export default function LoginPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const toast = useToast();

  const [mode, setMode] = useState<"login" | "register">("login");
  const [registerDone, setRegisterDone] = useState(false);

  const loginForm = useForm<LoginValues>({
    // 레거시 로그인 폼의 프리필 값 패리티(초기 계정)
    defaultValues: { username: "admin", password: "admin1234" },
  });
  const registerForm = useForm<RegisterValues>({
    defaultValues: { username: "", password: "", full_name: "", email: "" },
  });

  const switchMode = (next: "login" | "register") => {
    setMode(next);
    setRegisterDone(false);
    loginForm.clearErrors();
    registerForm.clearErrors();
  };

  const onLogin = loginForm.handleSubmit(async (values) => {
    try {
      await loginRequest(values.username.trim(), values.password);
      // 새 토큰으로 me 를 다시 받아 셸 가드/권한 nav 가 최신 상태로 뜨게 한다.
      await queryClient.invalidateQueries({ queryKey: ME_QUERY_KEY });
      router.replace("/dashboard");
    } catch (err) {
      const applied = applyServerFieldErrors<LoginValues>(
        err,
        ["username", "password"],
        loginForm.setError,
      );
      if (!applied) toast(errorMessage(err), true);
    }
  });

  const onRegister = registerForm.handleSubmit(async (values) => {
    setRegisterDone(false);
    try {
      unwrap(
        await client.POST("/api/auth/register", {
          body: {
            username: values.username.trim(),
            password: values.password,
            full_name: values.full_name.trim(),
            email: values.email.trim(),
          },
        }),
      );
      registerForm.reset();
      setRegisterDone(true);
    } catch (err) {
      const applied = applyServerFieldErrors<RegisterValues>(
        err,
        ["username", "password", "full_name", "email"],
        registerForm.setError,
      );
      if (!applied) toast(errorMessage(err), true);
    }
  });

  return (
    <div className={styles.wrap}>
      {mode === "login" ? (
        <form className={styles.card} onSubmit={onLogin} noValidate>
          <h1>ERP 관리 콘솔</h1>
          <p className={styles.sub}>거래처·품목 마스터를 한 화면에서 관리하세요.</p>
          <Field
            label="아이디"
            autoComplete="username"
            error={loginForm.formState.errors.username?.message}
            {...loginForm.register("username", {
              required: "아이디를 입력하세요.",
            })}
          />
          <Field
            label="비밀번호"
            type="password"
            autoComplete="current-password"
            error={loginForm.formState.errors.password?.message}
            {...loginForm.register("password", {
              required: "비밀번호를 입력하세요.",
            })}
          />
          <Button block type="submit" disabled={loginForm.formState.isSubmitting}>
            {loginForm.formState.isSubmitting ? "로그인 중…" : "로그인"}
          </Button>
          <div className={styles.hint}>
            초기 계정 &nbsp;<b>admin / admin1234</b>
          </div>
          <div className={`${styles.hint} ${styles.hintTight}`}>
            계정이 없으신가요?{" "}
            <button
              type="button"
              className={styles.linkBtn}
              onClick={() => switchMode("register")}
            >
              회원가입
            </button>
          </div>
        </form>
      ) : (
        <form className={styles.card} onSubmit={onRegister} noValidate>
          <h1>회원가입</h1>
          <p className={styles.sub}>
            가입 후 <b>관리자 승인</b>을 받아야 로그인할 수 있습니다.
          </p>
          <Field
            label="아이디 *"
            autoComplete="username"
            placeholder="2자 이상"
            error={registerForm.formState.errors.username?.message}
            {...registerForm.register("username", {
              required: "아이디를 입력하세요.",
              minLength: { value: 2, message: "아이디는 2자 이상이어야 합니다." },
            })}
          />
          <Field
            label="비밀번호 *"
            type="password"
            autoComplete="new-password"
            placeholder="4자 이상"
            error={registerForm.formState.errors.password?.message}
            {...registerForm.register("password", {
              required: "비밀번호를 입력하세요.",
              minLength: { value: 4, message: "비밀번호는 4자 이상이어야 합니다." },
            })}
          />
          <Field
            label="이름"
            error={registerForm.formState.errors.full_name?.message}
            {...registerForm.register("full_name")}
          />
          <Field
            label="이메일"
            type="email"
            error={registerForm.formState.errors.email?.message}
            {...registerForm.register("email")}
          />
          <Button block type="submit" disabled={registerForm.formState.isSubmitting}>
            {registerForm.formState.isSubmitting ? "신청 중…" : "가입 신청"}
          </Button>
          {registerDone && (
            <div className={styles.ok} role="status">
              가입 신청이 완료되었습니다. 관리자 승인 후 로그인할 수 있습니다.
            </div>
          )}
          <div className={`${styles.hint} ${styles.hintTight}`}>
            이미 계정이 있으신가요?{" "}
            <button
              type="button"
              className={styles.linkBtn}
              onClick={() => switchMode("login")}
            >
              로그인
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
