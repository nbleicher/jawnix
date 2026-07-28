# Accept before replacing User Accounts

Provisioning a replacement User Account records a pending User Account Invitation and changes nothing else. The Customer's existing User Account stays active until the invited identity signs in; that sign-in is acceptance, and it activates the new account and deactivates the former one in a single transaction. Administrators provision access only by invitation and never set or read a credential.

Exactly one active User Account per Customer, and at most one outstanding invitation per Customer, are enforced by partial unique indexes rather than by the screens or the request handlers. Concurrency therefore resolves in the database: a racing second invitation or a racing second activation is refused, and no path exists by which a Customer ends up with two accounts or none.

Replacement never touches the durable Customer. Its identity, Agency membership, Licensed States, Batch Requests, and Distribution Events are untouched by both invitation and acceptance, so replacing authentication can never read as replacing the party that owns the permanent no-repeat history.
