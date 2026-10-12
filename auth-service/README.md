# Jawnix authentication

Better Auth runs as a Vercel Node function. The dedicated Neon project is
`jawnix-auth` (`silent-heart-69317155`), with database `jawnix_auth`.

Production configuration lives in Vercel's `jawnix-auth` project environment:

- `JAWNIX_AUTH_DATABASE_URL`: pooled Neon connection to `jawnix_auth`.
- `BETTER_AUTH_SECRET`: provider cookie and credential encryption secret.
- `JAWNIX_AUTH_BRIDGE_SECRET`: private server bridge credential.
- `BETTER_AUTH_URL`: `https://jawnix.com/auth`.
- `RESEND_API_KEY`: transactional password-link delivery.

Secrets never belong in Git. Local configuration uses ignored `.env.production.local`.
Schema migrations use `JAWNIX_AUTH_DATABASE_URL_UNPOOLED`, a direct connection
to the same database. Run `node --env-file=.env.production.local
node_modules/tsx/dist/cli.mjs scripts/migrate.ts` after testing the migration.

Caddy proxies `/auth/*` from Jawnix to the identity service. FastAPI uses
`JAWNIX_AUTH_PROVIDER=better-auth`, `JAWNIX_BETTER_AUTH_URL`, and the bridge
secret. The private bridge provisions identities and connects Jawnix's
enrollment, session, and factor controls to the provider.
Public signup and public factor management are disabled. The private bridge
owns enrollment and replacement. Customer UUIDs stay unchanged during identity import.

Run `npm run typecheck` and `npm test` with
`JAWNIX_AUTH_TEST_DATABASE_URL` pointing at an isolated database ending in
`_test`. Tests exercise actual Better Auth and Postgres, with captured email
delivery instead of sending mail.
