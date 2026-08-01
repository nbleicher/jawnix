import type { HTMLAttributes, ReactNode } from "react";

import "./typography.css";
import { cx } from "./cx";

export interface HeadingProps extends HTMLAttributes<HTMLHeadingElement> {
  /** Document outline level. Kept separate from `size` so visual weight never
   *  forces a wrong heading level. */
  level: 2 | 3 | 4;
  size?: "sm" | "md" | "lg";
  children?: ReactNode;
}

export function Heading({ level, size = "md", className, ...rest }: HeadingProps) {
  const Component = `h${level}` as const;
  return <Component className={cx("jx-heading", `jx-heading--${size}`, className)} {...rest} />;
}

export interface TextProps extends HTMLAttributes<HTMLElement> {
  size?: "xs" | "sm" | "md" | "lg";
  tone?: "default" | "muted" | "success" | "warning" | "danger";
  weight?: "normal" | "medium" | "semibold" | "bold";
  as?: "p" | "span" | "div";
  children?: ReactNode;
}

export function Text({
  size = "md",
  tone = "default",
  weight = "normal",
  as: Component = "p",
  className,
  ...rest
}: TextProps) {
  return (
    <Component
      className={cx(`jx-text`, `jx-text--${size}`, `jx-text--${tone}`, `jx-text--${weight}`, className)}
      {...rest}
    />
  );
}

export interface LabelTextProps extends HTMLAttributes<HTMLSpanElement> {
  children?: ReactNode;
}

/** Small uppercase label used for stat captions and table headers. */
export function LabelText({ className, ...rest }: LabelTextProps) {
  return <span className={cx("jx-label-text", className)} {...rest} />;
}

export interface MonoProps extends HTMLAttributes<HTMLElement> {
  children?: ReactNode;
}

/** Monospace run. Reserved for identifiers and dense numeric data — never for
 *  prose, which stays in the sans interface face. */
export function Mono({ className, ...rest }: MonoProps) {
  return <span className={cx("jx-mono", className)} {...rest} />;
}

export interface NumeralProps extends HTMLAttributes<HTMLElement> {
  children?: ReactNode;
}

/** Display numerals for counts, dates, and metrics. Identifiers stay in Mono;
 *  prose containing incidental digits stays in the body face. */
export function Numeral({ className, ...rest }: NumeralProps) {
  return <span className={cx("jx-numeral", className)} {...rest} />;
}

export interface VisuallyHiddenProps extends HTMLAttributes<HTMLSpanElement> {
  children?: ReactNode;
}

/** Available to assistive technology, absent from the visual layout. */
export function VisuallyHidden({ className, ...rest }: VisuallyHiddenProps) {
  return <span className={cx("jx-visually-hidden", className)} {...rest} />;
}
