import { useCallback, useState } from "react";
import type { MouseEvent } from "react";
import { useSearchParams } from "react-router";

/**
 * The row open in a side panel, kept in the URL so the view is shareable.
 *
 * `param` names the query parameter ("decision", "batch"). Opening a row from
 * the list moves focus into the panel; a panel that arrives open from a shared
 * link leaves focus alone. Closing returns focus to the row's trigger, found
 * by `data-panel-trigger`.
 */
export function useSelectedRow(param: string) {
  const [params, setParams] = useSearchParams();
  const selected = params.get(param);
  const [openedHere, setOpenedHere] = useState(false);

  const select = useCallback(
    (key: string | null) => {
      setOpenedHere(key !== null);
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          if (key === null) next.delete(param);
          else next.set(param, key);
          return next;
        },
        { replace: true, preventScrollReset: true },
      );
    },
    [param, setParams],
  );

  /** A first-column link: a plain click opens the panel, while a modified
   *  click (new tab, new window) still follows the link to the full page. */
  const openFromLink = useCallback(
    (key: string) => (event: MouseEvent<HTMLAnchorElement>) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return;
      }
      event.preventDefault();
      select(key);
    },
    [select],
  );

  const returnFocus = useCallback(
    (key: string) => () =>
      document.querySelector<HTMLElement>(
        `[data-panel-trigger="${key.replace(/["\\]/g, "\\$&")}"]`,
      ),
    [],
  );

  return {
    selected,
    select,
    close: () => select(null),
    openFromLink,
    returnFocus,
    autoFocus: openedHere,
  };
}
