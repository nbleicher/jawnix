import type { IncomingMessage, ServerResponse } from "node:http";
import { toNodeHandler } from "better-auth/node";
import { getRuntime, type JawnixAuth } from "./auth.js";
import { bridge, bridgeAuthorized } from "./bridge.js";

export function createHandler(auth: JawnixAuth, bridgeSecret: string) {
  return async function dispatch(
    request: IncomingMessage,
    response: ServerResponse,
  ) {
    response.setHeader("Cache-Control", "no-store");
    if (request.url?.split("?")[0] !== "/bridge")
      return toNodeHandler(auth)(request, response);
    if (
      request.method !== "POST" ||
      !bridgeAuthorized(request.headers.authorization || null, bridgeSecret)
    ) {
      response.writeHead(401);
      response.end();
      return;
    }
    try {
      const chunks: Buffer[] = [];
      let size = 0;
      for await (const chunk of request) {
        size += chunk.length;
        if (size > 20000) {
          response.writeHead(413);
          response.end();
          return;
        }
        chunks.push(chunk);
      }
      const result = await bridge(
        auth,
        JSON.parse(Buffer.concat(chunks).toString("utf8")),
      );
      response.setHeader("Content-Type", "application/json");
      response.end(JSON.stringify(result));
    } catch (error) {
      const code =
        error && typeof error === "object" && "body" in error
          ? (error.body as { code?: string })?.code
          : "";
      const invalidCode = [
        "INVALID_CODE",
        "INVALID_BACKUP_CODE",
        "INVALID_TOTP_CODE",
      ].includes(code || "");
      const expiredSession = code === "SESSION_EXPIRED";
      response.writeHead(expiredSession ? 401 : invalidCode ? 422 : 400, {
        "Content-Type": "application/json",
      });
      response.end(
        JSON.stringify({
          error: "Identity request failed.",
          invalidCode,
          expiredSession,
        }),
      );
    }
  };
}

export async function handler(
  request: IncomingMessage,
  response: ServerResponse,
) {
  return createHandler(
    getRuntime().auth,
    process.env.JAWNIX_AUTH_BRIDGE_SECRET || "",
  )(request, response);
}
