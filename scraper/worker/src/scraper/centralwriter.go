package scraper

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/gosom/google-maps-scraper/gmaps"
	"github.com/gosom/google-maps-scraper/internal/jsonbsanitize"
	"github.com/gosom/google-maps-scraper/log"
	"github.com/gosom/scrapemate"
	"github.com/jackc/pgx/v5/pgxpool"
)

// FlushResult is sent to the River worker when results are flushed to DB.
type FlushResult struct {
	ResultCount int
	PhoneCount  int
	Err         error
}

type trackedJob struct {
	jobID      string
	entries    []*gmaps.Entry
	completion chan FlushResult
	riverJobID int64
	keyword    string
	cell       string
	state      string
	startedAt  time.Time
}

// SaveFunc persists results. The default publishes files for the inventory loader;
// tests can supply a lightweight substitute via NewCentralWriter.
type SaveFunc func(ctx context.Context, riverJobID int64, keyword, cell, state string, entries []*gmaps.Entry) error

var _ scrapemate.ResultWriter = (*CentralWriter)(nil)

// CentralWriter tracks exactly one in-flight River job, receives ScrapeMate
// results, and flushes them to the database when the exit monitor signals done.
type CentralWriter struct {
	mu      sync.Mutex
	current *trackedJob

	save           SaveFunc
	OnResultsSaved func(count int)
}

// NewCentralWriter creates a new CentralWriter.
// Pass nil for saveFn to use the default PostgreSQL saver built from db.
func NewCentralWriter(db *pgxpool.Pool, saveFn SaveFunc) *CentralWriter {
	if saveFn == nil {
		saveFn = pgSave(db)
	}

	return &CentralWriter{save: saveFn}
}

// RegisterJob registers the active River job and returns a completion channel
// that receives the flush result.
func (cw *CentralWriter) RegisterJob(jobID string, riverJobID int64, keyword, cell, state string) <-chan FlushResult {
	cw.mu.Lock()
	defer cw.mu.Unlock()

	ch := make(chan FlushResult, 1)
	cw.current = &trackedJob{
		jobID:      jobID,
		completion: ch,
		riverJobID: riverJobID,
		keyword:    keyword,
		cell:       cell,
		state:      state,
		startedAt:  time.Now(),
	}

	log.Debug("registered scrape job", "job_id", jobID, "river_job_id", riverJobID)

	return ch
}

// AddResult appends an entry for the currently tracked job.
func (cw *CentralWriter) AddResult(jobID string, entry *gmaps.Entry) {
	cw.mu.Lock()
	defer cw.mu.Unlock()

	if cw.current == nil || cw.current.jobID != jobID {
		return
	}

	cw.current.entries = append(cw.current.entries, entry)
}

// MarkDone is called by the exit monitor when a job is complete.
func (cw *CentralWriter) MarkDone(jobID string) {
	cw.Flush(jobID)
}

// ForceFlush immediately flushes results for a job (used on timeout/shutdown).
func (cw *CentralWriter) ForceFlush(jobID string) {
	cw.flush(jobID, &gmaps.AcquisitionError{Outcome: "timeout", Cause: fmt.Errorf("job ended before results arrived")})
}

// Fail signals the attempt failure without publishing a successful empty file.
func (cw *CentralWriter) Fail(jobID string, err error) {
	cw.mu.Lock()
	j := cw.current
	if j == nil || j.jobID != jobID {
		cw.mu.Unlock()
		return
	}
	cw.current = nil
	cw.mu.Unlock()
	j.completion <- FlushResult{Err: err}
}

// Discard drops the tracked job without persisting results.
func (cw *CentralWriter) Discard(jobID string) {
	cw.mu.Lock()
	defer cw.mu.Unlock()

	if cw.current != nil && cw.current.jobID == jobID {
		cw.current = nil
	}
}

// TrackedJobs returns how many jobs are currently tracked in-memory.
func (cw *CentralWriter) TrackedJobs() int {
	cw.mu.Lock()
	defer cw.mu.Unlock()

	if cw.current == nil {
		return 0
	}

	return 1
}

// FlushQueueDepth is kept for health endpoint compatibility.
func (cw *CentralWriter) FlushQueueDepth() int {
	return 0
}

// Flush saves results to DB, signals completion, and clears tracked state.
// Idempotent: a second call for the same jobID is a no-op.
func (cw *CentralWriter) Flush(jobID string) {
	cw.flush(jobID, nil)
}

