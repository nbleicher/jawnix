# Distributed Scraper Setup — Netcup + Oracle + Mac Mini

## Architecture

```
┌─────────────────────────────────────────────────┐
│              Neon.tech (Managed PostgreSQL)       │
│         Central job queue + results store         │
└──────────┬──────────────┬────────────────────────┘
           │              │                │
    ┌──────▼──────┐ ┌─────▼──────┐ ┌──────▼──────┐
    │  Netcup VPS │ │ Oracle VPS │ │  Mac Mini   │
    │  ARM64      │ │            │ │             │
    │  serve+     │ │  worker    │ │  worker     │
    │  worker     │ │            │ │             │
    └─────────────┘ └────────────┘ └─────────────┘
```

- **Neon** holds all jobs and results. Workers pull jobs from River queue, write results back.
- **Netcup** runs the web dashboard (`serve`) + a worker. This is your control plane.
- **Oracle + Mac Mini** run workers only — they pull from the same queue and contribute capacity.
- Adding more VPSes later: just deploy another worker pointed at the same `DATABASE_URL`.

---

## Step 1 — Create Neon Database

1. Go to [neon.tech](https://neon.tech) → sign up (free tier is sufficient to start)
2. Create a new project → name it `gmapssaas`
3. Copy the **connection string** — it looks like:
   ```
   postgres://noah:<password>@ep-cool-name-123456.us-east-1.aws.neon.tech/gmapssaas?sslmode=require
   ```
4. In Neon dashboard → **Connection Details** → enable **Connection Pooling** (pgBouncer). Use the pooled connection string for workers.

> **Free tier limits:** 3 GB storage, 1 project, always-on compute. More than enough to start.

---

## Step 2 — Run Migrations (once)

Migrations run automatically when `serve` starts for the first time. You don't need to run them manually — just deploy the server in Step 4 and it will apply all migrations against your Neon DB on startup.

---

## Step 3 — Generate Encryption Key

Run this once and save the output — you'll use the same key on every node:

```bash
openssl rand -hex 32
```

Keep this secret. All nodes must share the same key.

---

## Step 4 — Netcup VPS (serve + worker)

### Install Docker

```bash
ssh root@<netcup-ip>
curl -fsSL https://get.docker.com | sh
```

### Deploy

```bash
mkdir -p ~/gmapssaas && cd ~/gmapssaas

# Create your env file (see .env.example)
nano .env

# Pull and start
docker compose -f docker-compose.netcup.yaml up -d
```

### Expose the dashboard

The `serve` container listens on port `8080`. To access it remotely:

**Option A — Direct access (quick):**
Open port 8080 in your Netcup firewall, then visit `http://<netcup-ip>:8080`.

**Option B — HTTPS via Caddy (recommended):**
Point a domain at your Netcup IP, then add Caddy in front:

```bash
# Install Caddy
apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install caddy

# /etc/caddy/Caddyfile
your.domain.com {
    reverse_proxy localhost:8080
}
systemctl restart caddy
```

---

## Step 5 — Oracle VPS (worker only)

```bash
ssh ubuntu@<oracle-ip>
curl -fsSL https://get.docker.com | sh

mkdir -p ~/gmapssaas && cd ~/gmapssaas
nano .env   # same DATABASE_URL and ENCRYPTION_KEY as Netcup

docker compose -f docker-compose.worker.yaml up -d
```

---

## Step 6 — Mac Mini (worker only)

You already have Go installed. Two options:

**Option A — Run directly with Go:**

```bash
cd ~/path/to/google-maps-scraper

export DATABASE_URL="postgres://noah:<password>@ep-cool-name-123456.us-east-1.aws.neon.tech/gmapssaas?sslmode=require"
export ENCRYPTION_KEY="<your-32-byte-hex-key>"
export CONCURRENCY=4
export FAST_MODE=true

go run ./cmd/gmapssaas worker
```

**Option B — Docker (same as Oracle):**

```bash
docker compose -f docker-compose.worker.yaml up -d
```

Note: the Docker image is multi-arch (supports arm64), so it works on Apple Silicon.

---

## Step 7 — Access Results Remotely

### Download CSV via API

The `serve` API exposes result exports. From any browser or tool:

```
GET http://<netcup-ip>:8080/api/v1/jobs/{job_id}/results?format=csv
```

Or use the web dashboard at `http://<netcup-ip>:8080` — it has built-in export buttons.

### Connect with TablePlus / DBeaver

Use your Neon connection string directly in any PostgreSQL client. You can browse results,
run queries, and export to CSV/Excel from anywhere without touching the VPS.

**Results table:** `scrape_results`  
**Jobs table:** `river_jobs` (River queue)

---

## Scaling Up

To add more workers (new VPS, cloud instance, etc.):

1. Install Docker on the new machine
2. Copy `docker-compose.worker.yaml` + `.env`
3. Set `CONCURRENCY` based on available cores (1 per 2 vCores is a safe start)
4. `docker compose -f docker-compose.worker.yaml up -d`

That's it. The River job queue handles work distribution automatically — no coordination needed.

---

## Recommended Concurrency Settings

| Machine         | vCores | Recommended CONCURRENCY |
|----------------|--------|--------------------------|
| Netcup ARM64    | 10     | 4–6 (leave headroom for serve) |
| Oracle VPS      | varies | half of available vCores |
| Mac Mini        | varies | 4–6 (M-series) |

Start conservative and raise if CPU/memory stay comfortable.

---

## Monitoring

Each worker exposes a health endpoint:

```
GET http://<worker-ip>:8081/health
```

Returns jobs processed, results per minute, uptime, active jobs. Check this after deploying
to confirm workers are connected and pulling jobs.

---

## Firewall Summary

| Port | Machine   | Purpose                        |
|------|-----------|--------------------------------|
| 8080 | Netcup    | Web dashboard / API (expose to internet) |
| 8081 | All workers | Health check (keep internal or firewall) |
| 22   | All       | SSH                            |

Neon handles its own TLS — no port to open for the DB.
