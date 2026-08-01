# Owning your scraper repo (instead of re-forking from scratch)

Your `auto_scrape/util/scraper_src` **is already your version** of the gosom SaaS edition — the full `cmd/gmapssaas` + River queue + admin + provisioning code, with your tweaks. It was just (a) missing two upstream build files and (b) not under version control. You do **not** need to re-clone gosom and re-apply everything. Make what you have a proper repo you own, complete it, and apply the patch.

## Recommended — own it in place

```bash
cd auto_scrape/util/scraper_src

# 1. Restore the two upstream files that were missing from the copy
cp /path/to/scale/worker/Dockerfile.saas                     ./Dockerfile.saas
cp /path/to/scale/scraper_changes/docker-compose.saas.yaml   ./docker-compose.saas.yaml

# 2. Apply your migration + the worker Go change
cp /path/to/scale/scraper_changes/migrations/20260617000000-add_businesses_and_leads.sql ./migrations/
#    then edit ./scraper/centralwriter.go per scale/scraper_changes/centralwriter_businesses_change.md (Part A + B)

# 3. Put it under version control
cat > .gitignore <<'EOF'
bin/
*.db
*.db-wal
*.db-shm
.env
*.local
__pycache__/
EOF
git init
git add .
git commit -m "Fork of gosom/google-maps-scraper SaaS edition + businesses/leads + provenance"

# 4. Create a PRIVATE repo on your GitHub, then:
git remote add origin git@github.com:<you>/gmaps-scraper.git
git push -u origin main
```

That's it — you now have your own versioned fork, the worker image can be built, and CI/deploys have a source of truth.

## Optional — track upstream so you can pull future gosom fixes

```bash
git remote add upstream https://github.com/gosom/google-maps-scraper.git
git fetch upstream
git diff upstream/main -- .        # see exactly how your copy differs from upstream
# later, to pull selected upstream changes:
git merge upstream/main            # resolve conflicts in centralwriter.go etc.
```

## When (not) to re-fork from scratch

- **Don't** re-clone `main` and re-apply by hand just to clear the Dockerfile blocker — `main` is newer than your snapshot, so you'd be re-applying your changes onto drifted code and risk losing tweaks.
- **Do** consider a clean fork only if you specifically want to jump onto the latest upstream *and* you've used the `git diff upstream/main` above to enumerate your changes first.

## Notes
- Secrets (`.env`, `DATABASE_URL`, keys) are git-ignored — never commit them (closes O-1 for the code repo).
- The two upstream files now live in `scale/` for convenience: `scale/worker/Dockerfile.saas` and `scale/scraper_changes/docker-compose.saas.yaml`.
- `docker-compose.saas.yaml` is the **local-dev Postgres only**; production uses managed Postgres + `scale/worker/docker-compose.yml`.
