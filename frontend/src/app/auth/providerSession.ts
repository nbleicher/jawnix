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

function remember(session: ProviderIdentitySession) {
  sessionStorage.setItem(ACCESS_TOKEN_KEY, session.access_token);
  sessionStorage.setItem(REFRESH_TOKEN_KEY, session.refresh_token);
}

export async function getProviderSession(): Promise<ProviderIdentitySession> {
  const config = window.JAWNIX_CONFIG ?? {};
  if (config.supabaseUrl && config.supabaseAnonKey) {
    const { data, error } = await client().auth.getSession();
    if (!error && data.session) {
      remember(data.session);
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

export async function signInWithPassword(
  email: string,
  password: string,
): Promise<ProviderIdentitySession> {
  const { data, error } = await client().auth.signInWithPassword({
    email,
    password,
  });
  if (error || !data.session) {
    throw new Error("Sign in was not accepted.");
  }
  remember(data.session);
  return data.session;
}

export async function updateProviderPassword(password: string): Promise<ProviderIdentitySession> {
  const current = await getProviderSession();
  const { error } = await client().auth.updateUser({ password });
  if (error) {
    throw new Error("The invitation could not be accepted.");
  }
  return current;
}

export async function signOutProvider(): Promise<void> {
  try {
    const config = window.JAWNIX_CONFIG ?? {};
    if (config.supabaseUrl && config.supabaseAnonKey) {
      await client().auth.signOut();
    }
  } finally {
    sessionStorage.removeItem(ACCESS_TOKEN_KEY);
    sessionStorage.removeItem(REFRESH_TOKEN_KEY);
  }
}

export async function storeProviderSession(update: ProviderSessionUpdate): Promise<void> {
  remember({
    access_token: update.accessToken,
    refresh_token: update.refreshToken,
  });
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
