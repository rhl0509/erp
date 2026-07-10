"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import styles from "./Toast.module.css";

/** 레거시 toast(msg, isErr) 패리티 — 3.2초 후 자동 제거 */
type ToastFn = (message: string, isError?: boolean) => void;

type ToastItem = { id: number; message: string; isError: boolean };

const ToastContext = createContext<ToastFn | null>(null);

export function useToast(): ToastFn {
  const fn = useContext(ToastContext);
  if (!fn) throw new Error("useToast 는 <ToastProvider> 안에서만 사용할 수 있습니다.");
  return fn;
}

const TOAST_DURATION_MS = 3200;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(0);

  const toast = useCallback<ToastFn>((message, isError = false) => {
    const id = nextId.current++;
    setToasts((prev) => [...prev, { id, message, isError }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, TOAST_DURATION_MS);
  }, []);

  const value = useMemo(() => toast, [toast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className={styles.toasts} role="status" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={t.isError ? `${styles.toast} ${styles.error}` : styles.toast}
          >
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
