# Enforce administrator assurance at one boundary

Administration requires two live verified Authenticator Factors and an AAL2 Administrator Session, enforced centrally by `require_admin`; enrollment remains reachable below AAL2 but grants no administrative access. Revoked and superseded signed sessions are rejected through a server-side generation, while an exact copy of a currently valid bearer cookie cannot be distinguished from its original without adopting sender-constrained authentication.

Scraper Operations adds step-up without creating a parallel identity system: a fresh challenge through the same Authenticator Factor provider issues a signed Scraper Privileged Session bound to the Administrator Session generation. Native Scraper operations require both `require_admin` and that grant. This avoids persistent elevation and a second revocation store while keeping private Scraper credentials behind Jawnix.

Amended 2026-11-09 (operator UI plan Q4): the challenge happens once per sign-in rather than on every entry. Entering again reuses a live grant; the grant is bound to the exact sign-in (a digest of its session's CSRF token) as well as the generation, and lasts until sign-out or Administrator Session expiry instead of fifteen idle minutes. Sign-out clears it, "Sign out everywhere" and factor changes revoke it through the generation, and a later sign-in never inherits it.

Permanent lockout is rejected as the recovery policy. Break-glass Recovery is therefore an external two-person operator procedure with no application endpoint: it revokes every Administrator Session, removes the lost factors, records the operator, distinct authorizer, recipient, reason, and reference, and restores only the ability to enroll two replacement factors.
