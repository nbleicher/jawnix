import type { ReactNode } from "react";

import "./detail.css";
import { cx } from "./cx";

export interface DetailItem {
  /** The field name. Becomes the <dt>, so keep it short and stable. */
  term: string;
  /** The value. A node rather than a string so a status badge or a link can
   *  sit in a detail row without a second list primitive. */
  description: ReactNode;
}

export interface DetailListProps {
  /** Names the list for assistive technology. Required: a detail list without
   *  a name is indistinguishable from the next one on a record screen. */
  label: string;
  items: DetailItem[];
  className?: string;
}

/**
 * Term/description pairs for a record's attributes.
 *
 * A real `<dl>` rather than a two-column grid of `<span>`s, so the pairing
 * between a field and its value survives a screen reader. Single column on
 * mobile; the grid gives the term its own column once there is room, which is
 * how the denser desktop reading in #57 falls out without a second component.
 */
export function DetailList({ label, items, className }: DetailListProps) {
  return (
    <dl className={cx("jx-detail", className)} aria-label={label}>
      {items.map((item) => (
        <div className="jx-detail__row" key={item.term}>
          <dt className="jx-detail__term">{item.term}</dt>
          <dd className="jx-detail__description">{item.description}</dd>
        </div>
      ))}
    </dl>
  );
}
