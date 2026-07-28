import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";

import "./Button.css";
import { cx } from "./cx";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  variant?: ButtonVariant;
  /** Stretches to the container — the default posture for mobile forms. */
  fullWidth?: boolean;
  /** Marks an in-flight action. The button stays focusable and keeps its
   *  accessible name so screen readers are not stranded mid-submit. */
  busy?: boolean;
  busyLabel?: string;
  ref?: Ref<HTMLButtonElement>;
  children: ReactNode;
}

export function Button({
  variant = "secondary",
  fullWidth = false,
  busy = false,
  busyLabel = "Working…",
  type = "button",
  className,
  disabled,
  children,
  ref,
  ...rest
}: ButtonProps) {
  return (
    <button
      ref={ref}
      type={type}
      className={cx("jx-button", `jx-button--${variant}`, fullWidth ? "jx-button--full" : null, className)}
      // `aria-disabled` rather than `disabled` while busy: a disabled element
      // drops out of the tab order and silently discards focus mid-action.
      aria-disabled={busy || disabled ? true : undefined}
      disabled={disabled && !busy}
      data-busy={busy ? "true" : undefined}
      {...rest}
    >
      {busy ? (
        <>
          <span className="jx-button__spinner" aria-hidden="true" />
          <span>{busyLabel}</span>
        </>
      ) : (
        children
      )}
    </button>
  );
}
