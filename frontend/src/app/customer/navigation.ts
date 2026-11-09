import type { NavigationDestination } from "../shell/Navigation";

/** New request: the form that creates a batch (CP2). */
export const NEW_REQUEST_HREF = "/app/new-request";

/** Batches: every batch the Customer has requested (CP3). */
export const BATCHES_HREF = "/app/batches";

/**
 * Customer navigation (Q10): the Customer's jobs, not the backend's tables.
 * A batch page (/batches/1042) belongs to Batches.
 */
export function customerDestinations(): NavigationDestination[] {
  return [
    { to: "/home", label: "Home", icon: "◆" },
    { to: "/new-request", label: "New request", icon: "+" },
    { to: "/batches", label: "Batches", icon: "▤" },
    { to: "/account", label: "Account", icon: "◑" },
  ];
}
