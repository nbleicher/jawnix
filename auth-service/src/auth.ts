import { randomUUID } from "node:crypto";
import { betterAuth } from "better-auth";
import { createAuthMiddleware } from "better-auth/api";
import { bearer, twoFactor } from "better-auth/plugins";
import pg from "pg";
import { attachDatabasePool } from "@vercel/functions";

export function createAuth(
  database: pg.Pool,
  options: {
    baseURL: string;
    secret: string;
    sendReset: (email: string, url: string) => Promise<void>;
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
      disableSignUp: true,
      minPasswordLength: 8,
      revokeSessionsOnPasswordReset: true,
      sendResetPassword: async ({ user, url }) =>
        options.sendReset(user.email, url),
    },
    user: {
      additionalFields: {
        jawnixRole: { type: "string", defaultValue: "customer", input: false },
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
    rateLimit: { enabled: true, storage: "database", window: 60, max: 60 },
    plugins: [bearer(), twoFactor({ issuer: "Jawnix" })],
    hooks: {
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
  const auth = createAuth(database, {
    secret,
    baseURL,
    sendReset: async (email, url) => {
      if (!process.env.RESEND_API_KEY)
        throw new Error("Auth email delivery is unavailable.");
      const response = await fetch("https://api.resend.com/emails", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${process.env.RESEND_API_KEY}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          from:
            process.env.JAWNIX_BATCH_FROM_EMAIL ||
            "Noah Bleicher (Jawnix) <noah@jawnix.com>",
          to: [email],
          subject: "Set your Jawnix password",
          text: `Set your Jawnix password using this link:\n\n${url}\n\nThis link expires in one hour. If you did not request it, you can ignore this email.\n\n— Noah\nnoah@jawnix.com`,
        }),
      });
      if (!response.ok) throw new Error("Auth email delivery failed.");
    },
  });
  runtime = { auth, database };
  return runtime;
}
