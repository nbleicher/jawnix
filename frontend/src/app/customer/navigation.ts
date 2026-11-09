import type { NavigationDestination } from "../shell/Navigation";

/** The id of the request history's section on /requests. */
export const BATCH_HISTORY_ID = "batch-history";

/** Batches opens the request history's section on /requests. */
export const BATCH_HISTORY_HASH = `#${BATCH_HISTORY_ID}`;

/**
 * Whether the Requests page is showing batches rather than the request form:
 * one batch (`?request=`), a later page of the history, or the history itself.
 * Until New request and Batches have pages of their own, this decides which
 * of the two is the current page.
 */
export function showsBatches(location: { search: string; hash: string }): boolean {
  const params = new URLSearchParams(location.search);
  return (
    location.hash === BATCH_HISTORY_HASH
    || params.has("request")
    || params.has("page")
  );
}

/** Customer navigation (Q10): the Customer's jobs, not the backend's tables. */
export function customerDestinations(location: {
  search: string;
  hash: string;
}): NavigationDestination[] {
  const batches = showsBatches(location);
  const onRequests = (pathname: string) => /^\/requests(?:\/|$)/.test(pathname);
  return [
    { to: "/home", label: "Home", icon: "◆" },
    {
      to: "/requests",
      label: "New request",
      icon: "+",
      match: (pathname) => onRequests(pathname) && !batches,
    },
    {
      to: `/requests${BATCH_HISTORY_HASH}`,
      label: "Batches",
      icon: "▤",
      match: (pathname) => onRequests(pathname) && batches,
    },
    { to: "/account", label: "Account", icon: "◑" },
  ];
}
