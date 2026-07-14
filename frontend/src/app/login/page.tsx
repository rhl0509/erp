"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";

import Button from "@/components/ui/Button";
import Field from "@/components/ui/Field";
import PasswordStrength from "@/components/auth/PasswordStrength";
import { useToast } from "@/components/ui/Toast";
import { client, loginRequest, OtpRequiredError, unwrap } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import { applyServerFieldErrors } from "@/lib/forms";
import { ME_QUERY_KEY } from "@/lib/auth/AuthProvider";
import { usePasswordPolicy, validatePasswordClient } from "@/lib/auth/password";

import styles from "@/components/auth/auth.module.css";

type LoginValues = { username: string; password: string; otp: string };
type RegisterValues = {
  username: string;
  password: string;
  full_name: string;
  email: string;
  department: string;
  signup_reason: string;
};

/**
 * 로그인 / 회원가입.
 *
 * - 로그인: form-encoded(loginRequest). 2FA 계정이면 OtpRequiredError 를 받아 OTP 단계로 전환.
 *   비밀번호 강제 변경 대상(임시비번·정책 미달)이면 /change-password 로 보낸다.
 * - 회원가입: JSON(POST /api/auth/register). 가입은 비활성 상태로 생성되고 관리자 승인이 필요.
 *   비밀번호 정책·강도 안내는 서버(GET /api/auth/password-policy)를 단일 소스로 쓴다.
 * - 비보호 라우트: (app) 셸 밖이라 인증 가드를 타지 않는다.
 */
