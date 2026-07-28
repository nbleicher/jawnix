# Enforce administrator assurance at one boundary

Administration requires two live verified Authenticator Factors and an AAL2 Administrator Session, enforced centrally by `require_admin`; enrollment remains reachable below AAL2 but grants no administrative access. Revoked and superseded signed sessions are rejected through a server-side generation, while an exact copy of a currently valid bearer cookie cannot be distinguished from its original without adopting sender-constrained authentication.

Permanent lockout is rejected as the recovery policy. Break-glass Recovery is therefore an external two-person operator procedure with no application endpoint: it revokes every Administrator Session, removes the lost factors, records the operator, distinct authorizer, recipient, reason, and reference, and restores only the ability to enroll two replacement factors.
