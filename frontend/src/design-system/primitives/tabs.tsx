import { useLayoutEffect, useRef } from "react";
import { Link } from "react-router";

import "./tabs.css";

export interface TabLink {
  /** The tab's own address: tabs live in the URL (V7). */
  to: string;
  label: string;
  current: boolean;
}

export interface TabsProps {
  /** Names the set for assistive technology, e.g. "Account". */
  label: string;
  items: TabLink[];
}

/**
 * The one sub-section tab style (V7): a row of links under the page header,
 * the current one underlined in ink.
 *
 * Each tab is a page with its own address, so these are navigation links with
 * `aria-current`, not an ARIA tablist. The row stays on one line and scrolls
 * sideways on phones (V10), bringing the current tab into view.
 */
export function Tabs({ label, items }: TabsProps) {
  const row = useRef<HTMLElement>(null);

  useLayoutEffect(() => {
    const element = row.current;
    const current = element?.querySelector<HTMLElement>('[aria-current="page"]');
    if (!element || !current) return;
    const start = current.offsetLeft - element.offsetLeft;
    const end = start + current.offsetWidth;
    if (start < element.scrollLeft || end > element.scrollLeft + element.clientWidth) {
      element.scrollLeft = Math.max(0, end - element.clientWidth);
    }
  }, [items]);

  return (
    <nav className="jx-tabs" aria-label={label} ref={row}>
      {items.map((item) => (
        <Link
          key={item.to}
          to={item.to}
          className="jx-tabs__tab"
          aria-current={item.current ? "page" : undefined}
        >
          {item.label}
        </Link>
      ))}
    </nav>
  );
}