export default function LoginPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const toast = useToast();
  const policy = usePasswordPolicy();
  const minLength = policy?.min_length ?? 10;

  const [mode, setMode] = useState<"login" | "register">("login");
  const [registerDone, setRegisterDone] = useState(false);
  const [otpStep, setOtpStep] = useState(false);
  /** 아이디 중복 확인용 — 타이핑이 멎은 뒤(400ms) 확정되는 값 */
  const [checkedName, setCheckedName] = useState("");

  const loginForm = useForm<LoginValues>({
    defaultValues: { username: "", password: "", otp: "" },
  });
  const registerForm = useForm<RegisterValues>({
    defaultValues: {
      username: "",
      password: "",
      full_name: "",
      email: "",
      department: "",
      signup_reason: "",
    },
  });

  // useWatch: useForm().watch 는 React Compiler 가 메모이즈하지 못해(lint 경고) 쓰지 않는다.
  const registerPassword = useWatch({ control: registerForm.control, name: "password" });
  const registerUsername = useWatch({ control: registerForm.control, name: "username" });

  // 아이디 중복 확인 — 타이핑이 멎으면(400ms) 그 값을 확정하고, 확정된 값만 서버에 물어본다.
  useEffect(() => {
    const timer = setTimeout(() => setCheckedName(registerUsername.trim()), 400);
    return () => clearTimeout(timer);
  }, [registerUsername]);

  const usernameCheck = useQuery({
    queryKey: ["auth", "check-username", checkedName],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/auth/check-username", {
          params: { query: { username: checkedName } },
        }),
      ),
    enabled: mode === "register" && checkedName.length >= 2,
    retry: false, // 확인 실패(429 등)는 조용히 넘긴다 — 최종 판정은 제출 시 서버가 한다
  });

  // 입력이 바뀐 직후의 옛 응답을 표시하지 않도록, 확정값과 현재 입력이 같을 때만 반영한다.
  const usernameTaken =
    usernameCheck.data && checkedName === registerUsername.trim()
      ? !usernameCheck.data.available
      : null;

  const switchMode = (next: "login" | "register") => {
    setMode(next);
    setRegisterDone(false);
    setOtpStep(false);
    loginForm.clearErrors();
    registerForm.clearErrors();
  };

  const onLogin = loginForm.handleSubmit(async (values) => {
    try {
      const { mustChangePassword } = await loginRequest(
        values.username.trim(),
        values.password,
        values.otp.trim() || undefined,
      );
      // 새 세션으로 me 를 다시 받아 셸 가드/권한 nav 가 최신 상태로 뜨게 한다.
      await queryClient.invalidateQueries({ queryKey: ME_QUERY_KEY });
      router.replace(mustChangePassword ? "/change-password" : "/dashboard");
    } catch (err) {
      if (err instanceof OtpRequiredError) {
        setOtpStep(true);
        if (err.wrongCode) {
          loginForm.setError("otp", { type: "server", message: err.message });
        } else {
          loginForm.setValue("otp", "");
        }
        return;
      }
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
            department: values.department.trim(),
            signup_reason: values.signup_reason.trim(),
          },
        }),
      );
      registerForm.reset();
      setCheckedName("");
      setRegisterDone(true);
    } catch (err) {
      const applied = applyServerFieldErrors<RegisterValues>(
        err,
        ["username", "password", "full_name", "email", "department", "signup_reason"],
        registerForm.setError,
      );
      if (!applied) toast(errorMessage(err), true);
    }
  });

  if (mode === "login") {
    return (
      <div className={styles.wrap}>
        <form className={styles.card} onSubmit={onLogin} noValidate>
          <h1>ERP 관리 콘솔</h1>
          <p className={styles.sub}>
            {otpStep
              ? "인증 앱에 표시된 6자리 코드를 입력하세요."
              : "거래처·품목·재고·회계를 한 화면에서 관리하세요."}
          </p>

          {!otpStep ? (
            <>
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
            </>
          ) : (
            <Field
              label="2단계 인증 코드"
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="6자리"
              autoFocus
              error={loginForm.formState.errors.otp?.message}
              {...loginForm.register("otp", {
                required: "인증 코드를 입력하세요.",
              })}
            />
          )}

          <Button block type="submit" disabled={loginForm.formState.isSubmitting}>
            {loginForm.formState.isSubmitting
              ? "확인 중…"
              : otpStep
                ? "인증"
                : "로그인"}
          </Button>

          {otpStep ? (
            <div className={`${styles.hint} ${styles.hintTight}`}>
              <button
                type="button"
                className={styles.linkBtn}
                onClick={() => {
                  setOtpStep(false);
                  loginForm.setValue("otp", "");
                  loginForm.clearErrors();
                }}
              >
                다른 계정으로 로그인
              </button>
            </div>
          ) : (
            <>
              <div className={styles.hint}>
                <Link href="/forgot-password" className={styles.linkBtn}>
                  비밀번호를 잊으셨나요?
                </Link>
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
            </>
          )}
        </form>
      </div>
    );
  }

  return (
    <div className={styles.wrap}>
      <form className={styles.card} onSubmit={onRegister} noValidate>
        <h1>회원가입</h1>
        <p className={styles.sub}>
          가입 후 <b>관리자 승인</b>을 받아야 로그인할 수 있습니다.
        </p>

        <Field
          label="아이디 *"
          autoComplete="username"
          placeholder="2자 이상"
          error={
            registerForm.formState.errors.username?.message ??
            (usernameTaken ? "이미 사용 중인 아이디입니다." : undefined)
          }
          {...registerForm.register("username", {
            required: "아이디를 입력하세요.",
            minLength: { value: 2, message: "아이디는 2자 이상이어야 합니다." },
          })}
        />
        {usernameTaken === false && registerUsername.trim().length >= 2 && (
          <div className={styles.okInline} role="status">
            사용할 수 있는 아이디입니다.
          </div>
        )}

        <Field
          label="비밀번호 *"
          type="password"
          autoComplete="new-password"
          placeholder={policy?.text ?? `${minLength}자 이상, 영문+숫자`}
          error={registerForm.formState.errors.password?.message}
          {...registerForm.register("password", {
            required: "비밀번호를 입력하세요.",
            validate: (value) =>
              validatePasswordClient(value, minLength, registerUsername) ?? true,
          })}
        />
        <PasswordStrength password={registerPassword} minLength={minLength} />
        {policy && <div className={styles.policy}>{policy.text}</div>}

        <Field
          label="이름"
          error={registerForm.formState.errors.full_name?.message}
          {...registerForm.register("full_name")}
        />
        <Field
          label="이메일"
          type="email"
          placeholder="비밀번호 재설정에 사용됩니다"
          error={registerForm.formState.errors.email?.message}
          {...registerForm.register("email")}
        />
        <Field
          label="부서"
          error={registerForm.formState.errors.department?.message}
          {...registerForm.register("department")}
        />
        <Field
          label="가입 사유"
          placeholder="관리자가 승인 여부를 판단합니다"
          error={registerForm.formState.errors.signup_reason?.message}
          {...registerForm.register("signup_reason")}
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
    </div>
  );
}
