package api

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/riverqueue/river/riverdriver/riverpgxv5"
	"github.com/riverqueue/river/rivermigrate"
	"github.com/stretchr/testify/require"

	"github.com/gosom/google-maps-scraper/rqueue"
)

type batchKeyStore struct{}

func (batchKeyStore) ValidateAPIKey(_ context.Context, key string) (int, string, error) {
	if key != "fixture" {
		return 0, "", fmt.Errorf("invalid key")
	}
	return 1, "fixture", nil
}

func batchFixture(t *testing.T) (http.Handler, *pgxpool.Pool) {
	t.Helper()
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("TEST_DATABASE_URL is required for atomic bulk enqueue tests")
	}
	ctx := context.Background()
	admin, err := pgxpool.New(ctx, dsn)
	require.NoError(t, err)
	schema := fmt.Sprintf("enqueue_test_%d", time.Now().UnixNano())
	_, err = admin.Exec(ctx, "CREATE SCHEMA "+schema)
	require.NoError(t, err)
	config, err := pgxpool.ParseConfig(dsn)
	require.NoError(t, err)
	config.ConnConfig.RuntimeParams["search_path"] = schema
	db, err := pgxpool.NewWithConfig(ctx, config)
	require.NoError(t, err)
	t.Cleanup(func() { db.Close(); admin.Exec(ctx, "DROP SCHEMA "+schema+" CASCADE"); admin.Close() })
	migrator, err := rivermigrate.New(riverpgxv5.New(db), nil)
	require.NoError(t, err)
	_, err = migrator.Migrate(ctx, rivermigrate.DirectionUp, nil)
	require.NoError(t, err)
	_, err = db.Exec(ctx, `CREATE TABLE enqueue_log(keyword text,state text,cell text,day date,status text,updated_at timestamptz DEFAULT now(),last_error text,
		UNIQUE(keyword,state,cell,day));
		CREATE TABLE keyword_history(keyword text,state text,last_enqueued date,PRIMARY KEY(keyword,state));
		CREATE TABLE keyword_yield_evaluations(keyword text,state text,started_on date,status text,PRIMARY KEY(keyword,state,started_on));`)
	require.NoError(t, err)
	client, err := rqueue.NewClient(db, nil)
	require.NoError(t, err)
	router := chi.NewRouter()
	Routes(router, NewAppState(client, batchKeyStore{}))
	return router, db
}

func batchRequest(jobs ...BulkScrapeJob) BulkScrapeRequest {
	return BulkScrapeRequest{Day: "2026-10-13", Jobs: jobs}
}

func fixtureJob(cell string, cells int) BulkScrapeJob {
	return BulkScrapeJob{ScrapeRequest: ScrapeRequest{
		Keyword: "Plumbers", State: "tn", GeoCoordinates: cell, Zoom: 15,
		FastMode: true, Email: true, ExtraReviews: true,
	}, ExpectedCells: cells}
}

func sendBatch(handler http.Handler, payload BulkScrapeRequest, key string) *httptest.ResponseRecorder {
	body, _ := json.Marshal(payload)
	request := httptest.NewRequest(http.MethodPost, "/api/v1/scrape/bulk", bytes.NewReader(body))
	request.Header.Set("X-API-Key", key)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	return response
}

func countRows(t *testing.T, db *pgxpool.Pool, query string) int {
	t.Helper()
	var count int
	require.NoError(t, db.QueryRow(context.Background(), query).Scan(&count))
	return count
}

func TestBulkEnqueueReplaysAfterResponseLossAndJobRetention(t *testing.T) {
	handler, db := batchFixture(t)
	payload := batchRequest(fixtureJob("36,-86", 2), fixtureJob("35,-86", 2), fixtureJob("36,-86", 2))
	response := sendBatch(handler, payload, "fixture")
	require.Equal(t, http.StatusAccepted, response.Code, response.Body.String())
	require.JSONEq(t, `{"inserted":2,"skipped":1}`, response.Body.String())
	require.Equal(t, 2, countRows(t, db, "SELECT count(*) FROM river_job WHERE kind='scrape'"))
	require.Equal(t, 2, countRows(t, db, "SELECT count(*) FROM enqueue_log WHERE status='posted'"))
	require.Equal(t, 1, countRows(t, db, "SELECT count(*) FROM keyword_history"))
	var args rqueue.ScrapeJobArgs
	var raw []byte
	require.NoError(t, db.QueryRow(context.Background(), "SELECT args FROM river_job ORDER BY id LIMIT 1").Scan(&raw))
	require.NoError(t, json.Unmarshal(raw, &args))
	require.Equal(t, "36,-86", args.GeoCoordinates, "locking order must not reorder the pilot priority")
	require.Equal(t, 300, args.TimeoutSecs)
	require.Equal(t, "en", args.Lang)
	require.True(t, args.Email && args.ExtraReviews && args.FastMode)
	// Ignoring the first response models commit success followed by response loss.
	response = sendBatch(handler, payload, "fixture")
	require.JSONEq(t, `{"inserted":0,"skipped":3}`, response.Body.String())
	require.Equal(t, 2, countRows(t, db, "SELECT count(*) FROM river_job WHERE kind='scrape'"))
	_, err := db.Exec(context.Background(), "DELETE FROM river_job; UPDATE keyword_history SET last_enqueued='2026-10-14'")
	require.NoError(t, err)
	response = sendBatch(handler, payload, "fixture")
	require.JSONEq(t, `{"inserted":0,"skipped":3}`, response.Body.String())
	require.Zero(t, countRows(t, db, "SELECT count(*) FROM river_job"))
	require.Equal(t, 1, countRows(t, db, "SELECT count(*) FROM keyword_history WHERE last_enqueued='2026-10-14'"))
}

