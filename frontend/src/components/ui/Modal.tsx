"use client";

import {
  useEffect,
  useRef,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import styles from "./Modal.module.css";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

type ModalProps = {
  title: ReactNode;
  /** ESC / 오버레이 클릭 / footer 닫기 버튼에서 호출 */
  onClose: () => void;
  children: ReactNode;
  /** 하단 액션 슬롯(레거시 .modal .foot) — 취소/저장 버튼 등 */
  footer?: ReactNode;
  /** 넓은 표 콘텐츠용 — 레거시 거래처 내역 모달의 max-width 760px 확장 패리티 */
  wide?: boolean;
};

/**
 * 레거시 index.html .overlay/.modal 이식 — 오버레이 + 포커스 트랩 + ESC 닫기.
 * 열림/닫힘은 조건부 렌더로 제어한다: {open && <Modal …>…</Modal>}
 * (열릴 때마다 새로 마운트되어 내부 폼 상태가 초기화되는 관례)
 */
export default function Modal({
  title,
  onClose,
  children,
  footer,
  wide = false,
}: ModalProps) {
  const modalRef = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    // 열릴 때: 배경 스크롤 잠금 + 첫 폼 요소 포커스(레거시 openForm 패리티).
    // 닫힐 때: 원래 포커스 복원.
    restoreRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const modal = modalRef.current;
    const first =
      modal?.querySelector<HTMLElement>("input, select, textarea") ??
      modal?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
    (first ?? modal)?.focus();
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prevOverflow;
      restoreRef.current?.focus();
    };
  }, []);

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
      return;
    }
    if (e.key !== "Tab") return;
    // 포커스 트랩 — Tab 이 모달 밖으로 나가지 않게 양 끝에서 순환시킨다.
    const modal = modalRef.current;
    if (!modal) return;
    const focusables = Array.from(
      modal.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
    );
    if (focusables.length === 0) {
      e.preventDefault();
      return;
    }
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  const handleOverlayMouseDown = (e: MouseEvent<HTMLDivElement>) => {
    // 오버레이 자체를 눌렀을 때만 닫는다(레거시 e.target.id==="overlay" 패리티)
    if (e.target === e.currentTarget) onClose();
  };

  return createPortal(
    <div
      className={styles.overlay}
      onMouseDown={handleOverlayMouseDown}
      onKeyDown={handleKeyDown}
    >
      <div
        ref={modalRef}
        className={wide ? `${styles.modal} ${styles.wide}` : styles.modal}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
      >
        <h3 className={styles.title}>{title}</h3>
        <div className={styles.body}>{children}</div>
        {footer !== undefined && <div className={styles.foot}>{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

/** 레거시 .form-grid — 모달 폼 2열 그리드 */
export function FormGrid({ children }: { children: ReactNode }) {
  return <div className={styles.formGrid}>{children}</div>;
}

/** 레거시 .form-grid .full — 주소 등 한 줄 전체 폭 필드 래퍼 */
export function FormFull({ children }: { children: ReactNode }) {
  return <div className={styles.full}>{children}</div>;
}
