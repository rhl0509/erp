import type { ReactNode } from "react";

import styles from "./StatCard.module.css";

/** 레거시 .stat 카드 — label(.k) / value(.v) / foot(.foot) */
export default function StatCard({
  label,
  value,
  foot,
  smallValue = false,
}: {
  label: ReactNode;
  value: ReactNode;
  foot?: ReactNode;
  /** 역할명 등 텍스트 값일 때(레거시 내 역할 카드의 font-size 축소 패리티) */
  smallValue?: boolean;
}) {
  return (
    <div className={styles.stat}>
      <div className={styles.k}>{label}</div>
      <div className={smallValue ? `${styles.v} ${styles.vSmallText}` : styles.v}>
        {value}
      </div>
      {foot !== undefined && <div className={styles.foot}>{foot}</div>}
    </div>
  );
}
