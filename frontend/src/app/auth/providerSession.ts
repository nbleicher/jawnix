import { createAuthClient } from "better-auth/react";
import { twoFactorClient } from "better-auth/client/plugins";

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
let authClient: ReturnType<typeof client> | undefined;
function client() {
  return createAuthClient({
    baseURL: `${window.location.origin}/auth`,
    basePath: "/auth",
    plugins: [twoFactorClient()],
  });
}
function auth() {
  authClient ??= client();
  return authClient;
}
function remember(token: string): ProviderIdentitySession {
  sessionStorage.setItem(ACCESS_TOKEN_KEY, token);
  sessionStorage.setItem(REFRESH_TOKEN_KEY, token);
  return { access_token: token, refresh_token: token };
}
export class MFARequired extends Error {}
export class EmailVerificationRequired extends Error {}
export async function getProviderSession(fresh = false): Promise<ProviderIdentitySession> {
  if (fresh) {
    sessionStorage.removeItem(ACCESS_TOKEN_KEY);
    sessionStorage.removeItem(REFRESH_TOKEN_KEY);
  }
  // Enrollment can rotate the provider session through the server bridge.
  const stored = sessionStorage.getItem(ACCESS_TOKEN_KEY);
  if (stored) return { access_token: stored, refresh_token: stored };
  const { data, error } = await auth().getSession();
  if (error || !data?.session.token)
    throw new Error(
      "Your identity session expired. Sign in again to continue.",
    );
  return remember(data.session.token);
}
export async function signInWithPassword(
  email: string,
  password: string,
): Promise<ProviderIdentitySession> {
  sessionStorage.removeItem(ACCESS_TOKEN_KEY);
  sessionStorage.removeItem(REFRESH_TOKEN_KEY);
  const { data, error } = await auth().signIn.email({ email, password });
  if (error?.code === "EMAIL_NOT_VERIFIED") throw new EmailVerificationRequired();
  if (error) throw new Error("Sign in was not accepted.");
  if (data && "twoFactorRedirect" in data && data.twoFactorRedirect)
    throw new MFARequired();
  return getProviderSession();
}
export interface SignupDetails {
  first_name: string;
  last_name: string;
  phone: string;
  licensed_states: string[];
}
const signupCallback = () => `${window.location.origin}/app/complete-signup`;
export async function signUpWithPassword(email: string, password: string, details: SignupDetails): Promise<void> {
  const response = await fetch("/auth/sign-up/email", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, name: `${details.first_name} ${details.last_name}`, signupProfile: details, callbackURL: signupCallback() }),
  });
  if (!response.ok) throw new Error("We could not create your account. Check your details, or sign in if you already registered.");
}
export async function resendVerificationEmail(email: string): Promise<void> {
  const { error } = await auth().sendVerificationEmail({ email, callbackURL: signupCallback() });
  if (error) throw new Error("Verification email is temporarily unavailable. Try again shortly.");
}
export async function verifyProviderSignIn(
  code: string,
  recovery = false,
): Promise<ProviderIdentitySession> {
  const { error } = recovery
    ? await auth().twoFactor.verifyBackupCode({ code, trustDevice: false })
    : await auth().twoFactor.verifyTotp({ code, trustDevice: false });
  if (error) throw new Error("That verification code was not accepted.");
  return getProviderSession();
}
export async function requestProviderPasswordReset(
  email: string,
): Promise<void> {
  const { error } = await auth().requestPasswordReset({
    email,
    redirectTo: `${window.location.origin}/app/accept-invitation`,
  });
  if (error) throw new Error("Password recovery is temporarily unavailable.");
}
export async function updateProviderPassword(password: string): Promise<void> {
  const token = new URLSearchParams(window.location.search).get("token");
  if (!token) throw new Error("The password link is invalid or expired.");
  const { error } = await auth().resetPassword({
    newPassword: password,
    token,
  });
  if (error) throw new Error("The password link is invalid or expired.");
}
export async function signOutProvider(): Promise<void> {
  const token = sessionStorage.getItem(ACCESS_TOKEN_KEY);
  try {
    await auth().signOut({
      fetchOptions: token
        ? { headers: { Authorization: `Bearer ${token}` } }
        : {},
    });
  } finally {
    sessionStorage.removeItem(ACCESS_TOKEN_KEY);
    sessionStorage.removeItem(REFRESH_TOKEN_KEY);
  }
}
export async function storeProviderSession(
  update: ProviderSessionUpdate,
): Promise<void> {
  remember(update.accessToken);
}
