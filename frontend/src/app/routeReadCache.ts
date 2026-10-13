const PATHS = new Set([
  "/api/admin/operations-overview", "/api/admin/fulfillment",
  "/api/admin/pool-breakdown", "/api/admin/acquisition",
  "/api/admin/customers/directory", "/api/admin/activity",
  "/api/me/batch-requests",
]);
const pending = new Map<string, { expires: number; response: Promise<Response> }>();

/** A short, single-use prefetch. Normal reads always remain fresh. */
export function prefetchRead(path: string) {
  if (!PATHS.has(path)) return;
  const saved = pending.get(path);
  if (saved && saved.expires > Date.now()) return;
  const response = fetch(path, { credentials: "same-origin" });
  pending.set(path, { expires: Date.now() + 3_000, response });
  void response.catch(() => { if (pending.get(path)?.response === response) pending.delete(path); });
}

export function clearReadPrefetch() { pending.clear(); }

export async function readResponse(path: string, init: RequestInit = {}): Promise<Response> {
  const saved = pending.get(path);
  pending.delete(path);
  if (saved && saved.expires > Date.now() && !init.signal) {
    try { return (await saved.response).clone(); }
    catch { /* Retry a failed speculative read through the normal path. */ }
  }
  return fetch(path, { ...init, credentials: "same-origin" });
}
