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

import ConfirmModal from "./ConfirmModal";

/**
 * 확인 모달을 window.confirm 처럼 쓰는 훅.
 *
 *   const confirm = useConfirm();
 *   if (!(await confirm({ title: "삭제", message: "정말 삭제할까요?", danger: true }))) return;
 *
 * 브라우저 기본 confirm/alert 은 쓰지 않는다 — 앱 내 모달(딤 배경 + 카드 + 취소/확정)로
 * 통일해 디자인·문구·접근성을 우리가 통제한다. 호출부는 Promise<boolean> 만 보면 되므로
 * 화면마다 모달 상태를 따로 들고 있을 필요가 없다.
 */
export type ConfirmOptions = {
  title: string;
  message: ReactNode;
  confirmText?: string;
  cancelText?: string;
  /** 되돌리기 어려운 동작(삭제·취소·비활성화)이면 true — 확정 버튼이 danger 스타일 */
  danger?: boolean;
};

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

export function useConfirm(): ConfirmFn {
  const fn = useContext(ConfirmContext);
  if (!fn) throw new Error("useConfirm 은 <ConfirmProvider> 안에서만 사용할 수 있습니다.");
  return fn;
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [options, setOptions] = useState<ConfirmOptions | null>(null);
  const resolveRef = useRef<((ok: boolean) => void) | null>(null);

  const confirm = useCallback<ConfirmFn>((opts) => {
    setOptions(opts);
    return new Promise<boolean>((resolve) => {
      resolveRef.current = resolve;
    });
  }, []);

  const settle = useCallback((ok: boolean) => {
    setOptions(null);
    resolveRef.current?.(ok);
    resolveRef.current = null;
  }, []);

  const value = useMemo(() => confirm, [confirm]);

  return (
    <ConfirmContext.Provider value={value}>
      {children}
      {options && (
        <ConfirmModal
          title={options.title}
          message={options.message}
          confirmText={options.confirmText}
          cancelText={options.cancelText}
          danger={options.danger}
          onConfirm={() => settle(true)}
          onClose={() => settle(false)}
        />
      )}
    </ConfirmContext.Provider>
  );
}
