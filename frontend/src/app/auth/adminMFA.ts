import { redirect } from "react-router";

import { api, apiFetch } from "../api";

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
  backupCodes?: string[];
  session?: { accessToken: string; refreshToken: string; expiresIn: number };
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
      const response = await apiFetch("/api/auth/admin-mfa");
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
  const { getProviderSession } = await import("./providerSession");
  return (await getProviderSession()).access_token;
}

export function formatCode(value: string): string {
  return value.replace(/\D/g, "").slice(0, 6);
}
