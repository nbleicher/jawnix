import { randomUUID } from "node:crypto";
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { getMigrations } from "better-auth/db/migration";
import { createOTP } from "@better-auth/utils/otp";
import { base32 } from "@better-auth/utils/base32";
import pg from "pg";
import { createAuth } from "../src/auth.js";
import { bridge, bridgeAuthorized } from "../src/bridge.js";

const databaseURL = process.env.JAWNIX_AUTH_TEST_DATABASE_URL;
if (!databaseURL || !new URL(databaseURL).pathname.endsWith("_test"))
  throw new Error("Use an isolated *_test database for auth tests.");
const database = new pg.Pool({ connectionString: databaseURL });
const emails: { email: string; url: string }[] = [];
const verificationEmails: { email: string; url: string }[] = [];
const auth = createAuth(database, {
  baseURL: "http://localhost:3001/auth",
  secret: "test-only-secret-at-least-thirty-two-characters-long",
  sendVerification: async (email, url) => { verificationEmails.push({ email, url }); },
  sendReset: async (email, url) => {
    emails.push({ email, url });
  },
});

before(async () => {
  const migration = await getMigrations(auth.options);
  await migration.runMigrations();
});
after(async () => {
  await database.end();
});

async function provision(role = "customer") {
  const id = randomUUID();
  const email = `${id}@example.test`;
  const user = await bridge(auth, {
    action: "provision-user",
    userId: id,
    email,
    role,
  });
  assert.equal("id" in user && user.id, id);
  await bridge(auth, { action: "reset-user", email });
  const message = emails.at(-1)!;
  assert.equal(message.email, email);
  const token = new URL(message.url).pathname.split("/").at(-1)!;
  await auth.api.resetPassword({
    body: { token, newPassword: "password-for-auth-test" },
  });
  return { id, email };
}

test("bridge authorization uses a separate secret", () => {
  const secret = "bridge-test-secret-longer-than-thirty-two-characters";
  assert.equal(bridgeAuthorized(`Bearer ${secret}`, secret), true);
  assert.equal(bridgeAuthorized("Bearer invalid", secret), false);
  assert.equal(bridgeAuthorized("", ""), false);
});

test("manager uses password-only access with live session validation and cannot enroll MFA", async () => {
  const id = randomUUID();
  const email = `${id}@example.test`;
  const before = emails.length;
  const user = await bridge(auth, { action:"provision-manager", userId:id, email, name:"Alex fixture" });
  assert.ok("app_metadata" in user);
  assert.equal(user.app_metadata.jawnix_role, "manager");
  const link = await bridge(auth, { action:"manager-access-link", userId:id });
  assert.ok("url" in link && typeof link.url === "string");
  assert.equal(emails.length, before);
  const token = new URL(link.url).pathname.split("/").at(-1)!;
  await auth.api.resetPassword({ body:{ token, newPassword:"password-for-manager-test" } });
  const login = await auth.api.signInEmail({ body:{ email, password:"password-for-manager-test" } });
  assert.ok("token" in login && login.token);
  const identity = await bridge(auth, { action:"identity", accessToken:login.token as string });
  assert.ok("user" in identity);
  assert.ok(identity.user);
  assert.equal(identity.user.app_metadata.jawnix_role, "manager");
  await assert.rejects(bridge(auth, { action:"enroll", accessToken:login.token as string, password:"password-for-manager-test" }));
  assert.ok("claims" in identity && identity.claims);
  assert.deepEqual(await bridge(auth, { action:"validate-session", userId:id, sessionId:identity.claims.sessionId, role:"manager" }), { valid:true });
  await assert.rejects(bridge(auth, { action:"validate-session", userId:id, sessionId:identity.claims.sessionId, role:"admin" }));
  await bridge(auth, { action:"logout", accessToken:login.token as string });
  await assert.rejects(bridge(auth, { action:"validate-session", userId:id, sessionId:identity.claims.sessionId, role:"manager" }));
  assert.equal(emails.length, before);
});

test("public signup validates details, verifies email, and cannot grant an operator role", async () => {
  const email = `${randomUUID()}@example.test`;
  const details = { first_name:" New ", last_name:" Customer ", phone:"+1 (615) 555-0100", licensed_states:["TN", "SC", "TN"] };
  const request = (body: unknown) => auth.handler(new Request("http://localhost:3001/auth/sign-up/email", {
    method:"POST", headers:{ "Content-Type":"application/json", Origin:"http://localhost:3001" }, body:JSON.stringify(body),
  }));
  const base = { email, password:"signup-password-for-test", name:"Ignored", signupProfile:details, jawnixRole:"admin", emailVerified:true, callbackURL:"http://localhost:3001/app/complete-signup" };
  for (const invalid of [{ ...details, licensed_states:["XX"] }, { ...details, phone:"123" }, { ...details, first_name:" " }, { ...details, customer_id:99 }]) {
    assert.equal((await request({ ...base, signupProfile:invalid })).status, 400);
  }
  const response = await request(base);
  assert.equal(response.status, 200);
  const value = await response.json();
  assert.equal(value.token, null);
  assert.equal(value.user.jawnixRole, "customer");
  assert.equal(value.user.emailVerified, false);
  await assert.rejects(auth.api.signInEmail({ body:{ email, password:base.password } }));
  const message = verificationEmails.find(item => item.email === email)!;
  assert.ok(message);
  const verified = await auth.handler(new Request(message.url));
  assert.equal(verified.status, 302);
  const login = await auth.api.signInEmail({ body:{ email, password:base.password } });
  assert.ok("token" in login && login.token);
  const identity = await bridge(auth, { action:"identity", accessToken:login.token as string });
  assert.ok("user" in identity && identity.user);
  assert.equal(identity.user.email_verified, true);
  assert.equal(identity.user.app_metadata.jawnix_role, "customer");
  assert.deepEqual(identity.user.user_metadata.signup_profile, { first_name:"New", last_name:"Customer", phone:"6155550100", licensed_states:["SC", "TN"] });
  await assert.rejects(bridge(auth, { action:"enroll", accessToken:login.token as string, password:base.password }));
});

