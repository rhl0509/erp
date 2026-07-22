"use client";

import {
  useId,
  type InputHTMLAttributes,
  type Ref,
  type SelectHTMLAttributes,
} from "react";

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
  const errId = `${inputId}-err`;
  return (
    <div className={styles.field}>
      <label htmlFor={inputId}>{label}</label>
      <input
        id={inputId}
        aria-invalid={!!error || undefined}
        // 연결이 없으면 스크린리더가 "유효하지 않음"만 읽고 이유는 읽지 않는다
        aria-describedby={error ? errId : undefined}
        {...rest}
      />
      {/* 항상 렌더한다 — 조건부 렌더면 검증 순간 아래 필드가 전부 밀린다 */}
      <div id={errId} className={styles.fldErr}>
        {error ?? ""}
      </div>
    </div>
  );
}

type SelectFieldProps = SelectHTMLAttributes<HTMLSelectElement> & {
  label: string;
  error?: string;
  /** [value, label] 쌍 — 레거시 openForm 의 select opts 관례 */
  options: readonly (readonly [string, string])[];
  ref?: Ref<HTMLSelectElement>;
};

/** Field 의 select 변형(모달 폼의 유형/과세구분/상태 등). RHF: {...register("x")} */
export function SelectField({
  label,
  error,
  options,
  id,
  ...rest
}: SelectFieldProps) {
  const autoId = useId();
  const selectId = id ?? autoId;
  const errId = `${selectId}-err`;
  return (
    <div className={styles.field}>
      <label htmlFor={selectId}>{label}</label>
      <select
        id={selectId}
        aria-invalid={!!error || undefined}
        aria-describedby={error ? errId : undefined}
        {...rest}
      >
        {options.map(([value, text]) => (
          <option key={value} value={value}>
            {text}
          </option>
        ))}
      </select>
      <div id={errId} className={styles.fldErr}>
        {error ?? ""}
      </div>
    </div>
  );
}
