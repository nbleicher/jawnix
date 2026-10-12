//nolint:testpackage // verifies the default result sink used by the worker
package scraper

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/gosom/google-maps-scraper/gmaps"
	"github.com/stretchr/testify/require"
)

func TestDefaultWriterPublishesLoadableResults(t *testing.T) {
	spool := t.TempDir()
	t.Setenv("GMS_SPOOL_DIR", spool)
	writer := NewCentralWriter(nil, nil)
	completion := writer.RegisterJob("job-42", 42, "roofers", "40,-83", "oh")
	writer.AddResult("job-42", &gmaps.Entry{Title: "A Roofing", Phone: "6145550101"})
	require.NotPanics(t, func() { writer.MarkDone("job-42") })
	require.NoError(t, (<-completion).Err)

	markers, err := filepath.Glob(filepath.Join(spool, "results-42-*.ndjson.done"))
	require.NoError(t, err)
	require.Len(t, markers, 1)
	data, err := os.ReadFile(strings.TrimSuffix(markers[0], ".done"))
	require.NoError(t, err)
	var row struct {
		JobID   int64        `json:"job_id"`
		Keyword string       `json:"keyword"`
		State   string       `json:"state"`
		Cell    string       `json:"cell"`
		Entry   *gmaps.Entry `json:"entry"`
	}
	require.NoError(t, json.Unmarshal(data, &row))
	require.Equal(t, int64(42), row.JobID)
	require.Equal(t, "roofers", row.Keyword)
	require.Equal(t, "oh", row.State)
	require.Equal(t, "40,-83", row.Cell)
	require.Equal(t, "6145550101", row.Entry.Phone)
}

func TestDefaultWriterRecordsAnEmptyJob(t *testing.T) {
	spool := t.TempDir()
	t.Setenv("GMS_SPOOL_DIR", spool)
	writer := NewCentralWriter(nil, nil)
	completion := writer.RegisterJob("job-43", 43, "roofers", "40,-83", "oh")
	writer.MarkDone("job-43")
	require.NoError(t, (<-completion).Err)
	markers, err := filepath.Glob(filepath.Join(spool, "results-43-*.ndjson.done"))
	require.NoError(t, err)
	require.Len(t, markers, 1)
	metadata, err := os.ReadFile(markers[0])
	require.NoError(t, err)
	require.JSONEq(t, `{"job_id":43,"keyword":"roofers"}`, string(metadata))
	data, err := os.ReadFile(strings.TrimSuffix(markers[0], ".done"))
	require.NoError(t, err)
	require.Empty(t, data)
}

func TestDefaultWriterDoesNotPublishAnInvalidResult(t *testing.T) {
	spool := t.TempDir()
	t.Setenv("GMS_SPOOL_DIR", spool)
	writer := NewCentralWriter(nil, nil)
	completion := writer.RegisterJob("job-44", 44, "roofers", "40,-83", "oh")
	writer.AddResult("job-44", &gmaps.Entry{Latitude: math.NaN()})
	writer.MarkDone("job-44")
	require.Error(t, (<-completion).Err)
	files, err := os.ReadDir(spool)
	require.NoError(t, err)
	require.Empty(t, files)
}
