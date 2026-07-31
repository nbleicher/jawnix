# Go change — worker result sink (O-5 / O-4)

**Apply into:** `auto_scrape/util/scraper_src/scraper/centralwriter.go`
**Depends on:** the migrations in `scale/scraper_changes/migrations/` for `businesses`/`leads` and lightweight `scrape_results` counts (apply them first).

> **Chosen architecture: Part C (spool-and-ship).** The worker writes NDJSON + a `.done` marker; `worker/shipper.py` bulk-loads `businesses` and records lightweight per-job counts in `scrape_results`. This decouples scraping from DB latency, batches loads, and uses one DB connection per box (mitigates O-9). See `SPOOL_AND_SHIP.md`.
>
> **Apply: Part B (provenance) + Part C (below).** Part B's `state`/`cell` are written into the NDJSON wrapper. **Part A (direct write to Postgres) is the superseded alternative — skip it;** it's kept below only for reference.

---

# Part A — direct write *(SKIPPED — superseded by Part C; reference only)*

## Why additive (keep `scrape_results`)
`scrape_results` is **read** elsewhere — `rqueue.go` fetches a job's results and does `SUM(result_count)` for stats, and `jobs.go`/`worker_jobs.go` delete by `job_id`. So we do **not** remove that write. We keep it and *also* upsert each business into the deduped `businesses` table. Dedup happens at write time via `ON CONFLICT (dedup_key) DO NOTHING/UPDATE`.

## 1. Add imports
In the `import (...)` block at the top of `centralwriter.go`, add:
```go
	"crypto/md5"
	"encoding/hex"
	"strings"

	"github.com/jackc/pgx/v5"
```
(`pgxpool` is already imported.)

## 2. Replace `pgSave` and add two helpers
Replace the existing `pgSave` function (the one writing `scrape_results`) with:

```go
// pgSave returns a SaveFunc that (1) writes the per-job blob to scrape_results
// (kept — rqueue.go reads results/result_count) and (2) upserts each business
// into the deduplicated `businesses` table.
func pgSave(db *pgxpool.Pool) SaveFunc {
	return func(ctx context.Context, riverJobID int64, keyword string, entries []*gmaps.Entry) error {
		resultsJSON, err := json.Marshal(entries)
		if err != nil {
			return err
		}

		q := `INSERT INTO scrape_results (job_id, keyword, results, result_count)
			VALUES ($1, $2, $3, $4)
			ON CONFLICT (job_id) DO UPDATE SET
				results = $3,
				result_count = $4`

		if _, err = db.Exec(ctx, q, riverJobID, keyword, resultsJSON, len(entries)); err != nil {
			return err
		}

		return upsertBusinesses(ctx, db, riverJobID, keyword, entries)
	}
}

// dedupKey mirrors util/organize.py: place_id → cid → md5(title|phone|address).
func dedupKey(e *gmaps.Entry) string {
	if e.PlaceID != "" {
		return "pid:" + e.PlaceID
	}
	if e.Cid != "" {
		return "cid:" + e.Cid
	}
	norm := strings.ToLower(strings.TrimSpace(e.Title)) + "|" +
		strings.TrimSpace(e.Phone) + "|" + strings.TrimSpace(e.Address)
	sum := md5.Sum([]byte(norm))
	return "tp:" + hex.EncodeToString(sum[:])
}

// upsertBusinesses writes one deduplicated row per business (batched).
func upsertBusinesses(ctx context.Context, db *pgxpool.Pool, riverJobID int64, keyword string, entries []*gmaps.Entry) error {
	if len(entries) == 0 {
		return nil
	}

	const q = `INSERT INTO businesses
		(dedup_key, place_id, cid, title, phone, website, category, address,
		 latitude, longitude, rating, review_count, emails, keyword, source_job_id, raw)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
		ON CONFLICT (dedup_key) DO UPDATE SET
			last_seen    = NOW(),
			review_count = EXCLUDED.review_count,
			rating       = EXCLUDED.rating`

	batch := &pgx.Batch{}
	for _, e := range entries {
		raw, _ := json.Marshal(e)
		batch.Queue(q,
			dedupKey(e), e.PlaceID, e.Cid, e.Title, e.Phone, e.WebSite,
			e.Category, e.Address, e.Latitude, e.Longtitude, e.ReviewRating,
			e.ReviewCount, e.Emails, keyword, riverJobID, string(raw))
	}

	br := db.SendBatch(ctx, batch)
	defer func() { _ = br.Close() }()
	for range entries {
		if _, err := br.Exec(); err != nil {
			return err
		}
	}
	return nil
}
```

