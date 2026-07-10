import type { ReactNode } from "react";

import styles from "./Panel.module.css";

/** 레거시 .panel — 목록/대시보드 화면 골격 */
export function Panel({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={className ? `${styles.panel} ${className}` : styles.panel}>
      {children}
    </div>
  );
}

/** 레거시 .panel-head — 제목(h3) + 우측 액션 슬롯 */
export function PanelHead({
  title,
  actions,
}: {
  title: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className={styles.panelHead}>
      <h3>{title}</h3>
      {actions}
    </div>
  );
}

/** 레거시 .toolbar — 검색/필터 줄. input[type=text]/select 스타일 포함 */
export function Toolbar({ children }: { children: ReactNode }) {
  return <div className={styles.toolbar}>{children}</div>;
}

/** 레거시 .spacer — 툴바에서 좌측 필터와 우측 버튼을 밀어내는 빈 공간 */
export function Spacer() {
  return <div className={styles.spacer} />;
}
