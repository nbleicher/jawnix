# Queue Operations

River remains the durable job queue. The enqueuer deliberately keeps only a
small amount of ready work in front of the workers so a large campaign cannot
fill Postgres or hide a stalled scraper fleet.

## Backpressure

`control/enqueue.py --watch` calculates the target queue depth from fresh
worker heartbeats:

```text
target = clamp(alive_workers * target_per_worker, min_target_depth, max_target_depth)
```

The shipped values are 25 jobs per worker, a minimum of 25, and a hard cap of
500. If heartbeat data is unavailable, the fallback target is 50. Each cycle
adds at most 100 jobs and then waits five seconds before measuring again. These
values reflect the first box's measured smoke-test rate and keep roughly 20
seconds of work ready per worker.

The all-in-one bootstrap installs `gms-heartbeat.timer`, which records every
worker container once per minute. Heartbeats older than three minutes do not
count toward the target.

Change these values on the Configure page or in `control/active_states.yaml`.
Keep the maximum bounded; increasing it does not increase scraper throughput.

## Healthy Signals

The dashboard shows queued, running, retryable, and oldest queued job age.
The alert timer reports a problem when any of these defaults are exceeded:

- queued jobs: 500
- oldest queued job: 30 minutes
- retryable jobs: 50
- completed spool files: 200
- oldest completed spool file: 15 minutes
- empty-result rate: 90% over the last hour

A rising oldest-job age with idle workers usually means the workers cannot
claim jobs. Rising retryable jobs usually means the scrape operation or an
external dependency is failing. A full queue with all workers busy means more
capacity is needed or the enqueue target should remain capped until throughput
is understood.

## Retention

River removes completed and cancelled job rows after 24 hours and discarded
job rows after seven days. The maintenance worker removes lightweight
`scrape_results` count rows after seven days. Business records are retained.

Completed NDJSON files are a separate disk-backed handoff. `gms-ship.path`
drains them in coalesced two-second batches and `gms-ship.timer` retries the
drain every minute, so bursts do not start a process per file and a missed
filesystem event cannot leave the spool growing forever.

## Scaling

`bootstrap-box.sh` defaults to approximately two worker containers per three
CPU cores because each container is limited to 1.5 CPUs. Override the detected
value with `WORKER_REPLICAS` in `.env`, then apply it with:

```bash
docker compose -f docker-compose.box.yml up -d --scale worker="$WORKER_REPLICAS" worker
```

After changing capacity, wait up to three heartbeat intervals for the dynamic
queue target to settle.
