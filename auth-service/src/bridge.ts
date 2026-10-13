import { randomUUID, timingSafeEqual } from "node:crypto";
import { APIError } from "better-auth/api";
import QRCode from "qrcode";
import type { JawnixAuth } from "./auth.js";
import { accessLinkContext } from "./auth.js";

export function bridgeAuthorized(header: string | null, secret: string) {
  const supplied = Buffer.from(header || "");
  const expected = Buffer.from(`Bearer ${secret}`);
  return (
    secret.length >= 32 &&
    supplied.length === expected.length &&
    timingSafeEqual(supplied, expected)
  );
}

type Payload = {
  action: string;
  accessToken?: string;
  userId?: string;
  sessionId?: string;
  factorId?: string;
  code?: string;
  method?: string;
  password?: string;
  email?: string;
  name?: string;
  role?: string;
  page?: number;
};

export async function bridge(auth: JawnixAuth, body: Payload) {
  const context = await auth.$context;
  const adapter = context.internalAdapter;
  const headers = new Headers({
    Authorization: `Bearer ${body.accessToken || ""}`,
  });
  const identity = () => auth.api.getSession({ headers });
  const factorFor = async (userId: string) =>
    context.adapter.findOne<{ id: string; userId: string; verified: boolean }>({
      model: "twoFactor",
      where: [{ field: "userId", value: userId }],
    });
  const userJSON = (user: {
    id: string;
    email: string;
    name: string;
    jawnixRole?: string;
  }) => ({
    id: user.id,
    email: user.email,
    user_metadata: { name: user.name },
    app_metadata: { jawnix_role: user.jawnixRole || "customer" },
  });
  const requireSession = async () => {
    const session = await identity();
    if (!session)
      throw new APIError("UNAUTHORIZED", {
        code: "SESSION_EXPIRED",
        message: "Identity session expired.",
      });
    return session;
  };
  const requireAdmin = async () => {
    const session = await requireSession();
    if (!["admin", "manager"].includes(session.user.jawnixRole)) throw new APIError("FORBIDDEN");
    return session;
  };
  const sessionJSON = async (token: string) => {
    const verified = await auth.api.getSession({
      headers: new Headers({ Authorization: `Bearer ${token}` }),
    });
    if (!verified) throw new APIError("UNAUTHORIZED");
    return {
      access_token: verified.session.token,
      refresh_token: verified.session.token,
      expires_in: Math.max(
        0,
        Math.floor((verified.session.expiresAt.getTime() - Date.now()) / 1000),
      ),
    };
  };

  switch (body.action) {
    case "identity": {
      const session = await requireSession();
      const factor = await factorFor(session.user.id);
      const assured =
        !!session.session.mfaVerifiedAt &&
        !!session.user.twoFactorEnabled &&
        !!factor?.verified;
      return {
        user: userJSON(session.user),
        claims: {
          sub: session.user.id,
          sessionId: session.session.id,
          aal: assured ? "aal2" : "aal1",
          amr: assured
            ? [
                {
                  method: session.session.mfaMethod,
                  timestamp: Math.floor(
                    session.session.mfaVerifiedAt!.getTime() / 1000,
                  ),
                },
              ]
            : [],
        },
      };
    }
    case "factors": {
      if (body.sessionId) {
        const session = await context.adapter.findOne<{
          userId: string;
          expiresAt: Date;
        }>({
          model: "session",
          where: [{ field: "id", value: body.sessionId }],
        });
        if (
          !session ||
          session.userId !== body.userId ||
          session.expiresAt <= new Date()
        )
          throw new APIError("UNAUTHORIZED", {
            code: "SESSION_EXPIRED",
            message: "Identity session expired.",
          });
      }
      const factor = await factorFor(body.userId!);
      return factor
        ? [
            {
              id: factor.id,
              status: factor.verified ? "verified" : "unverified",
              factor_type: "totp",
              friendly_name: "Jawnix authenticator",
            },
          ]
        : [];
    }
    case "admin-user": {
      const user = await adapter.findUserById(body.userId!);
      if (!user) throw new APIError("NOT_FOUND");
      return userJSON(user);
    }
    case "users": {
      const users = await context.adapter.findMany<{
        id: string;
        email: string;
        name: string;
        jawnixRole?: string;
      }>({
        model: "user",
        limit: 1000,
        offset: ((body.page || 1) - 1) * 1000,
        sortBy: { field: "id", direction: "asc" },
      });
      return { users: users.map(userJSON) };
    }
    case "provision-user": {
      const existing = await adapter.findUserByEmail(body.email!);
      if (existing && body.userId && existing.user.id !== body.userId)
        throw new APIError("CONFLICT", {
          message: "Identity mapping conflict.",
        });
      if (existing) return userJSON(existing.user);
      const user = await adapter.createUser(
        {
          id: body.userId || randomUUID(),
          email: body.email!.toLowerCase(),
          name: body.name || body.email!,
          emailVerified: true,
          jawnixRole: body.role === "admin" ? "admin" : "customer",
        },
        { method: "email-password" },
      );
      return userJSON(user);
    }
    case "provision-manager": {
      const existing = await context.adapter.findOne<{ id: string; email: string; name: string; jawnixRole?: string }>({
        model: "user", where: [{ field: "email", value: body.email!.toLowerCase() }],
      });
      if (existing) {
        if (existing.jawnixRole !== "manager")
          throw new APIError("CONFLICT", { message: "This email already has another Jawnix role." });
        return userJSON(existing);
      }
      const user = await adapter.createUser({
        id: body.userId || randomUUID(), email: body.email!.toLowerCase(),
        name: body.name || body.email!, emailVerified: true, jawnixRole: "manager",
      }, { method: "email-password" });
      return userJSON(user);
    }
    case "manager-access-link": {
      const user = await context.adapter.findOne<{ id: string; email: string; jawnixRole?: string }>({
        model: "user", where: [{ field: "id", value: body.userId! }],
      });
      if (!user || user.jawnixRole !== "manager") throw new APIError("FORBIDDEN");
      const capture: { url?: string } = {};
      await accessLinkContext.run(capture, () => auth.api.requestPasswordReset({
        body: { email: user.email, redirectTo: `${new URL(auth.options.baseURL as string).origin}/app/accept-invitation` },
      }));
      if (!capture.url) throw new APIError("INTERNAL_SERVER_ERROR");
      return { url: capture.url, expiresIn: 3600 };
    }
    case "reset-user": {
      await auth.api.requestPasswordReset({
        body: {
          email: body.email!,
          redirectTo: `${new URL(auth.options.baseURL as string).origin}/app/accept-invitation`,
        },
      });
      return { ok: true };
    }
    case "replace":
    case "enroll": {
      const session = await requireAdmin();
      let enrollmentHeaders = headers;
      let replacementToken: string | undefined;
      if (body.action === "replace") {
        const existing = await factorFor(session.user.id);
        if (!session.session.mfaVerifiedAt || !existing?.verified)
          throw new APIError("FORBIDDEN");
        await auth.api.verifyPassword({
          headers,
          body: { password: body.password! },
        });
        await context.adapter.delete({
          model: "twoFactor",
          where: [{ field: "id", value: existing.id }],
        });
        await adapter.updateUser(session.user.id, { twoFactorEnabled: false });
        await adapter.deleteUserSessions(session.user.id);
        const newSession = await adapter.createSession(session.user.id);
        if (!newSession) throw new APIError("INTERNAL_SERVER_ERROR");
        replacementToken = newSession.token;
        enrollmentHeaders = new Headers({
          Authorization: `Bearer ${replacementToken}`,
        });
      }
      const value = await auth.api.enableTwoFactor({
        headers: enrollmentHeaders,
        body: { password: body.password!, issuer: "Jawnix" },
      });
      const factor = await factorFor(session.user.id);
      if (!factor || value.method !== "totp")
        throw new APIError("INTERNAL_SERVER_ERROR");
      return {
        id: factor.id,
        friendly_name: "Jawnix authenticator",
        totp: {
          qr_code: await QRCode.toString(value.totpURI, { type: "svg" }),
          secret: new URL(value.totpURI).searchParams.get("secret"),
          uri: value.totpURI,
        },
        backup_codes: value.backupCodes,
        session: replacementToken
          ? await sessionJSON(replacementToken)
          : undefined,
      };
    }
    case "challenge": {
      const session = await requireAdmin();
      const factor = await factorFor(session.user.id);
      if (factor?.id !== body.factorId) throw new APIError("BAD_REQUEST");
      return { id: randomUUID() };
    }
    case "verify": {
      const session = await requireAdmin();
      const factor = await factorFor(session.user.id);
      if (factor?.id !== body.factorId) throw new APIError("BAD_REQUEST");
      const response =
        body.method === "recovery"
          ? await auth.api.verifyBackupCode({
              headers,
              body: { code: body.code!, trustDevice: false },
              asResponse: true,
            })
          : await auth.api.verifyTOTP({
              headers,
              body: { code: body.code!, trustDevice: false },
              asResponse: true,
            });
      const value = (await response.json()) as {
        token?: string;
        code?: string;
      };
      if (!response.ok)
        throw new APIError("UNAUTHORIZED", {
          code: value.code,
          message: "Verification refused.",
        });
      const token = response.headers.get("set-auth-token") || value.token;
      if (!token) throw new APIError("UNAUTHORIZED");
      return sessionJSON(token);
    }
    case "delete-factor": {
      const factor = await factorFor(body.userId!);
      if (!factor || factor.id !== body.factorId) return { ok: true };
      await context.adapter.delete({
        model: "twoFactor",
        where: [{ field: "id", value: factor.id }],
      });
      if (factor.verified) {
        await adapter.updateUser(body.userId!, { twoFactorEnabled: false });
        await adapter.deleteUserSessions(body.userId!);
      }
      return { ok: true };
    }
    case "logout": {
      const session = await requireSession();
      await adapter.deleteUserSessions(session.user.id);
      return { ok: true };
    }
    default:
      throw new APIError("BAD_REQUEST");
  }
}
