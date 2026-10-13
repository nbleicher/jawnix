package gmaps

import (
	"context"
	"os"
	"testing"

	"github.com/gosom/scrapemate"
	"github.com/stretchr/testify/require"
)

// Sanitized response layouts captured from the same fetcher as production.
func TestSearchResponseLayouts(t *testing.T) {
	for _, tc := range []struct {
		name  string
		count int
	}{{"multi", 1}, {"single", 1}, {"empty", 0}} {
		t.Run(tc.name, func(t *testing.T) {
			body, err := os.ReadFile("testdata/search-" + tc.name + ".json")
			require.NoError(t, err)
			entries, err := ParseSearchResults(body)
			require.NoError(t, err)
			require.Len(t, entries, tc.count)
		})
	}
}

func TestSearchFailureClassification(t *testing.T) {
	for _, tc := range []struct {
		name     string
		response scrapemate.Response
		outcome  string
	}{
		{"throttled", scrapemate.Response{StatusCode: 429}, "blocked"},
		{"denied", scrapemate.Response{StatusCode: 403}, "blocked"},
		{"challenge", scrapemate.Response{StatusCode: 200, Body: []byte("<html>captcha</html>")}, "blocked"},
		{"upstream", scrapemate.Response{StatusCode: 503}, "fetch_error"},
		{"timeout", scrapemate.Response{Error: context.DeadlineExceeded}, "timeout"},
		{"malformed", scrapemate.Response{Body: []byte(")]}'\n[[null,[]]]")}, "parse_error"},
		{"unknown_shape", scrapemate.Response{Body: []byte(")]}'\n[[null,[[null]]]]")}, "parse_error"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			calls := 0
			job := NewSearchJob(&MapSearchParams{}, WithSearchJobWriterManagedCompletion(),
				WithSearchJobFailureHandler(func(_ string, err error) { calls++; require.Equal(t, tc.outcome, FailureOutcome(err)) }))
			_, _, err := job.Process(context.Background(), &tc.response)
			require.Error(t, err)
			require.Equal(t, 1, calls)
			require.Zero(t, job.MaxRetries)
		})
	}
}
