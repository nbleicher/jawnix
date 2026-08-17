import { useEffect } from "react";

export const TITLE_SUFFIX = "JAWNIX";

/**
 * Sets the document title for a routed screen.
 *
 * The title is also what {@link RouteAnnouncer} reads out after a client-side
 * navigation, so every screen calling this hook gets its route change announced
 * without any extra wiring.
 */
export function useDocumentTitle(title: string) {
  useEffect(() => {
    document.title = title ? `${title} · ${TITLE_SUFFIX}` : TITLE_SUFFIX;
  }, [title]);
}
