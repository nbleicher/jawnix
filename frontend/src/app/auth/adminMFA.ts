import { redirect } from "react-router";

import { getProviderSession } from "./providerSession";

export interface FactorUseLocation {
  ipAddress: string;
  userAgent: string;
}

export interface AdminMFAFactor {
  id: string;
  name: string;
  status: string;
  type: string;
  createdAt: string | null;
  lastUsedAt: string | null;
  lastUsedFrom: FactorUseLocation | null;
}

export interface AdminMFAStatus {
  assurance: "aal1" | "aal2";
  enforced: boolean;
  stage: string;
  factors: AdminMFAFactor[];
  throttled: boolean;
  lockedUntil: string | null;
  next: string;
}

export interface EnrollmentSecret {
  factorId: string;
  slot: "primary" | "backup" | "replacement";
  qrCode: string;
  manualKey: string;
  uri: string;
}

interface APIErrorBody {
  detail?: string;
}

function csrf(): string {
  const item = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith("jawnix_csrf="));
  return decodeURIComponent(item?.split("=", 2)[1] ?? "");
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = init.method ?? "GET";
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-CSRF-Token", csrf());
  }
  const response = await fetch(path, {
    ...init,
    method,
    headers,
    credentials: "same-origin",
  });
  const body = (await response.json().catch(() => ({}))) as APIErrorBody;
  if (!response.ok) {
    const error = new Error(body.detail || "The request could not be completed.");
    Object.assign(error, { status: response.status });
    throw error;
  }
  return body as T;
}

export async function adminMFAStatusLoader(): Promise<AdminMFAStatus> {
  try {
    return await api<AdminMFAStatus>("/api/auth/admin-mfa");
  } catch (error) {
    if ((error as { status?: number }).status === 401) {
      window.location.assign(`/login.html?next=${encodeURIComponent(window.location.pathname)}`);
      return new Promise(() => undefined);
    }
    throw error;
  }
}

export async function adminAccessLoader() {
  try {
    await api<{ ok: true }>("/api/auth/admin-mfa/access");
    return null;
  } catch (error) {
    const status = (error as { status?: number }).status;
    if (status === 401) {
      const response = await fetch("/api/auth/admin-mfa", { credentials: "same-origin" });
      if (response.ok) {
        const state = (await response.json()) as AdminMFAStatus;
        throw redirect(state.next.replace(/^\/app/, ""));
      }
      window.location.assign(`/login.html?next=${encodeURIComponent(window.location.pathname)}`);
      return new Promise(() => undefined);
    }
    throw error;
  }
}

export async function providerAccessToken(): Promise<string> {
  return (await getProviderSession()).access_token;
}

export function formatCode(value: string): string {
  return value.replace(/\D/g, "").slice(0, 6);
}
