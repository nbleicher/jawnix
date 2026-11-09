import { useLayoutEffect, useRef } from "react";

import "./tabs.css";
import { ApplicationLink } from "./ApplicationLink";
import { cx } from "./cx";

export interface TabItem {
  key: string;
  label: string;
  /** The tab's own address. Tabs live in the URL, so a view is shareable
   *  and the back button returns to the tab the operator came from. */
  href: string;
}

export interface TabsProps {
  /** Names the navigation landmark, e.g. "Customer sections". */
  label: string;
  items: TabItem[];
  /** The key of the tab whose content is showing. */
  current: string;
  className?: string;
}

/**
 * Sub-sections of one record (V7): the operator's Customer page and the
 * Customer's Account share this one tab style.
 *
 * Each tab is a link to its own address rather than an ARIA tab that swaps
 * content in place, so it behaves like the navigation it is: it opens in a
 * new tab, survives a reload, and reads as "current page" to assistive
 * technology. Tabs stay on one line and scroll sideways on phones (V10).
 */
export function Tabs({ label, items, current, className }: TabsProps) {
  const listRef = useRef<HTMLUListElement>(null);

  // On a narrow screen the current tab may sit past the edge; bring it into
  // view without scrolling the page itself.
  useLayoutEffect(() => {
    const list = listRef.current;
    const active = list?.querySelector<HTMLElement>('[aria-current="page"]');
    if (!list || !active) return;
    const start = active.offsetLeft - list.offsetLeft;
    const end = start + active.offsetWidth;
    if (start < list.scrollLeft || end > list.scrollLeft + list.clientWidth) {
      list.scrollLeft = Math.max(0, start - 16);
    }
  }, [current]);

  return (
    <nav className={cx("jx-tabs", className)} aria-label={label}>
      <ul className="jx-tabs__list" ref={listRef}>
        {items.map((item) => (
          <li key={item.key} className="jx-tabs__item">
            <ApplicationLink
              href={item.href}
              className="jx-tabs__tab"
              aria-current={item.key === current ? "page" : undefined}
            >
              {item.label}
            </ApplicationLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