test("provisioning cannot silently turn a customer into a manager", async () => {
  const existing = await provision();
  await assert.rejects(bridge(auth, { action:"provision-manager", email:existing.email }));
  await assert.rejects(bridge(auth, { action:"manager-access-link", userId:existing.id }));
});

test("existing UUID survives password setup, and customer cannot enroll admin MFA", async () => {
  const user = await provision();
  const login = await auth.api.signInEmail({
    body: { email: user.email, password: "password-for-auth-test" },
  });
  assert.ok("token" in login && login.token);
  const token = (login as { token: string }).token;
  const identity = await bridge(auth, {
    action: "identity",
    accessToken: token,
  });
  assert.ok("claims" in identity);
  assert.ok(identity.claims);
  assert.equal(identity.claims.aal, "aal1");
  for (const path of ["enable", "disable", "get-totp-uri", "generate-backup-codes"]) {
    const management = await auth.handler(
      new Request(`http://localhost:3001/auth/two-factor/${path}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Origin: "http://localhost:3001",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ password: "password-for-auth-test" }),
      }),
    );
    assert.equal(management.status, 404);
  }
  await assert.rejects(
    bridge(auth, {
      action: "enroll",
      accessToken: token,
      password: "password-for-auth-test",
    }),
  );
  const signup = await auth.handler(
    new Request("http://localhost:3001/auth/sign-up/email", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Origin: "http://localhost:3001",
      },
      body: JSON.stringify({
        email: "uninvited@example.test",
        password: "password-for-auth-test",
        name: "Uninvited",
        jawnixRole: "admin",
      }),
    }),
  );
  assert.notEqual(signup.status, 200);
});

test("one verified TOTP establishes assurance, recovery codes are single use, revocation fails closed", async () => {
  const user = await provision("admin");
  const login = await auth.api.signInEmail({
    body: { email: user.email, password: "password-for-auth-test" },
  });
  assert.ok("token" in login && login.token);
  let token = (login as { token: string }).token;
  const enrolled = await bridge(auth, {
    action: "enroll",
    accessToken: token,
    password: "password-for-auth-test",
  });
  assert.ok("totp" in enrolled && enrolled.totp?.secret);
  assert.ok(enrolled.totp);
  assert.ok(enrolled.backup_codes);
  assert.equal(
    (
      (await bridge(auth, { action: "identity", accessToken: token })) as {
        claims: { aal: string };
      }
    ).claims.aal,
    "aal1",
  );
  await assert.rejects(
    bridge(auth, {
      action: "verify",
      accessToken: token,
      factorId: enrolled.id,
      code: "invalid-code",
    }),
  );
  assert.equal(
    (
      (await bridge(auth, { action: "identity", accessToken: token })) as {
        claims: { aal: string };
      }
    ).claims.aal,
    "aal1",
  );
  const code = await createOTP(
    new TextDecoder().decode(base32.decode(enrolled.totp.secret)),
    { digits: 6, period: 30 },
  ).totp();
  const verified = await bridge(auth, {
    action: "verify",
    accessToken: token,
    factorId: enrolled.id,
    code,
  });
  assert.ok("access_token" in verified);
  token = verified.access_token;
  assert.equal(
    (
      (await bridge(auth, { action: "identity", accessToken: token })) as {
        claims: { aal: string };
      }
    ).claims.aal,
    "aal2",
  );
  const factors = await bridge(auth, { action: "factors", userId: user.id });
  assert.ok(Array.isArray(factors));
  assert.equal(factors.length, 1);
  assert.equal(factors[0].status, "verified");
  const passwordOnly = await auth.api.signInEmail({
    body: { email: user.email, password: "password-for-auth-test" },
  });
  assert.ok(
    "twoFactorRedirect" in passwordOnly && passwordOnly.twoFactorRedirect,
  );
  assert.ok(!("token" in passwordOnly && passwordOnly.token));
  const recovered = await bridge(auth, {
    action: "verify",
    accessToken: token,
    factorId: enrolled.id,
    code: enrolled.backup_codes[0],
    method: "recovery",
  });
  assert.ok("access_token" in recovered);
  await assert.rejects(
    bridge(auth, {
      action: "verify",
      accessToken: recovered.access_token,
      factorId: enrolled.id,
      code: enrolled.backup_codes[0],
      method: "recovery",
    }),
  );
  await bridge(auth, {
    action: "delete-factor",
    userId: user.id,
    factorId: enrolled.id,
  });
  await assert.rejects(
    bridge(auth, { action: "identity", accessToken: recovered.access_token }),
  );
});
