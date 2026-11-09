import { redirect } from "react-router";
import type { LoaderFunctionArgs } from "react-router";

import { clearReadPrefetch } from "../routeReadCache";

interface SessionExchangeResponse {
  ok: true;
  role: string;
  assurance: string;
  next: string;
}

export interface SignInLoaderData {
  next: string;
  /** True after a password reset or invitation set a new password. */
  passwordUpdated: boolean;
}

export interface InvitationLoaderData {
  canAccept: boolean;
}

export interface PasswordResetLoaderData {
  canReset: boolean;
}

export type CustomerOverviewTone =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "danger";

export interface CustomerOverviewAction {
  kind:
    | "download_artifact"
    | "request_batch"
    | "submit_feedback"
    | "review_request"
    | "review_account"
    | "add_licensed_states";
  label: string;
  description: string;
  href: string;
}

export interface CustomerOverviewItem {
  id: string;
  kind:
    | "batch_ready"
    | "artifact_expiring"
    | "waiting_inventory"
    | "feedback_nudge"
    | "setup_problem";
  title: string;
  description: string;
  tone: CustomerOverviewTone;
  action: CustomerOverviewAction;
}

export interface CustomerOverviewData {
  items: CustomerOverviewItem[];
}

function csrf(): string {
  const item = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith("jawnix_csrf="));
  return decodeURIComponent(item?.split("=", 2)[1] ?? "");
}

function safeCustomerPath(value: string | null, fallback = "/app/overview"): string {
  if (
    !value
    || !value.startsWith("/app/")
    || value.startsWith("//")
    || value.startsWith("/app/admin/")
  ) {
    return fallback;
  }
  return value;
}

/** The session exchange accepts a requested path of at most this length. */
const MAX_NEXT_LENGTH = 200;

/**
 * Sign-in, returning to `url` afterwards. The query is kept, because emailed
 * links name a request or batch with it; a query too long to carry through
 * sign-in falls back to the page alone.
 */
export function signInPath(url: URL): string {
  const full = url.pathname + url.search;
  const requested = safeCustomerPath(
    full.length <= MAX_NEXT_LENGTH ? full : url.pathname,
  );
  return `/sign-in?next=${encodeURIComponent(requested)}`;
}

export function routerPath(value: string): string {
  if (!value.startsWith("/app/") || value.startsWith("//")) {
    return "/overview";
  }
  return value.replace(/^\/app/, "");
}

export function signInLoader({ request }: LoaderFunctionArgs): SignInLoaderData {
  const url = new URL(request.url);
  return {
    next: safeCustomerPath(url.searchParams.get("next")),
    passwordUpdated: url.searchParams.get("password") === "updated",
  };
}

/** A one-time provider link is usable only with a token and no error. */
function hasUsableToken(request: Request): boolean {
  const params = new URL(request.url).searchParams;
  return Boolean(params.get("token")) && !params.has("error");
}

export async function invitationLoader({ request }: LoaderFunctionArgs): Promise<InvitationLoaderData> {
  return { canAccept: hasUsableToken(request) };
}

export async function passwordResetLoader({ request }: LoaderFunctionArgs): Promise<PasswordResetLoaderData> {
  return { canReset: hasUsableToken(request) };
}

export async function exchangeJawnixSession(
  accessToken: string,
  requestedNext: string,
): Promise<SessionExchangeResponse> {
  clearReadPrefetch();
  const response = await fetch("/api/auth/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({
      access_token: accessToken,
      requested_next: safeCustomerPath(requestedNext),
    }),
  });
  if (!response.ok) {
    throw new Error("Jawnix did not establish a session.");
  }
  return response.json() as Promise<SessionExchangeResponse>;
}

export async function customerAccessLoader({
  request,
}: LoaderFunctionArgs): Promise<CustomerOverviewData> {
  const response = await fetch("/api/me/overview", {
    credentials: "same-origin",
  });
  if (response.status === 401 || response.status === 403) {
    throw redirect(signInPath(new URL(request.url)));
  }
  if (response.status === 409) throw redirect("/registration-status");
  // 404 is an authenticated principal with no Customer profile — the
  // administrator identity intentionally has none. Since cutover the site
  // root lands here, so hand them to their own shell; the admin boundary
  // still decides admission.
  if (response.status === 404) {
    const destination = await fetch("/api/auth/destination", { credentials: "same-origin" });
    if (destination.ok) {
      const value = await destination.json() as { next: string };
      throw redirect(routerPath(value.next));
    }
    throw redirect("/admin/overview");
  }
  if (!response.ok) {
    throw new Response("Customer access is temporarily unavailable.", {
      status: response.status,
    });
  }
  return response.json() as Promise<CustomerOverviewData>;
}

// Shared by the Customer and Administration shells: clears the Jawnix session
// cookie and then the provider session. The endpoint is role-agnostic.
export async function signOut(): Promise<void> {
  clearReadPrefetch();
  try {
    await fetch("/api/auth/logout", {
      method: "POST",
      headers: { "X-CSRF-Token": csrf() },
      credentials: "same-origin",
    });
  } finally {
    const { signOutProvider } = await import("./providerSession");
    await signOutProvider();
  }
}
