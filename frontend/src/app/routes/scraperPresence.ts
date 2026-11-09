import { useCallback, useEffect, useState } from "react";

/** How long without a click or key before live regions stop refreshing. */
export const PRESENCE_IDLE_MS = 15 * 60 * 1000;

/**
 * Whether an operator is actually watching the privileged Scraper workspace.
 *
 * The Scraper grant lasts the whole sign-in, so this is not a security
 * boundary: it only stops polling while the tab is hidden or nobody has
 * touched it for a while, so an unattended tab puts no load on the Scraper.
 */
export function useOperatorPresence(idleMs: number = PRESENCE_IDLE_MS): {
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
