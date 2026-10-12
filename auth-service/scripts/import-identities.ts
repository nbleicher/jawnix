import { readFileSync } from "node:fs";
import { bridge } from "../src/bridge.js";
import { getRuntime } from "../src/auth.js";

const [manifestPath, adminEmail] = process.argv.slice(2);
if (!manifestPath || !adminEmail)
  throw new Error(
    "Provide the identity manifest and confirmed administrator email.",
  );
if (
  new URL(process.env.JAWNIX_AUTH_DATABASE_URL || "").pathname !==
  "/jawnix_auth"
)
  throw new Error("Import requires the dedicated Jawnix auth database.");
const manifest = JSON.parse(readFileSync(manifestPath, "utf8")) as {
  identities: { userId: string; email: string; name: string; role: string }[];
  adminIds: string[];
};
if (manifest.adminIds.length !== 1)
  throw new Error(
    "An explicit mapping is required when there is not exactly one administrator.",
  );
const identities = [
  ...manifest.identities,
  {
    userId: manifest.adminIds[0],
    email: adminEmail,
    name: "Noah Bleicher",
    role: "admin",
  },
];
const ids = new Set<string>();
const emails = new Set<string>();
for (const identity of identities) {
  identity.email = identity.email.trim().toLowerCase();
  if (
    !/^[0-9a-f-]{36}$/i.test(identity.userId) ||
    !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(identity.email) ||
    !["admin", "customer"].includes(identity.role)
  )
    throw new Error("Invalid identity manifest.");
  if (ids.has(identity.userId) || emails.has(identity.email))
    throw new Error("The identity manifest contains a conflicting mapping.");
  ids.add(identity.userId);
  emails.add(identity.email);
}
const { auth, database } = getRuntime();
try {
  for (const identity of identities)
    await bridge(auth, { action: "provision-user", ...identity });
  console.log(
    JSON.stringify({
      imported: identities.length,
      customers: manifest.identities.length,
      administrators: 1,
      emailsSent: 0,
    }),
  );
} finally {
  await database.end();
}
