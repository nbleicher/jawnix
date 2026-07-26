# Back up both data-ownership stores

Encrypted backups cover both PostgreSQL and the persistent Scraper Dataset, and restore verification checks their dataset version and checksum relationship. The restored Scraper Dataset may be newer than PostgreSQL and replay forward idempotently, but it must contain—and never predate—PostgreSQL's last synchronized dataset version.
