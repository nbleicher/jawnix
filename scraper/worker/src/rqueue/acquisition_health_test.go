package rqueue

import (
	"context"
	"fmt"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/gosom/google-maps-scraper/gmaps"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/riverqueue/river"
	"github.com/riverqueue/river/rivertype"
	"github.com/stretchr/testify/require"
)

func healthFixture(t *testing.T) *AcquisitionHealth {
	t.Helper()
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("TEST_DATABASE_URL is required for the shared recovery integration test")
	}
	ctx := context.Background()
	admin, err := pgxpool.New(ctx, dsn)
	require.NoError(t, err)
	schema := fmt.Sprintf("health_test_%d", time.Now().UnixNano())
	_, err = admin.Exec(ctx, "CREATE SCHEMA "+schema)
	require.NoError(t, err)
	config, err := pgxpool.ParseConfig(dsn)
	require.NoError(t, err)
	config.ConnConfig.RuntimeParams["search_path"] = schema
	db, err := pgxpool.NewWithConfig(ctx, config)
	require.NoError(t, err)
	t.Cleanup(func() { db.Close(); admin.Exec(ctx, "DROP SCHEMA "+schema+" CASCADE"); admin.Close() })
	migration, err := os.ReadFile("../../../scraper_changes/migrations/20261013000000-acquisition-quality.sql")
	require.NoError(t, err)
	_, err = db.Exec(ctx, strings.Split(string(migration), "-- +migrate Down")[0])
	require.NoError(t, err)
	return NewAcquisitionHealth(db)
}

func TestSharedGuardPausesOnlyAfterFailedProbeAndRecovers(t *testing.T) {
	h := healthFixture(t)
	ctx := context.Background()
	for n := int64(1); n <= 6; n++ {
		job := &river.Job[ScrapeJobArgs]{JobRow: &rivertype.JobRow{ID: n, Attempt: 1}, Args: ScrapeJobArgs{Keyword: "fixture", State: "tn", GeoCoordinates: "36,-86"}}
		require.NoError(t, h.Record(job, &gmaps.AcquisitionError{Outcome: "blocked", Cause: fmt.Errorf("429")}, 0, 0))
	}
	delay, err := h.Delay(ctx)
	require.NoError(t, err)
	require.Zero(t, delay, "candidate failures alone do not pause the fleet")
	h.probe = func(context.Context, ScrapeJobArgs) error {
		return &gmaps.AcquisitionError{Outcome: "blocked", Cause: fmt.Errorf("429")}
	}
	require.NoError(t, h.check(ctx))
	delay, err = h.Delay(ctx)
	require.NoError(t, err)
	require.Positive(t, delay)
	// Expiring the cooldown alone never resumes scraping without a positive probe.
	_, err = h.db.Exec(ctx, "UPDATE acquisition_health SET probe_after=now()-interval '1 second',blocked_until=now()-interval '1 second'")
	require.NoError(t, err)
	delay, err = h.Delay(ctx)
	require.NoError(t, err)
	require.Positive(t, delay)
	h.probe = func(context.Context, ScrapeJobArgs) error { return nil }
	require.NoError(t, h.check(ctx))
	delay, err = h.Delay(ctx)
	require.NoError(t, err)
	require.Zero(t, delay)
	var jobs, failures int
	require.NoError(t, h.db.QueryRow(ctx, "SELECT jobs,failed_jobs FROM acquisition_quality_1h").Scan(&jobs, &failures))
	require.Equal(t, 6, jobs)
	require.Equal(t, 6, failures)
}

func TestFleetClaimsOneProbe(t *testing.T) {
	h := healthFixture(t)
	ctx := context.Background()
	_, err := h.db.Exec(ctx, "UPDATE acquisition_health SET probe_after=now()-interval '1 second'")
	require.NoError(t, err)
	var calls atomic.Int32
	entered, release := make(chan struct{}), make(chan struct{})
	h.probe = func(context.Context, ScrapeJobArgs) error { calls.Add(1); close(entered); <-release; return nil }
	errors := make(chan error, 8)
	go func() { errors <- h.check(ctx) }()
	<-entered
	var wg sync.WaitGroup
	for n := 0; n < 7; n++ {
		wg.Add(1)
		go func() { defer wg.Done(); errors <- h.check(ctx) }()
	}
	wg.Wait()
	close(release)
	for n := 0; n < 8; n++ {
		require.NoError(t, <-errors)
	}
	require.Equal(t, int32(1), calls.Load())
}
