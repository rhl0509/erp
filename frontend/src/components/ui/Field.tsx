"use client";

import { useId, type InputHTMLAttributes, type Ref } from "react";

import styles from "./Field.module.css";

type FieldProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  /** 서버 422 fields / RHF 검증 메시지 — .fld-err 로 표시 */
  error?: string;
  /** react-hook-form register 의 ref (React 19: ref 를 일반 prop 으로 전달) */
  ref?: Ref<HTMLInputElement>;
};

/** 레거시 .field(label + input) + .fld-err 패리티. RHF: <Field {...register("x")} /> */
export default function Field({ label, error, id, ...rest }: FieldProps) {
  const autoId = useId();
  const inputId = id ?? autoId;
  return (
    <div className={styles.field}>
      <label htmlFor={inputId}>{label}</label>
      <input id={inputId} aria-invalid={!!error || undefined} {...rest} />
      {error !== undefined && <div className={styles.fldErr}>{error}</div>}
    </div>
  );
}
