import { createClient } from "@supabase/supabase-js";

declare global {
  interface Window {
    JAWNIX_CONFIG?: {
      supabaseUrl?: string;
      supabaseAnonKey?: string;
    };
  }
}

export interface ProviderSessionUpdate {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
}

interface ProviderIdentitySession {
  access_token: string;
  refresh_token: string;
}

const ACCESS_TOKEN_KEY = "jawnix_provider_access_token";
const REFRESH_TOKEN_KEY = "jawnix_provider_refresh_token";

function client() {
  const config = window.JAWNIX_CONFIG ?? {};
  if (!config.supabaseUrl || !config.supabaseAnonKey) {
    throw new Error("Sign-in configuration is unavailable. Return to sign in and try again.");
  }
  return createClient(config.supabaseUrl, config.supabaseAnonKey);
}

export async function getProviderSession(): Promise<ProviderIdentitySession> {
  const config = window.JAWNIX_CONFIG ?? {};
  if (config.supabaseUrl && config.supabaseAnonKey) {
    const { data, error } = await client().auth.getSession();
    if (!error && data.session) {
      sessionStorage.setItem(ACCESS_TOKEN_KEY, data.session.access_token);
      sessionStorage.setItem(REFRESH_TOKEN_KEY, data.session.refresh_token);
      return data.session;
    }
  }
  const accessToken = sessionStorage.getItem(ACCESS_TOKEN_KEY);
  const refreshToken = sessionStorage.getItem(REFRESH_TOKEN_KEY);
  if (!accessToken || !refreshToken) {
    throw new Error("Your identity session expired. Sign in again to continue.");
  }
  return { access_token: accessToken, refresh_token: refreshToken };
}

export async function storeProviderSession(update: ProviderSessionUpdate): Promise<void> {
  sessionStorage.setItem(ACCESS_TOKEN_KEY, update.accessToken);
  sessionStorage.setItem(REFRESH_TOKEN_KEY, update.refreshToken);
  const config = window.JAWNIX_CONFIG ?? {};
  if (!config.supabaseUrl || !config.supabaseAnonKey) return;
  const { data, error } = await client().auth.setSession({
    access_token: update.accessToken,
    refresh_token: update.refreshToken,
  });
  if (error || !data.session) {
    throw new Error("Your verified session could not be saved. Sign in again.");
  }
}
