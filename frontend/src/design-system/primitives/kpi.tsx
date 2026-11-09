import type { ReactNode } from "react";

import "./kpi.css";
import { cx } from "./cx";

export interface KpiRowProps {
  /** Names the row for assistive technology, like a detail list's label. */
  label: string;
  /** Tiles per row once there is room (V1): 4 on Today, Insights, and Home;
   *  6 on a record page; 2 inside a side panel. Narrow screens drop to 2. */
  columns?: 2 | 4 | 6;
  className?: string;
  children: ReactNode;
}

/**
 * The numbers row a page with totals starts with.
 *
 * A real `<dl>`: each tile is a term (its label) with one or two descriptions
 * (the figure and its supporting line), so a screen reader keeps every figure
 * paired with what it counts. Tiles share hairline borders, so the row reads
 * as one ruled block rather than a grid of cards.
 */
export function KpiRow({ label, columns = 4, className, children }: KpiRowProps) {
  return (
    <dl className={cx("jx-kpis", `jx-kpis--${columns}`, className)} aria-label={label}>
      {children}
    </dl>
  );
}

export interface KpiProps {
  label: string;
  /** Already formatted: full numbers, money with cents (V5). */
  value: ReactNode;
  /** One supporting line: an as-of time, a breakdown, a comparison. */
  meta?: ReactNode;
  /** A red figure. Reserved for problems that need action (V3, V4). */
  alert?: boolean;
}

export function Kpi({ label, value, meta, alert = false }: KpiProps) {
  return (
    <div className={cx("jx-kpi", alert && "jx-kpi--alert")}>
      <dt className="jx-kpi__label">{label}</dt>
      <dd className="jx-kpi__value">{value}</dd>
      {meta ? <dd className="jx-kpi__meta">{meta}</dd> : null}
    </div>
  );
}