func (cw *CentralWriter) flush(jobID string, emptyErr error) {
	cw.mu.Lock()
	j := cw.current

	if j == nil || j.jobID != jobID {
		cw.mu.Unlock()
		return
	}

	cw.current = nil
	cw.mu.Unlock()
	if len(j.entries) == 0 && emptyErr != nil {
		j.completion <- FlushResult{Err: emptyErr}
		return
	}

	for _, entry := range j.entries {
		jsonbsanitize.StripNULFromEntry(entry)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	err := cw.save(ctx, j.riverJobID, j.keyword, j.cell, j.state, j.entries)

	cancel()

	if err != nil {
		log.Error("failed to save results",
			"job_id", j.jobID,
			"river_job_id", j.riverJobID,
			"error", err,
		)
	} else if cw.OnResultsSaved != nil && len(j.entries) > 0 {
		cw.OnResultsSaved(len(j.entries))
	}

	phones := 0
	for _, entry := range j.entries {
		if entry.Phone != "" {
			phones++
		}
	}
	j.completion <- FlushResult{ResultCount: len(j.entries), PhoneCount: phones, Err: err}

	log.Debug("flushed scrape job",
		"job_id", j.jobID,
		"river_job_id", j.riverJobID,
		"result_count", len(j.entries),
		"duration_ms", time.Since(j.startedAt).Milliseconds(),
		"save_error", err,
	)
}

// Run processes results from the ScrapeMate scraper and buffers them for the
// currently registered River job.
func (cw *CentralWriter) Run(ctx context.Context, in <-chan scrapemate.Result) error {
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case result, ok := <-in:
			if !ok {
				return nil
			}

			cw.processResult(result)
		}
	}
}

func (cw *CentralWriter) processResult(result scrapemate.Result) {
	jobID := ""
	if result.Job != nil {
		jobID = result.Job.GetID()
	}

	if entry, ok := result.Data.(*gmaps.Entry); ok {
		entryJobID := entry.ID
		if entryJobID == "" {
			entryJobID = jobID
		}

		cw.AddResult(entryJobID, entry)
		cw.markCompletedFromResult(result, 1)

		return
	}

	if entries, ok := result.Data.([]*gmaps.Entry); ok {
		for _, entry := range entries {
			entry.ID = jobID
			cw.AddResult(jobID, entry)
		}

		cw.markCompletedFromResult(result, len(entries))
	}
}

func (cw *CentralWriter) markCompletedFromResult(result scrapemate.Result, count int) {
	if count <= 0 || result.Job == nil {
		return
	}

	switch job := result.Job.(type) {
	case *gmaps.PlaceJob:
		if job.WriterManagedCompletion && job.ExitMonitor != nil {
			job.ExitMonitor.IncrPlacesCompleted(count)
		}
	case *gmaps.EmailExtractJob:
		if job.WriterManagedCompletion && job.ExitMonitor != nil {
			job.ExitMonitor.IncrPlacesCompleted(count)
		}
	case *gmaps.SearchJob:
		if job.WriterManagedCompletion && job.ExitMonitor != nil {
			job.ExitMonitor.IncrPlacesCompleted(count)
		}
	}
}

// pgSave publishes each completed job for worker/shipper.py. The pool remains
// in the constructor signature, but scraping does not wait for inventory writes.
func pgSave(_ *pgxpool.Pool) SaveFunc {
	spool := os.Getenv("GMS_SPOOL_DIR")
	if spool == "" {
		spool = "/data/incoming"
	}
	return func(ctx context.Context, riverJobID int64, keyword, cell, state string, entries []*gmaps.Entry) (retErr error) {
		if err := ctx.Err(); err != nil {
			return err
		}
		if err := os.MkdirAll(spool, 0o755); err != nil {
			return err
		}
		base := filepath.Join(spool, fmt.Sprintf("results-%d-%d.ndjson", riverJobID, time.Now().UnixNano()))
		tmp := base + ".tmp"
		file, err := os.Create(tmp)
		if err != nil {
			return err
		}
		defer func() {
			if retErr != nil {
				_ = file.Close()
				_ = os.Remove(tmp)
				_ = os.Remove(base + ".done.tmp")
			}
		}()
		encoder := json.NewEncoder(file)
		for _, entry := range entries {
			if err := ctx.Err(); err != nil {
				return err
			}
			if err := encoder.Encode(map[string]any{
				"job_id": riverJobID, "keyword": keyword,
				"state": state, "cell": cell, "entry": entry,
			}); err != nil {
				return err
			}
		}
		if err := file.Sync(); err != nil {
			return err
		}
		if err := file.Close(); err != nil {
			return err
		}
		if err := os.Rename(tmp, base); err != nil {
			return err
		}
		metadata, err := json.Marshal(map[string]any{"job_id": riverJobID, "keyword": keyword})
		if err != nil {
			return err
		}
		if err := os.WriteFile(base+".done.tmp", metadata, 0o644); err != nil {
			return err
		}
		return os.Rename(base+".done.tmp", base+".done")
	}
}
