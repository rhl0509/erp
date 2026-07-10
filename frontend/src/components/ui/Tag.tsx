import type { ReactNode } from "react";

import styles from "./Tag.module.css";

/** 레거시 .tag / .tag.cust / .tag.supp / .tag.both / .tag.gray 이식 */
export type TagVariant = "default" | "cust" | "supp" | "both" | "gray";

export default function Tag({
  variant = "default",
  children,
}: {
  variant?: TagVariant;
  children: ReactNode;
}) {
  const cls =
    variant === "default" ? styles.tag : `${styles.tag} ${styles[variant]}`;
  return <span className={cls}>{children}</span>;
}
