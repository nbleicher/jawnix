package gmaps

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"

	"github.com/google/uuid"
	"github.com/gosom/google-maps-scraper/exiter"
	"github.com/gosom/scrapemate"
)

type SearchJobOptions func(*SearchJob)

type MapLocation struct {
	Lat     float64
	Lon     float64
	ZoomLvl float64
	Radius  float64
}

type MapSearchParams struct {
	Location  MapLocation
	Query     string
	ViewportW int
	ViewportH int
	Hl        string
}

type SearchJob struct {
	scrapemate.Job

	params                  *MapSearchParams
	ExitMonitor             exiter.Exiter
	WriterManagedCompletion bool
	OnFailure               func(string, error)
}

func NewSearchJob(params *MapSearchParams, opts ...SearchJobOptions) *SearchJob {
	const (
		defaultPrio       = scrapemate.PriorityMedium
		defaultMaxRetries = 3
		baseURL           = "https://maps.google.com/search"
	)

	job := SearchJob{
		Job: scrapemate.Job{
			ID:         uuid.New().String(),
			Method:     http.MethodGet,
			URL:        baseURL,
			URLParams:  buildGoogleMapsParams(params),
			MaxRetries: defaultMaxRetries,
			Priority:   defaultPrio,
		},
	}

	job.params = params

	for _, opt := range opts {
		opt(&job)
	}

	return &job
}

func WithSearchJobExitMonitor(exitMonitor exiter.Exiter) SearchJobOptions {
	return func(j *SearchJob) {
		j.ExitMonitor = exitMonitor
	}
}

func WithSearchJobWriterManagedCompletion() SearchJobOptions {
	return func(j *SearchJob) {
		j.WriterManagedCompletion = true
		// River owns retries. An inner retry could arrive after this job completes.
		j.MaxRetries = 0
	}
}

func WithSearchJobFailureHandler(handler func(string, error)) SearchJobOptions {
	return func(j *SearchJob) { j.OnFailure = handler }
}

type AcquisitionError struct {
	Outcome string
	Cause   error
}

func (e *AcquisitionError) Error() string { return e.Outcome + ": " + e.Cause.Error() }
func (e *AcquisitionError) Unwrap() error { return e.Cause }

func FailureOutcome(err error) string {
	var acquisition *AcquisitionError
	if errors.As(err, &acquisition) {
		return acquisition.Outcome
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return "timeout"
	}
	return "fetch_error"
}

func (j *SearchJob) fail(outcome string, err error) (any, []scrapemate.IJob, error) {
	failure := &AcquisitionError{Outcome: outcome, Cause: err}
	if j.OnFailure != nil {
		j.OnFailure(j.ID, failure)
	}
	if j.ExitMonitor != nil {
		j.ExitMonitor.IncrSeedCompleted(1)
	}
	return nil, nil, failure
}

func (j *SearchJob) ProcessOnFetchError() bool {
	return true
}

func (j *SearchJob) Process(_ context.Context, resp *scrapemate.Response) (any, []scrapemate.IJob, error) {
	defer func() {
		resp.Document = nil
		resp.Body = nil
		resp.Meta = nil
	}()

	if resp.StatusCode == 403 || resp.StatusCode == 429 {
		return j.fail("blocked", fmt.Errorf("HTTP %d", resp.StatusCode))
	}
	if resp.Error != nil {
		return j.fail(FailureOutcome(resp.Error), resp.Error)
	}
	if resp.StatusCode >= 400 {
		return j.fail("fetch_error", fmt.Errorf("HTTP %d", resp.StatusCode))
	}
	prefix := strings.ToLower(string(resp.Body[:min(len(resp.Body), 4096)]))
	if strings.Contains(prefix, "<html") || strings.Contains(prefix, "<!doctype") {
		return j.fail("blocked", fmt.Errorf("received HTML instead of Maps results"))
	}

	body := removeFirstLine(resp.Body)
	if len(body) == 0 {
		return j.fail("parse_error", fmt.Errorf("empty response body"))
	}

	entries, err := ParseSearchResults(body)
	if err != nil {
		return j.fail("parse_error", fmt.Errorf("failed to parse search results: %w", err))
	}

	entries = filterAndSortEntriesWithinRadius(entries,
		j.params.Location.Lat,
		j.params.Location.Lon,
		j.params.Location.Radius,
	)

	if j.ExitMonitor != nil {
		j.ExitMonitor.IncrPlacesFound(len(entries))
		j.ExitMonitor.IncrSeedCompleted(1)

		if !j.WriterManagedCompletion {
			j.ExitMonitor.IncrPlacesCompleted(len(entries))
		}
	}

	return entries, nil, nil
}

func removeFirstLine(data []byte) []byte {
	if len(data) == 0 {
		return data
	}

	index := bytes.IndexByte(data, '\n')
	if index == -1 {
		return []byte{}
	}

	return data[index+1:]
}

func buildGoogleMapsParams(params *MapSearchParams) map[string]string {
	params.ViewportH = 800
	params.ViewportW = 600

	ans := map[string]string{
		"tbm":      "map",
		"authuser": "0",
		"hl":       params.Hl,
		"q":        params.Query,
	}

	pb := fmt.Sprintf("!4m12!1m3!1d3826.902183192154!2d%.4f!3d%.4f!2m3!1f0!2f0!3f0!3m2!1i%d!2i%d!4f%.1f!7i20!8i0"+
		"!10b1!12m22!1m3!18b1!30b1!34e1!2m3!5m1!6e2!20e3!4b0!10b1!12b1!13b1!16b1!17m1!3e1!20m3!5e2!6b1!14b1!46m1!1b0"+
		"!96b1!19m4!2m3!1i360!2i120!4i8",
		params.Location.Lon,
		params.Location.Lat,
		params.ViewportW,
		params.ViewportH,
		params.Location.ZoomLvl,
	)

	ans["pb"] = pb

	return ans
}
