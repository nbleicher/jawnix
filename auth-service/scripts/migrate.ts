import { getMigrations } from "better-auth/db/migration";
import { getRuntime } from "../src/auth.js";

if (!process.env.JAWNIX_AUTH_DATABASE_URL_UNPOOLED)
  throw new Error("A direct migration connection is required.");
process.env.JAWNIX_AUTH_DATABASE_URL =
  process.env.JAWNIX_AUTH_DATABASE_URL_UNPOOLED;
const { auth, database } = getRuntime();
const migrations = await getMigrations(auth.options);
await migrations.runMigrations();
await database.end();
console.log("Better Auth schema migrated.");
