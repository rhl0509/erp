"use client";

import type { ButtonHTMLAttributes } from "react";

import styles from "./Button.module.css";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  /** 레거시 .btn(기본) / .btn.ghost / .btn.danger */
  variant?: "primary" | "ghost" | "danger";
  /** 레거시 .btn.sm */
  size?: "md" | "sm";
  /** 레거시 .btn.block */
  block?: boolean;
};

export default function Button({
  variant = "primary",
  size = "md",
  block = false,
  className,
  type = "button",
  ...rest
}: ButtonProps) {
  const cls = [
    styles.btn,
    variant !== "primary" ? styles[variant] : "",
    size === "sm" ? styles.sm : "",
    block ? styles.block : "",
    className ?? "",
  ]
    .filter(Boolean)
    .join(" ");
  return <button type={type} className={cls} {...rest} />;
}
