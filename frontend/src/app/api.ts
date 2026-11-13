import { clearReadPrefetch, readResponse } from "./routeReadCache";

/** The CSRF cookie's value, echoed back as the X-CSRF-Token header. */
export function csrfToken(): string {
  const item = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith("jawnix_csrf="));
  return decodeURIComponent(item?.split("=", 2)[1] ?? "");
}

/** `detail` is a plain string on most failures, but blocked lifecycle actions
 *  answer with a structured object the caller needs intact (the counts that
 *  explain the block). Both shapes are carried through. */
interface APIErrorDetail {
  message?: string;
  dependencies?: Record<string, number>;
}

interface APIErrorBody {
  detail?: string | APIErrorDetail;
}

export interface ApiInit extends RequestInit {
  /** The X-CSRF-Token header rides on every write; pass `false` only for the
   *  session bootstrap that runs before the cookie exists. */
  csrf?: boolean;
}

/**
 * The one request helper: same-origin credentials, a JSON content type for
 * JSON bodies, the CSRF token on writes, and the read prefetch consumed for
 * GETs. Returns the raw Response so callers keep their own status handling.
 */
export async function apiFetch(
  path: string,
  { csrf = true, ...init }: ApiInit = {},
): Promise<Response> {
  const method = init.method ?? "GET";
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (csrf && !["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-CSRF-Token", csrfToken());
  }
  const options = {
    ...init,
    method,
    headers,
    credentials: "same-origin" as const,
  };
  if (method !== "GET") clearReadPrefetch();
  const response = method === "GET" ? await readResponse(path, options) : await fetch(path, options);
  if (method !== "GET") clearReadPrefetch();
  return response;
}

/**
 * `apiFetch` for the common case: the body is JSON, and a refusal throws an
 * Error carrying the response's `status` and `detail`.
 */
export async function api<T>(path: string, init: ApiInit = {}): Promise<T> {
  const response = await apiFetch(path, init);
  const body = (await response.json().catch(() => ({}))) as APIErrorBody;
  if (!response.ok) {
    const detail = body.detail;
    const message = typeof detail === "string" ? detail : detail?.message;
    const error = new Error(message || "The request could not be completed.");
    Object.assign(error, { status: response.status, detail });
    throw error;
  }
  return body as T;
}