## 3. Notes
- **Field mapping:** `e.WebSite` → `website`, `e.Longtitude` (the struct's spelling) → `longitude`, `e.ReviewRating` → `rating`, `e.ReviewCount` → `review_count`, `e.Emails` (`[]string`) → `emails TEXT[]`, `json.Marshal(e)` → `raw JSONB`.
- **`state` and `cell` are left NULL** for now — the worker's `pgSave` only receives `keyword`. Threading state/cell from the job args is the FR-4.4 provenance follow-up (small, separate change). `export_leads.py` derives lead state from the phone area code anyway.
- **Leads:** `businesses` now fills in real time; `export_leads.py --daily` rolls `businesses → leads` and writes the 10k CSVs. No bridge transform needed.

## 4. Apply & build
```bash
cd auto_scrape/util/scraper_src
# 1) copy the migration in, then run it (see SETUP_POSTGRES_CONTROL_VPS.md Part A4)
cp ../../../scale/scraper_changes/migrations/20260617000000-add_businesses_and_leads.sql migrations/
# 2) edit scraper/centralwriter.go per §1–§2 above
go build ./...           # compiles
go vet ./scraper/        # sanity
# 3) rebuild the worker image (worker/Dockerfile.saas) and redeploy box1
```

> Could not `go build` here (no Go toolchain in this environment). Build on the box / your machine; the code follows the existing pgx patterns in `postgres/resultwriter.go`.

---

# Part B — FR-4.4 provenance (populate `state` + `cell`)

Part A fills `businesses` but leaves `state` and `cell` NULL because `pgSave` only receives `keyword`. Part B threads both through. `cell` already exists on the job (`GeoCoordinates = "lat,lon"`); `state` is **not carried today**, so it must be added from the enqueuer → API → job → writer. (`control/enqueue.py` already sends `"state"` and the correct `"max_depth"` field — done.)

Five files, all small:

**B1 — `api/responses.go`** (add to the `ScrapeRequest` struct):
```go
	// US state the job belongs to (provenance)
	State string `json:"state,omitempty" example:"fl"`
```

**B2 — `api/api.go`** (in `scrapeHandler`, add to the `jobArgs` literal):
```go
		State:          req.State,
```

**B3 — `rqueue/rqueue.go`** (add to `ScrapeJobArgs`):
```go
	State string `json:"state"`
```

**B4 — `rqueue/rqueue.go`** — extend the `ScrapeManager` interface and the `Work` call:
```go
// interface:
RegisterJob(jobID string, riverJobID int64, keyword, cell, state string) <-chan scraper.FlushResult
// in Work(), replace the RegisterJob call:
completionCh := w.Manager.RegisterJob(jobID, job.ID, args.Keyword, args.GeoCoordinates, args.State)
```

**B5 — `scraper/centralwriter.go`** — carry `cell`/`state` on the tracked job and into the save:
```go
// trackedJob: add two fields
cell  string
state string

// SaveFunc: widen the signature
type SaveFunc func(ctx context.Context, riverJobID int64, keyword, cell, state string, entries []*gmaps.Entry) error

// RegisterJob: accept + store them
func (cw *CentralWriter) RegisterJob(jobID string, riverJobID int64, keyword, cell, state string) <-chan FlushResult {
	// ... set cw.current.cell = cell; cw.current.state = state ...
}

// Flush: pass them to save
err := cw.save(ctx, j.riverJobID, j.keyword, j.cell, j.state, j.entries)
```

Then update `pgSave`/`upsertBusinesses` from Part A to take `cell, state string` and add them to the `businesses` INSERT column list + values:
```go
const q = `INSERT INTO businesses
    (dedup_key, place_id, cid, title, phone, website, category, address,
     latitude, longitude, rating, review_count, emails, state, keyword, cell, source_job_id, raw)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
    ON CONFLICT (dedup_key) DO UPDATE SET
        last_seen = NOW(), review_count = EXCLUDED.review_count, rating = EXCLUDED.rating`
// ...Queue(q, dedupKey(e), e.PlaceID, e.Cid, e.Title, e.Phone, e.WebSite, e.Category,
//        e.Address, e.Latitude, e.Longtitude, e.ReviewRating, e.ReviewCount, e.Emails,
//        state, keyword, cell, riverJobID, string(raw))
```

**Tests:** the `RegisterJob` / `SaveFunc` signature change ripples to `scraper/centralwriter_test.go` and `rqueue/rqueue_test.go` — update those call sites (add the two new string args).

After Part B, every `businesses` row carries `state`, `keyword`, `cell`, `source_job_id`, and `last_seen` — so per-keyword/per-state analysis is a plain `WHERE`, with no `input_id` parsing (closes the FR-4.4 / I-9 / I-18 concerns).

---

# Part C — spool-and-ship variant (RECOMMENDED for the fleet)

Instead of writing to Postgres from the worker, write each job's results to a local **NDJSON** file + a `.done` marker. `worker/shipper.py` (triggered by a systemd `.path` on the marker) bulk-loads them into `businesses`. This decouples scrape throughput from DB latency, batches the writes, and uses **one DB connection per box** instead of one per container.

**Replace `pgSave`** (and you do **not** need the Part A `upsertBusinesses`/`dedupKey` helpers or the pgx import) with:

```go
// imports to add: "os", "path/filepath", "fmt", "time"  ("encoding/json" already present)

// pgSave spools each job's results to a local NDJSON file + a .done marker.
// worker/shipper.py drains them into Postgres. db is unused here (kept for the
// NewCentralWriter signature); pass nil or ignore.
func pgSave(_ *pgxpool.Pool) SaveFunc {
	spool := os.Getenv("GMS_SPOOL_DIR")
	if spool == "" {
		spool = "/data/incoming"
	}
	return func(ctx context.Context, riverJobID int64, keyword, cell, state string, entries []*gmaps.Entry) error {
		if err := os.MkdirAll(spool, 0o755); err != nil {
			return err
		}
		base := filepath.Join(spool, fmt.Sprintf("results-%d-%d.ndjson", riverJobID, time.Now().UnixNano()))
		f, err := os.Create(base + ".tmp")
		if err != nil {
			return err
		}
		enc := json.NewEncoder(f)
		for _, e := range entries {
			_ = enc.Encode(map[string]any{
				"job_id": riverJobID, "keyword": keyword, "state": state, "cell": cell, "entry": e,
			})
		}
		if err := f.Close(); err != nil {
			return err
		}
		if err := os.Rename(base+".tmp", base); err != nil { // atomic publish of the data file
			return err
		}
		return os.WriteFile(base+".done", nil, 0o644) // marker LAST → shipper only reads complete files
	}
}
```

**Tradeoff to know:** this drops the worker's direct raw-JSONB `scrape_results` write. The archived NDJSON files are the raw audit, and `worker/shipper.py` writes a tiny `scrape_results (job_id, keyword, result_count)` row for stats and empty-rate alerts.

NDJSON line shape the shipper expects:
```json
{"job_id":123,"keyword":"plumbers","state":"fl","cell":"27.1,-82.1","entry":{ …raw gmaps Entry… }}
```
