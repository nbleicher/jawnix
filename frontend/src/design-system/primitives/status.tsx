import type { HTMLAttributes, ReactNode } from "react";

import "./status.css";
import { cx } from "./cx";

export type StatusTone = "neutral" | "info" | "success" | "warning" | "danger";

export interface StatusBadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: StatusTone;
  /** Always required: status is conveyed by the label, with colour only
   *  reinforcing it (WCAG 1.4.1 — never colour alone). */
  children: ReactNode;
}

export function StatusBadge({ tone = "neutral", className, children, ...rest }: StatusBadgeProps) {
  return (
    <span className={cx("jx-status", `jx-status--${tone}`, className)} {...rest}>
      <span className="jx-status__dot" aria-hidden="true" />
      {children}
    </span>
  );
}
