package api

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/gosom/google-maps-scraper/log"
	"github.com/gosom/google-maps-scraper/rqueue"
)

// BulkScrapeJob supplies the same scrape options as the single-job endpoint.
type BulkScrapeJob struct {
	ScrapeRequest
	ExpectedCells int `json:"expected_cells,omitempty"`
}

// BulkScrapeRequest uses a stable ledger day so retries across midnight are safe.
type BulkScrapeRequest struct {
	Day  string          `json:"day"`
	Jobs []BulkScrapeJob `json:"jobs"`
}

// BulkScrapeResponse counts committed new jobs and entries already covered.
type BulkScrapeResponse struct {
	Inserted int `json:"inserted"`
	Skipped  int `json:"skipped"`
}

func bulkScrapeHandler(appState *AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
		decoder := json.NewDecoder(r.Body)
		decoder.DisallowUnknownFields()
		var request BulkScrapeRequest
		if err := decoder.Decode(&request); err != nil {
			respondWithJSON(w, http.StatusBadRequest, ErrorResponse{Message: "invalid request body"})
			return
		}
		if err := decoder.Decode(new(any)); err != io.EOF {
			respondWithJSON(w, http.StatusBadRequest, ErrorResponse{Message: "invalid request body"})
			return
		}
		day, err := time.Parse(time.DateOnly, request.Day)
		if err != nil || len(request.Jobs) == 0 || len(request.Jobs) > rqueue.MaxEnqueueBatch {
			respondWithJSON(w, http.StatusBadRequest, ErrorResponse{Message: "a valid day and 1 to 100 jobs are required"})
			return
		}
		jobs := make([]rqueue.EnqueueJob, 0, len(request.Jobs))
		coverage := make(map[[2]string]int)
		for index, job := range request.Jobs {
			job.SetDefaults()
			job.Keyword = strings.TrimSpace(job.Keyword)
			job.State = strings.ToLower(strings.TrimSpace(job.State))
			if err := job.Validate(); err != nil {
				respondWithJSON(w, http.StatusBadRequest, ErrorResponse{Message: fmt.Sprintf("job %d: %s", index, err)})
				return
			}
			if len(job.State) != 2 || job.State[0] < 'a' || job.State[0] > 'z' || job.State[1] < 'a' || job.State[1] > 'z' || job.GeoCoordinates == "" || job.ExpectedCells < 0 || job.ExpectedCells > 100000 {
				respondWithJSON(w, http.StatusBadRequest, ErrorResponse{Message: fmt.Sprintf("job %d: state, cell and valid coverage are required", index)})
				return
			}
			pair := [2]string{job.Keyword, job.State}
			if count, exists := coverage[pair]; exists && count != job.ExpectedCells {
				respondWithJSON(w, http.StatusBadRequest, ErrorResponse{Message: "inconsistent campaign coverage"})
				return
			}
			coverage[pair] = job.ExpectedCells
			jobs = append(jobs, rqueue.EnqueueJob{Args: rqueue.ScrapeJobArgs{
				State: job.State, Keyword: job.Keyword, Lang: job.Lang, MaxDepth: job.MaxDepth,
				Email: job.Email, GeoCoordinates: job.GeoCoordinates, Zoom: job.Zoom, Radius: job.Radius,
				FastMode: job.FastMode, ExtraReviews: job.ExtraReviews, TimeoutSecs: job.Timeout,
			}, ExpectedCells: job.ExpectedCells})
		}
		inserted, err := appState.RQueue.InsertEnqueueBatch(r.Context(), jobs, day)
		if err != nil {
			log.Error("failed to insert acquisition batch", "error", err)
			respondWithJSON(w, http.StatusInternalServerError, ErrorResponse{Message: "failed to create batch"})
			return
		}
		respondWithJSON(w, http.StatusAccepted, BulkScrapeResponse{Inserted: inserted, Skipped: len(jobs) - inserted})
	}
}
