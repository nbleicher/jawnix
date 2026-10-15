import { randomUUID } from "node:crypto";
import { betterAuth } from "better-auth";
import { createAuthMiddleware } from "better-auth/api";
import { bearer, twoFactor } from "better-auth/plugins";
import pg from "pg";
import { attachDatabasePool } from "@vercel/functions";
import { AsyncLocalStorage } from "node:async_hooks";
import { signupProfile } from "./signup.js";

// Trusted bridge calls can return an onboarding link to the operator without
// sending email. Per-call storage keeps simultaneous resets isolated.
export const accessLinkContext = new AsyncLocalStorage<{ url?: string }>();

export function createAuth(
  database: pg.Pool,
  options: {
    baseURL: string;
    secret: string;
    sendReset: (email: string, url: string) => Promise<void>;
    sendVerification?: (email: string, url: string) => Promise<void>;
  },
) {
  return betterAuth({
    appName: "Jawnix",
    baseURL: options.baseURL,
    basePath: "/auth",
    secret: options.secret,
    database,
    trustedOrigins: [new URL(options.baseURL).origin],
    disabledPaths: [
      "/two-factor/enable",
      "/two-factor/disable",
      "/two-factor/get-totp-uri",
      "/two-factor/generate-backup-codes",
    ],
    advanced: { database: { generateId: () => randomUUID() } },
    emailAndPassword: {
      enabled: true,
      requireEmailVerification: true,
      minPasswordLength: 8,
      revokeSessionsOnPasswordReset: true,
      sendResetPassword: async ({ user, url }) => {
        const capture = accessLinkContext.getStore();
        if (capture) capture.url = url;
        else await options.sendReset(user.email, url);
      },
    },
    emailVerification: {
      sendOnSignUp: true,
      autoSignInAfterVerification: true,
      expiresIn: 3600,
      sendVerificationEmail: async ({ user, url }) => {
        if (!options.sendVerification) throw new Error("Auth email delivery is unavailable.");
        await options.sendVerification(user.email, url);
      },
    },
    user: {
      additionalFields: {
        jawnixRole: { type: "string", defaultValue: "customer", input: false },
        signupProfile: { type: "json", required: false, input: true },
      },
    },
    session: {
      expiresIn: 60 * 60 * 12,
      cookieCache: { enabled: false },
      additionalFields: {
        mfaVerifiedAt: { type: "date", required: false, input: false },
        mfaMethod: { type: "string", defaultValue: "", input: false },
      },
    },
    rateLimit: { enabled: true, storage: "database", window: 60, max: 60,
      customRules: { "/sign-up/email": { window: 60, max: 5 }, "/send-verification-email": { window: 60, max: 3 } } },
    plugins: [bearer(), twoFactor({ issuer: "Jawnix" })],
    hooks: {
      before: createAuthMiddleware(async (ctx) => {
        if (ctx.path !== "/sign-up/email") return;
        const details = signupProfile(ctx.body?.signupProfile);
        return { context: { body: { ...ctx.body, signupProfile: details, name: `${details.first_name} ${details.last_name}` } } };
      }),
      after: createAuthMiddleware(async (ctx) => {
        if (
          ![
            "/two-factor/verify-totp",
            "/two-factor/verify-backup-code",
          ].includes(ctx.path)
        )
          return;
        const result = ctx.context.returned;
        if (
          !result ||
          typeof result !== "object" ||
          !("token" in result) ||
          typeof result.token !== "string"
        )
          return;
        // Only the provider's successful verification response can raise
        // assurance. Enrollment, password login, and client fields cannot.
        const token = ctx.context.newSession?.session.token || result.token;
        await ctx.context.internalAdapter.updateSession(token, {
          mfaVerifiedAt: new Date(),
          mfaMethod: ctx.path.endsWith("verify-totp") ? "totp" : "recovery",
        });
      }),
    },
  });
}

export type JawnixAuth = ReturnType<typeof createAuth>;

let runtime: { auth: JawnixAuth; database: pg.Pool } | undefined;
export function getRuntime() {
  if (runtime) return runtime;
  const databaseURL = process.env.JAWNIX_AUTH_DATABASE_URL;
  const secret = process.env.BETTER_AUTH_SECRET;
  const baseURL = process.env.BETTER_AUTH_URL;
  if (!databaseURL || !secret || !baseURL)
    throw new Error("Auth configuration is incomplete.");
  const database = new pg.Pool({
    connectionString: databaseURL,
    max: 5,
    idleTimeoutMillis: 5000,
    connectionTimeoutMillis: 10000,
  });
  if (process.env.VERCEL) attachDatabasePool(database);
  async function sendMail(email: string, subject: string, text: string) {
    if (!process.env.RESEND_API_KEY) throw new Error("Auth email delivery is unavailable.");
    const response = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: { Authorization: `Bearer ${process.env.RESEND_API_KEY}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        from: process.env.JAWNIX_BATCH_FROM_EMAIL || "Noah Bleicher (Jawnix) <noah@jawnix.com>",
        to: [email], subject, text,
      }),
    });
    if (!response.ok) throw new Error("Auth email delivery failed.");
  }
  const auth = createAuth(database, {
    secret,
    baseURL,
    sendReset: (email, url) => sendMail(email, "Set your Jawnix password", `Set your Jawnix password using this link:\n\n${url}\n\nThis link expires in one hour. If you did not request it, you can ignore this email.\n\n— Noah\nnoah@jawnix.com`),
    sendVerification: (email, url) => sendMail(email, "Verify your Jawnix email", `Verify your email to finish creating your Jawnix account:\n\n${url}\n\nThis link expires in one hour. If you did not request it, you can ignore this email.\n\n— Noah\nnoah@jawnix.com`),
  });
  runtime = { auth, database };
  return runtime;
}
