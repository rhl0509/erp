"use client";

import { useState, type ReactNode } from "react";

import Button from "./Button";
import Modal from "./Modal";

import styles from "./ConfirmModal.module.css";

/**
 * 확인 모달 — 브라우저 기본 window.confirm 대신 쓰는 앱 내 모달(딤 배경 + 카드 + 취소/확정).
 * 되돌리기 어려운 동작(비활성화·거절·임시비번 발급 등) 앞에 세운다.
 *
 * onConfirm 이 Promise 를 돌려주면 진행 중 버튼이 잠긴다. 성공/실패 토스트는 호출부 책임.
 */
export default function ConfirmModal({
  title,
  message,
  confirmText = "확인",
  cancelText = "취소",
  danger = false,
  onConfirm,
  onClose,
}: {
  title: string;
  message: ReactNode;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
  onConfirm: () => void | Promise<void>;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);

  const confirm = async () => {
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {cancelText}
          </Button>
          <Button
            variant={danger ? "danger" : "primary"}
            onClick={() => void confirm()}
            disabled={busy}
          >
            {busy ? "처리 중…" : confirmText}
          </Button>
        </>
      }
    >
      <div className={styles.message}>{message}</div>
    </Modal>
  );
}
