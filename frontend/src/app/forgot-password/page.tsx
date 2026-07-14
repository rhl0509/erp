"use client";

import { useState } from "react";
import Link from "next/link";
import { useForm } from "react-hook-form";

import Button from "@/components/ui/Button";
import Field from "@/components/ui/Field";
import { useToast } from "@/components/ui/Toast";
import { client, unwrap } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";

import styles from "@/components/auth/auth.module.css";

type Values = { identifier: string };

/**
 * 비밀번호 찾기 — POST /api/auth/forgot-password.
 *
 * 서버는 계정 존재 여부를 응답으로 알려주지 않는다(계정 열거 방지). 응답의 delivery 로
 * 두 경로가 갈린다:
 *   email → 등록된 이메일로 재설정 링크 발송(SMTP 설정된 환경)
 *   admin → SMTP 미설정 — 관리자에게 임시 비밀번호 발급을 요청해야 한다
 */
export default function ForgotPasswordPage() {
  const toast = useToast();
  const [result, setResult] = useState<{ detail: string; delivery: string } | null>(null);
  const { register, handleSubmit, formState } = useForm<Values>({
    defaultValues: { identifier: "" },
  });

  const onSubmit = handleSubmit(async (values) => {
    try {
      const res = unwrap(
        await client.POST("/api/auth/forgot-password", {
          body: { identifier: values.identifier.trim() },
        }),
      );
      setResult({ detail: res.detail, delivery: res.delivery });
    } catch (err) {
      toast(errorMessage(err), true);
    }
  });

  return (
    <div className={styles.wrap}>
      <form className={styles.card} onSubmit={onSubmit} noValidate>
        <h1>비밀번호 찾기</h1>
        <p className={styles.sub}>가입한 아이디 또는 이메일을 입력하세요.</p>

        {result ? (
          <>
            <div className={styles.notice} role="status">
              {result.detail}
              {result.delivery === "admin" && (
                <>
                  <br />
                  관리자가 회원 화면에서 <b>임시 비밀번호</b>를 발급해 주면, 그 비밀번호로
                  로그인한 뒤 새 비밀번호를 설정할 수 있습니다.
                </>
              )}
            </div>
            <div className={styles.hint}>
              <Link href="/login" className={styles.linkBtn}>
                로그인으로 돌아가기
              </Link>
            </div>
          </>
        ) : (
          <>
            <Field
              label="아이디 또는 이메일"
              autoComplete="username"
              autoFocus
              error={formState.errors.identifier?.message}
              {...register("identifier", {
                required: "아이디 또는 이메일을 입력하세요.",
              })}
            />
            <Button block type="submit" disabled={formState.isSubmitting}>
              {formState.isSubmitting ? "요청 중…" : "재설정 요청"}
            </Button>
            <div className={`${styles.hint} ${styles.hintTight}`}>
              <Link href="/login" className={styles.linkBtn}>
                로그인으로 돌아가기
              </Link>
            </div>
          </>
        )}
      </form>
    </div>
  );
}
