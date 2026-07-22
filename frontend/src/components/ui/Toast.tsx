"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import styles from "./Toast.module.css";

/** 레거시 toast(msg, isErr) 패리티 — 3.2초 후 자동 제거 */
type ToastFn = (message: string, isError?: boolean) => void;

type ToastItem = {
  id: number;
  message: string;
  isError: boolean;
  /** 퇴장 트랜지션 재생 중 — 끝나면 목록에서 제거된다 */
  leaving?: boolean;
};

const ToastContext = createContext<ToastFn | null>(null);

export function useToast(): ToastFn {
  const fn = useContext(ToastContext);
  if (!fn) throw new Error("useToast 는 <ToastProvider> 안에서만 사용할 수 있습니다.");
  return fn;
}

const TOAST_DURATION_MS = 3200;
/** Toast.module.css 의 --dur-menu(200ms)와 맞춘다 — 퇴장 트랜지션이 끝난 뒤 제거 */
const TOAST_EXIT_MS = 200;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(0);
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());

  // 언마운트 시 남은 타이머 정리(제거된 노드에 setState 하지 않게)
  useEffect(() => {
    const pending = timers.current;
    return () => {
      pending.forEach(clearTimeout);
      pending.clear();
    };
  }, []);

  const schedule = useCallback((fn: () => void, ms: number) => {
    const handle = setTimeout(() => {
      timers.current.delete(handle);
      fn();
    }, ms);
    timers.current.add(handle);
  }, []);

  const toast = useCallback<ToastFn>(
    (message, isError = false) => {
      const id = nextId.current++;
      setToasts((prev) => [...prev, { id, message, isError }]);
      schedule(() => {
        // 하드컷 대신 퇴장 트랜지션을 태운 뒤 제거한다
        setToasts((prev) =>
          prev.map((t) => (t.id === id ? { ...t, leaving: true } : t)),
        );
        schedule(() => {
          setToasts((prev) => prev.filter((t) => t.id !== id));
        }, TOAST_EXIT_MS);
      }, TOAST_DURATION_MS);
    },
    [schedule],
  );

  const value = useMemo(() => toast, [toast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* 성공은 polite, 오류는 assertive — 한 컨테이너에 섞으면 오류도 정중하게
          읽혀 놓친다. 자동 소멸 전에 스크린리더가 읽도록 분리한다. */}
      <div className={styles.toasts}>
        <div role="status" aria-live="polite" className={styles.stack}>
          {toasts
            .filter((t) => !t.isError)
            .map((t) => (
              <div
                key={t.id}
                className={styles.toast}
                data-leaving={t.leaving ? "true" : undefined}
              >
                {t.message}
              </div>
            ))}
        </div>
        <div role="alert" aria-live="assertive" className={styles.stack}>
          {toasts
            .filter((t) => t.isError)
            .map((t) => (
              <div
                key={t.id}
                className={`${styles.toast} ${styles.error}`}
                data-leaving={t.leaving ? "true" : undefined}
              >
                {t.message}
              </div>
            ))}
        </div>
      </div>
    </ToastContext.Provider>
  );
}
