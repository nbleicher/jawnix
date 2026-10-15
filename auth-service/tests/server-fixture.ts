import { createServer } from "node:http";
import { randomUUID } from "node:crypto";
import pg from "pg";
import { getMigrations } from "better-auth/db/migration";
import { createAuth, type JawnixAuth } from "../src/auth.js";
import { bridge } from "../src/bridge.js";
import { createHandler } from "../src/handler.js";

const url = process.env.JAWNIX_AUTH_TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("An isolated test database is required.");
const database = new pg.Pool({ connectionString: url });
const bridgeSecret = "jawnix-integration-bridge-secret-for-tests-only";
const password = "integration-password-for-tests-only";
const messages: { email: string; url: string }[] = [];
const verificationMessages: { email: string; url: string }[] = [];
let auth: JawnixAuth;
const server = createServer((request, response) => {
  if (request.url === "/fixture/password-links") {
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify(messages));
  } else if (request.url === "/fixture/verification-links") {
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify(verificationMessages));
  } else void createHandler(auth, bridgeSecret)(request, response);
});
await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
const address = server.address();
if (!address || typeof address === "string")
  throw new Error("Test server did not bind.");
const origin = `http://127.0.0.1:${address.port}`;
auth = createAuth(database, {
  baseURL: `${origin}/auth`,
  secret: "jawnix-provider-secret-for-isolated-tests-only",
  sendVerification: async (email, url) => { verificationMessages.push({ email, url }); },
  sendReset: async (email, url) => {
    messages.push({ email, url });
  },
});
await (await getMigrations(auth.options)).runMigrations();
// The HTTP fixture issues many deliberate sign-in failures from one test IP.
// Production rate limiting is exercised by the provider's separate tests.
(await auth.$context).rateLimit.enabled = false;
const context = await auth.$context;
const admin = {
  userId: randomUUID(),
  email: `${randomUUID()}@example.test`,
  role: "admin",
};
await bridge(auth, { action: "provision-user", ...admin });
await context.internalAdapter.createAccount({
  userId: admin.userId,
  accountId: admin.userId,
  providerId: "credential",
  password: await context.password.hash(password),
});
console.log(JSON.stringify({ url: origin, bridgeSecret, password, admin }));
process.on("SIGTERM", () =>
  server.close(() => {
    void database.end().then(() => process.exit(0));
  }),
);
