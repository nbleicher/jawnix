import type {
  AnchorHTMLAttributes,
  ButtonHTMLAttributes,
  ReactNode,
  Ref,
} from "react";

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
      aria-busy={busy || undefined}
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

export interface ButtonLinkProps
  extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "children" | "href"> {
  href: string;
  variant?: Exclude<ButtonVariant, "danger">;
  fullWidth?: boolean;
  children: ReactNode;
}

/** A commitment that happens to be an anchor (a download, an external
 *  document). It wears the button slab — the payoff action draws the ink —
 *  while staying a real link so open-in-new-tab and copy-link still work. */
export function ButtonLink({
  variant = "primary",
  fullWidth = false,
  className,
  children,
  ...rest
}: ButtonLinkProps) {
  return (
    <a
      className={cx(
        "jx-button",
        `jx-button--${variant}`,
        fullWidth ? "jx-button--full" : null,
        className,
      )}
      {...rest}
    >
      {children}
    </a>
  );
}

export interface ActionLinkProps
  extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "children" | "href"> {
  href: string;
  variant?: Exclude<ButtonVariant, "danger">;
  fullWidth?: boolean;
  children: ReactNode;
}

/** A navigation action rendered as an accent text link — the one place the
 *  accent appears on an action. It remains an anchor so open-in-new-tab,
 *  copy-link, and browser history behavior are preserved. The brand admits no
 *  variants on a text link, so `variant`/`fullWidth` are accepted for call-site
 *  compatibility but do not change the rendering. */
export function ActionLink({
  variant = "secondary",
  fullWidth = false,
  className,
  children,
  ...rest
}: ActionLinkProps) {
  void variant;
  void fullWidth;
  return (
    <a className={cx("jx-action-link", className)} {...rest}>
      {children}
    </a>
  );
}