func TestBulkEnqueueRejectsWholeInvalidBatchAndRequiresAuthentication(t *testing.T) {
	handler, db := batchFixture(t)
	valid := fixtureJob("36,-86", 2)
	invalid := fixtureJob("35,-86", 2)
	invalid.Timeout = 301
	for _, payload := range []BulkScrapeRequest{
		batchRequest(valid, invalid), {Day: "invalid", Jobs: []BulkScrapeJob{valid}}, batchRequest(),
		batchRequest(valid, fixtureJob("35,-86", 3)),
		batchRequest(append(make([]BulkScrapeJob, rqueue.MaxEnqueueBatch), valid)...),
	} {
		require.Equal(t, http.StatusBadRequest, sendBatch(handler, payload, "fixture").Code)
	}
	require.Equal(t, http.StatusUnauthorized, sendBatch(handler, batchRequest(valid), "wrong").Code)
	require.Zero(t, countRows(t, db, "SELECT count(*) FROM enqueue_log"))
	require.Zero(t, countRows(t, db, "SELECT count(*) FROM river_job"))
}

func TestBulkEnqueueRollsBackLedgerWhenRiverInsertFails(t *testing.T) {
	handler, db := batchFixture(t)
	_, err := db.Exec(context.Background(), `ALTER TABLE river_job ADD CONSTRAINT reject_fixture CHECK(args->>'geo_coordinates' <> '35,-86')`)
	require.NoError(t, err)
	response := sendBatch(handler, batchRequest(fixtureJob("36,-86", 2), fixtureJob("35,-86", 2)), "fixture")
	require.Equal(t, http.StatusInternalServerError, response.Code)
	require.Zero(t, countRows(t, db, "SELECT count(*) FROM enqueue_log"))
	require.Zero(t, countRows(t, db, "SELECT count(*) FROM river_job"))
	require.Zero(t, countRows(t, db, "SELECT count(*) FROM keyword_history"))
}

func TestConcurrentBatchesDeduplicateAndCompleteDisjointCoverage(t *testing.T) {
	handler, db := batchFixture(t)
	var requests []BulkScrapeRequest
	for n := 0; n < 8; n++ {
		requests = append(requests, batchRequest(fixtureJob(fmt.Sprintf("%d,-86", 30+n), 8)))
	}
	var group sync.WaitGroup
	responses := make(chan *httptest.ResponseRecorder, 16)
	for _, payload := range append(requests, requests...) {
		group.Add(1)
		go func(payload BulkScrapeRequest) {
			defer group.Done()
			responses <- sendBatch(handler, payload, "fixture")
		}(payload)
	}
	group.Wait()
	close(responses)
	inserted := 0
	for response := range responses {
		require.Equal(t, http.StatusAccepted, response.Code, response.Body.String())
		var result BulkScrapeResponse
		require.NoError(t, json.Unmarshal(response.Body.Bytes(), &result))
		inserted += result.Inserted
	}
	require.Equal(t, 8, inserted)
	require.Equal(t, 8, countRows(t, db, "SELECT count(*) FROM river_job WHERE kind='scrape'"))
	require.Equal(t, 8, countRows(t, db, "SELECT count(*) FROM enqueue_log WHERE status='posted'"))
	require.Equal(t, 1, countRows(t, db, "SELECT count(*) FROM keyword_history"))
}

func TestBulkEnqueueRetainsWaveHistoryAndReservationLeases(t *testing.T) {
	handler, db := batchFixture(t)
	_, err := db.Exec(context.Background(), `INSERT INTO keyword_yield_evaluations VALUES('Plumbers','tn','2026-10-13','expanding');
		INSERT INTO enqueue_log(keyword,state,cell,day,status,updated_at) VALUES
		('Plumbers','tn','36,-86','2026-10-13','reserved',now()),
		('Plumbers','tn','35,-86','2026-10-13','reserved',now()-interval '16 minutes'),
		('Plumbers','tn','34,-86','2026-10-13','failed',now());`)
	require.NoError(t, err)
	response := sendBatch(handler, batchRequest(fixtureJob("36,-86", 2), fixtureJob("35,-86", 2), fixtureJob("34,-86", 2)), "fixture")
	require.Equal(t, http.StatusAccepted, response.Code, response.Body.String())
	require.JSONEq(t, `{"inserted":2,"skipped":1}`, response.Body.String())
	require.Equal(t, 1, countRows(t, db, "SELECT count(*) FROM enqueue_log WHERE status='reserved'"))
	require.Zero(t, countRows(t, db, "SELECT count(*) FROM keyword_history"), "the verified wave gate owns new campaign completion")
}
