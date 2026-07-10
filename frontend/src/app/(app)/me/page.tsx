"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";

import Button from "@/components/ui/Button";
import Modal, { FormFull, FormGrid } from "@/components/ui/Modal";
import Field from "@/components/ui/Field";
import { Panel } from "@/components/ui/Panel";
import { useToast } from "@/components/ui/Toast";
import { client, unwrap } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import { useAuth } from "@/lib/auth/AuthProvider";
import { applyServerFieldErrors } from "@/lib/forms";

import styles from "./page.module.css";

/**
 * 내 정보(슬라이스 8) — 레거시 #me 패리티:
 * 아이디·이름·이메일·역할 kv + 보유 권한 칩 + 비밀번호 변경(모달).
 * 데이터는 useAuth 의 me(GET /api/auth/me 캐시)를 그대로 재사용한다.
 * 백엔드 app/api/auth.py 에는 본인 프로필 필드 수정 엔드포인트가 없으므로
 * (register/login/me/password 뿐) 레거시와 동일하게 조회 + 비밀번호 변경만 제공.
 */
export default function MePage() {
  const { me } = useAuth();
  const [pwOpen, setPwOpen] = useState(false);

  // (app) 레이아웃 가드가 me 확정 전에는 children 을 렌더하지 않지만 타입 방어
  if (!me) return null;

  return (
    <section>
      <div className={styles.pageHead}>
        <div>
          <h2 className={styles.title}>내 정보</h2>
          <p className={styles.subtitle}>
            로그인한 계정의 역할과 보유 권한입니다.
          </p>
        </div>
      </div>

      <Panel className={styles.panel}>
        <dl className={styles.kv}>
          <dt>아이디</dt>
          <dd>{me.username}</dd>
          <dt>이름</dt>
          <dd>{me.full_name || "-"}</dd>
          <dt>이메일</dt>
          <dd>{me.email || "-"}</dd>
          <dt>역할</dt>
          <dd>{me.roles.map((r) => r.name).join(", ") || "-"}</dd>
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
          <Button onClick={() => setPwOpen(true)}>비밀번호 변경</Button>
        </div>
      </Panel>

      {pwOpen && <PasswordModal onClose={() => setPwOpen(false)} />}
    </section>
  );
}

// ==================== 비밀번호 변경 모달 ====================

type PasswordFormValues = {
  current_password: string;
  new_password: string;
  confirm: string;
};

const PASSWORD_FIELD_NAMES = ["current_password", "new_password"] as const;

/**
 * 레거시 openPasswordForm 패리티 — 현재 비번 + 새 비번 2회(일치 검증은 클라).
 * PUT /api/auth/me/password(204). 성공 토스트 후 닫기 — 레거시도 변경 후
 * 재로그인을 요구하지 않는다(토큰 유지). 새 비밀번호 최소 길이는 백엔드
 * PasswordChange(min_length=8) 계약을 따른다(레거시 라벨 "4자"는 계약과 불일치).
 */
function PasswordModal({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const { register, handleSubmit, setError, getValues, formState } =
    useForm<PasswordFormValues>({
      defaultValues: { current_password: "", new_password: "", confirm: "" },
    });
  const { errors, isSubmitting } = formState;

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
      toast("비밀번호를 변경했습니다.");
      onClose();
    } catch (err) {
      const applied = applyServerFieldErrors<PasswordFormValues>(
        err,
        PASSWORD_FIELD_NAMES,
        setError,
      );
      // 400(현재 비밀번호 불일치/기존과 동일) 등은 detail 토스트
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
              label="새 비밀번호 * (8자 이상)"
              type="password"
              autoComplete="new-password"
              error={errors.new_password?.message}
              {...register("new_password", {
                required: "새 비밀번호를 입력하세요.",
                minLength: {
                  value: 8,
                  message: "8자 이상 입력하세요.",
                },
              })}
            />
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
