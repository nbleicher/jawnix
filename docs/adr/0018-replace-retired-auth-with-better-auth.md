# Replace retired Supabase authentication with Better Auth

The Supabase project is unreachable. Better Auth owns passwords, sessions,
authenticator secrets, and recovery codes in a dedicated Neon project and
database. Vercel runs the identity service. Jawnix keeps Customer ownership,
User Account mappings, session generations, CSRF protection, and administrator
authorization in its existing application.

Existing identity UUIDs and email addresses are imported without changing
Customer or Distribution Event identities. Passwords and authenticator secrets
cannot be recovered from the retired provider. Users set passwords through
expiring provider links. Administrators enroll one authenticator and save
single-use recovery codes. No migration sends bulk email.

This supersedes ADR 0012's requirement for two verified Authenticator Factors.
One live verified Authenticator Factor plus a successfully verified provider
session establishes Administrator Assurance. A recovery code can establish the
same assurance when the device is unavailable. Provider session revocation and
factor removal invalidate administrator access, including existing signed
Jawnix cookies. Every entry to Scraper Operations still requires fresh
verification. Losing the authenticator and all recovery codes requires the
existing two-person Break-glass Recovery procedure.
