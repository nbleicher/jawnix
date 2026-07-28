import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router";

import { TITLE_SUFFIX } from "./useDocumentTitle";

/**
 * Announces client-side route changes.
 *
 * A single-page navigation produces no page-load event, so screen readers stay
 * silent when the whole screen changes. This mirrors the new document title
 * into a polite live region to restore that feedback.
 *
 * The first render is deliberately silent: the initial page load is already
 * announced by the user agent, and re-announcing would double it.
 */
export function RouteAnnouncer() {
  const location = useLocation();
  const [message, setMessage] = useState("");
  const isFirstRender = useRef(true);

  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }

    // Read the title after the route's own effects have run, so the announcer
    // reports the destination rather than the screen being left.
    const frame = requestAnimationFrame(() => {
      const title = document.title.replace(new RegExp(`\\s*·\\s*${TITLE_SUFFIX}$`), "");
      setMessage(title || TITLE_SUFFIX);
    });

    return () => cancelAnimationFrame(frame);
  }, [location.pathname, location.search]);

  return (
    <div
      data-testid="jx-route-announcer"
      className="jx-visually-hidden"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      {message}
    </div>
  );
}
