import { redirect } from "react-router";
import type { LoaderFunctionArgs } from "react-router";

import {
  getProviderSession,
  signOutProvider,
} from "./providerSession";

interface SessionExchangeResponse {
  ok: true;
  role: string;
  assurance: string;
  next: string;
}

export interface SignInLoaderData {
  next: string;
}

export interface InvitationLoaderData {
  canAccept: boolean;
}

// Invitation/recovery links are hard navigations. Capture the fragment while
// the application module initializes, before the provider client consumes and
// removes its one-time credentials from the address bar.
const AUTH_REDIRECT_FRAGMENT =
  typeof window === "undefined" ? "" : window.location.hash;

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

export function routerPath(value: string): string {
  if (!value.startsWith("/app/") || value.startsWith("//")) {
    return "/overview";
  }
  return value.replace(/^\/app/, "");
}

export function signInLoader({ request }: LoaderFunctionArgs): SignInLoaderData {
  const url = new URL(request.url);
  return { next: safeCustomerPath(url.searchParams.get("next")) };
}

export async function invitationLoader(): Promise<InvitationLoaderData> {
  const fragment = new URLSearchParams(
    AUTH_REDIRECT_FRAGMENT.replace(/^#/, ""),
  );
  const flow = fragment.get("type");
  const providerRedirect =
    (flow === "invite" || flow === "recovery")
    && Boolean(fragment.get("access_token"))
    && Boolean(fragment.get("refresh_token"));
  if (!providerRedirect) return { canAccept: false };

  try {
    await getProviderSession();
    return { canAccept: true };
  } catch {
    return { canAccept: false };
  }
}

export async function exchangeJawnixSession(
  accessToken: string,
  requestedNext: string,
): Promise<SessionExchangeResponse> {
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

export async function customerAccessLoader({ request }: LoaderFunctionArgs) {
  const response = await fetch("/api/me/profile", {
    credentials: "same-origin",
  });
  if (response.status === 401 || response.status === 403) {
    const requested = safeCustomerPath(new URL(request.url).pathname);
    throw redirect(`/sign-in?next=${encodeURIComponent(requested)}`);
  }
  if (!response.ok) {
    throw new Response("Customer access is temporarily unavailable.", {
      status: response.status,
    });
  }
  return response.json();
}

export async function signOutCustomer(): Promise<void> {
  try {
    await fetch("/api/auth/logout", {
      method: "POST",
      headers: { "X-CSRF-Token": csrf() },
      credentials: "same-origin",
    });
  } finally {
    await signOutProvider();
  }
}
