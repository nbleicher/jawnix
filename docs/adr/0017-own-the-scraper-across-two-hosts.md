# Own the Scraper across two hosts

Jawnix owns the complete Scraper system—control plane, worker, migrations,
schedules, and deployment source—in this repository, replacing Scale as the
production source of truth. Deployment remains split between the Jawnix
application host and a dedicated acquisition host for the Scraper database,
control service, worker, and scheduled operations, connected over private
WireGuard so the acquisition boundary in ADR 0001 remains intact. The imported
Scale snapshot is provenance and migration input rather than an independent
production source, and the running service changes only through the staged
cutover plan.
