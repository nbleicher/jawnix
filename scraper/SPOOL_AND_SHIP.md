# Spool-and-Ship — decoupled results loading

Workers write results to a **local file**, a separate **shipper** loads them into Postgres. This decouples scrape throughput from DB latency (your coupling concern), batches the writes, and uses **one DB connection per box** instead of one per container (mitigates O-9).

```
 box1 (worker host)
 ┌────────────────────────────────────────────────────────┐
 │ worker container ─┐                                      │
 │ worker container ─┼─► /data/incoming/                    │
 │   …  (12 of them) │     results-<job>-<ts>.ndjson        │
 │ worker container ─┘     results-<job>-<ts>.ndjson.done ──┼─┐
 └────────────────────────────────────────────────────────┘ │ systemd .path
                                                             │ fires on the .done glob
                                  ┌──────────────────────────▼─────────┐
                                  │ shipper.py --drain (oneshot)        │
                                  │  • parse all *.ndjson.done          │
                                  │  • upsert → businesses (ON CONFLICT)│──► Managed Postgres
                                  │  • upsert → scrape_results counts   │
                                  │  • move file+marker → archive/      │
                                  └─────────────────────────────────────┘
```

## How it triggers (event-driven, "always on")
- **`gms-ship.path`** watches `PathExistsGlob=/data/incoming/*.ndjson.done`. The instant a worker finishes a job and drops a `.done` marker, systemd starts `gms-ship.service`. After the shipper archives the files the glob no longer matches, so the unit **re-arms** for the next file. No long-running daemon to babysit.
- **`gms-ship.timer`** runs the same drain every ~3 min as a **backstop** — inotify can drop events under heavy load, so the timer guarantees nothing is ever stranded. Belt and suspenders.
- Both just call `shipper.py --drain`, which processes the **whole folder** each run → idempotent and burst-proof whether woken by one event or fifty.

## Why it's safe
- **Never reads a half-written file** — the worker writes `results-*.ndjson`, renames it atomically, then creates `.ndjson.done` *last*. The shipper only touches files that have a marker.
- **Idempotent loads** — `INSERT … ON CONFLICT (dedup_key) DO NOTHING/UPDATE`, and files are archived only *after* a successful commit. Re-running can't double-load.
- **Fails soft** — if Postgres is slow or down, the file is left in place and the run exits non-zero; scraping keeps going and files buffer on disk until the DB recovers. Malformed NDJSON is moved to `quarantine/` by default and reported; pass `--tolerate-parse-errors` only when you explicitly want to load valid lines from a malformed file.

## Pieces
| Piece | Runs on | What |
|---|---|---|
| Go patch Part C (`centralwriter_businesses_change.md`) | worker image | worker writes NDJSON + `.done` instead of writing Postgres |
| `worker/docker-compose.yml` | box1 | bind-mounts `/data/incoming` into every container; sets `GMS_SPOOL_DIR` |
| `worker/shipper.py` | box1 host | drains markers → `businesses` + lightweight `scrape_results` counts, archives, retries/quarantines |
| `worker/bootstrap.sh` | box1 host | provisions the box incl. installing `gms-ship.path` + `.service` + `.timer` |

## Monitoring the backlog
The health signal is **file backlog**: if `*.ndjson.done` files pile up in `/data/incoming`, the shipper or DB is behind. `shipper.py --max-backlog N` warns when pending files exceed N. From the control side, this also shows up as **stalled `businesses` growth**, which `alert.py`'s pace check already catches.

## Apply (on box1)
1. Build the image with Go patch **Part B + C** (worker writes NDJSON).
2. Run `worker/bootstrap.sh` — it writes `worker/.env` (incl. `GMS_SPOOL_DIR`), builds the image, and installs the `gms-ship` `.path`/`.service`/`.timer`. (Compose already mounts `/data/incoming`.)
3. Test: `touch /data/incoming/x.ndjson && touch /data/incoming/x.ndjson.done` → `journalctl -u gms-ship.service -n 20`.
