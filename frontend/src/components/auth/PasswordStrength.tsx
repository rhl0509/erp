"use client";

import { passwordStrength, STRENGTH_LABELS } from "@/lib/auth/password";

import styles from "./PasswordStrength.module.css";

/**
 * 비밀번호 강도 표시(0~4). 서버와 같은 공식으로 계산하며(lib/auth/password),
 * 판정이 아니라 안내다 — 통과 여부는 제출 시 서버가 정한다.
 */
export default function PasswordStrength({
  password,
  minLength,
}: {
  password: string;
  minLength: number;
}) {
  const score = passwordStrength(password, minLength);
  const label = STRENGTH_LABELS[score];

  return (
    <div className={styles.wrap}>
      <div
        className={styles.bars}
        role="meter"
        aria-valuemin={0}
        aria-valuemax={4}
        aria-valuenow={score}
        aria-label={`비밀번호 강도: ${label}`}
      >
        {[0, 1, 2, 3].map((i) => (
          <span
            key={i}
            className={`${styles.bar} ${i < score ? styles[`lv${score}`] : ""}`}
          />
        ))}
      </div>
      <span className={styles.label}>{password ? label : ""}</span>
    </div>
  );
}
