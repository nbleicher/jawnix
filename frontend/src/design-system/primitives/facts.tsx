import type { ReactNode } from "react";

import "./facts.css";
import { cx } from "./cx";

export interface Fact {
  term: string;
  description: ReactNode;
}

export interface FactListProps {
  /** Names the list for assistive technology. */
  label: string;
  items: Fact[];
  className?: string;
}

/**
 * Compact label/value pairs: the term on the left, the value right-aligned
 * with tabular figures. Used for sections in the main column and inside
 * boxed panels; `DetailList` stays the stacked record form.
 */
export function FactList({ label, items, className }: FactListProps) {
  return (
    <dl className={cx("jx-facts", className)} aria-label={label}>
      {items.map((item) => (
        <div className="jx-facts__row" key={item.term}>
          <dt className="jx-facts__term">{item.term}</dt>
          <dd className="jx-facts__value">{item.description}</dd>
        </div>
      ))}
    </dl>
  );
}
