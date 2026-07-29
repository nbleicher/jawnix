import { useCallback, useEffect, useState } from "react";

/**
 * Whether an operator is actually watching the privileged Scraper workspace.
 *
 * Polling is not operator activity. It stops while the tab is hidden or the
 * operator is idle so background refresh cannot silently extend the
 * fifteen-minute grant forever.
 */
export function useOperatorPresence(idleMs: number): {
  watching: boolean;
  resume: () => void;
} {
  const [idle, setIdle] = useState(false);
  const [hidden, setHidden] = useState(
    typeof document === "undefined" ? false : document.hidden,
  );

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    function mark() {
      setIdle(false);
      clearTimeout(timer);
      timer = setTimeout(() => setIdle(true), idleMs);
    }
    function visibility() {
      setHidden(document.hidden);
      if (!document.hidden) mark();
    }
    mark();
    const events = ["pointerdown", "keydown"] as const;
    events.forEach((event) => window.addEventListener(event, mark));
    document.addEventListener("visibilitychange", visibility);
    return () => {
      clearTimeout(timer);
      events.forEach((event) => window.removeEventListener(event, mark));
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [idleMs]);

  return {
    watching: !idle && !hidden,
    resume: useCallback(() => setIdle(false), []),
  };
}
