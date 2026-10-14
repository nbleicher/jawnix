package rqueue

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"time"

	"github.com/riverqueue/river"
)

// MaxEnqueueBatch bounds the work committed by one acquisition request.
const MaxEnqueueBatch = 100

// EnqueueJob combines scrape parameters with the full campaign's coverage count.
// ExpectedCells is zero when the caller does not maintain keyword history.
type EnqueueJob struct {
	Args          ScrapeJobArgs
	ExpectedCells int
}

type enqueueKey struct {
	Keyword string `json:"keyword"`
	State   string `json:"state"`
	Cell    string `json:"cell"`
}

type campaignCoverage struct {
	Keyword       string `json:"keyword"`
	State         string `json:"state"`
	ExpectedCells int    `json:"expected_cells"`
}

// InsertEnqueueBatch commits River jobs, their posted ledger entries, and legacy
// campaign completion together. Replays use the durable ledger even after River
// removes finished jobs. Fresh reservations made by older producers are retained.
func (c *Client) InsertEnqueueBatch(ctx context.Context, jobs []EnqueueJob, day time.Time) (int, error) {
	if len(jobs) == 0 || len(jobs) > MaxEnqueueBatch {
		return 0, fmt.Errorf("batch must contain between 1 and %d jobs", MaxEnqueueBatch)
	}

	keys := make([]enqueueKey, 0, len(jobs))
	unique := make(map[enqueueKey]EnqueueJob, len(jobs))
	coverage := make(map[[2]string]int)
	for _, job := range jobs {
		key := enqueueKey{job.Args.Keyword, job.Args.State, job.Args.GeoCoordinates}
		pair := [2]string{key.Keyword, key.State}
		if previous, exists := coverage[pair]; exists && previous != job.ExpectedCells {
			return 0, fmt.Errorf("inconsistent campaign coverage")
		}
		coverage[pair] = job.ExpectedCells
		if _, exists := unique[key]; !exists {
			keys = append(keys, key)
			unique[key] = job
		}
	}
	// Lock overlapping ledger entries in the same order across producers.
	ordered := append([]enqueueKey(nil), keys...)
	sort.Slice(ordered, func(i, j int) bool {
		left, right := ordered[i], ordered[j]
		if left.Keyword != right.Keyword {
			return left.Keyword < right.Keyword
		}
		if left.State != right.State {
			return left.State < right.State
		}
		return left.Cell < right.Cell
	})
	payload, err := json.Marshal(ordered)
	if err != nil {
		return 0, err
	}
	tx, err := c.dbPool.Begin(ctx)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback(context.Background()) //nolint:errcheck // no-op after a successful commit
	// Keep alias membership stable until jobs and canonical coverage commit.
	if _, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock_shared(hashtextextended('acquisition-category-catalogue',0))`); err != nil {
		return 0, err
	}
	// Disjoint batches for the same campaign must also see each other's posted
	// cells before deciding whether legacy coverage is complete.
	if _, err := tx.Exec(ctx, `WITH pairs AS MATERIALIZED (
		SELECT DISTINCT coalesce(alias.category_id,input.keyword) AS keyword,input.state
		FROM jsonb_to_recordset($1::jsonb) AS input(keyword text,state text,cell text)
		LEFT JOIN acquisition_category_aliases alias ON alias.alias=lower(input.keyword)
		ORDER BY keyword,state
	) SELECT pg_advisory_xact_lock(hashtextextended('acquisition-enqueue:'||keyword||':'||state,0)) FROM pairs`, payload); err != nil {
		return 0, err
	}

	rows, err := tx.Query(ctx, `WITH identified AS (
        SELECT input.*,alias.category_id
        FROM jsonb_to_recordset($1::jsonb) AS input(keyword text,state text,cell text)
        LEFT JOIN acquisition_category_aliases alias ON alias.alias=lower(input.keyword)
    ), chosen AS (
        SELECT DISTINCT ON(coalesce(category_id,keyword),state,cell) * FROM identified
        ORDER BY coalesce(category_id,keyword),state,cell,keyword
    ) INSERT INTO enqueue_log(keyword,state,cell,day,status,updated_at)
        SELECT keyword,state,cell,$2::date,'reserved',now() FROM chosen input
        WHERE input.category_id IS NULL OR (
            NOT EXISTS(SELECT 1 FROM acquisition_category_cells covered
                WHERE covered.category_id=input.category_id AND covered.state=input.state AND covered.cell=input.cell)
            AND NOT EXISTS(SELECT 1 FROM enqueue_log covered
                JOIN acquisition_category_aliases alias ON alias.alias=lower(covered.keyword)
                WHERE alias.category_id=input.category_id AND covered.state=input.state AND covered.cell=input.cell AND covered.status='posted')
        )
		ORDER BY keyword,state,cell
		ON CONFLICT(keyword,state,cell,day) DO UPDATE SET
		  status='reserved',updated_at=now(),last_error=NULL
		WHERE enqueue_log.status='failed' OR
		  (enqueue_log.status='reserved' AND enqueue_log.updated_at < now()-interval '15 minutes')
		RETURNING keyword,state,cell`, payload, day.Format(time.DateOnly))
	if err != nil {
		return 0, err
	}
	reserved := make(map[enqueueKey]bool)
	for rows.Next() {
		var key enqueueKey
		if err := rows.Scan(&key.Keyword, &key.State, &key.Cell); err != nil {
			rows.Close()
			return 0, err
		}
		reserved[key] = true
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return 0, err
	}

	params := make([]river.InsertManyParams, 0, len(reserved))
	posted := make([]enqueueKey, 0, len(reserved))
	// Preserve pilot and wave priority rather than the ledger's locking order.
	for _, key := range keys {
		if reserved[key] {
			params = append(params, river.InsertManyParams{Args: unique[key].Args})
			posted = append(posted, key)
		}
	}
	if len(params) > 0 {
		if _, err := c.riverClient.InsertManyTx(ctx, tx, params); err != nil {
			return 0, err
		}
		payload, err = json.Marshal(posted)
		if err != nil {
			return 0, err
		}
		if _, err := tx.Exec(ctx, `UPDATE enqueue_log ledger SET status='posted',updated_at=now(),last_error=NULL
			FROM jsonb_to_recordset($1::jsonb) AS input(keyword text,state text,cell text)
			WHERE ledger.keyword=input.keyword AND ledger.state=input.state AND ledger.cell=input.cell AND ledger.day=$2::date`,
			payload, day.Format(time.DateOnly)); err != nil {
			return 0, err
		}
		if _, err := tx.Exec(ctx, `INSERT INTO acquisition_category_cells(category_id,state,cell)
            SELECT alias.category_id,input.state,input.cell
            FROM jsonb_to_recordset($1::jsonb) AS input(keyword text,state text,cell text)
            JOIN acquisition_category_aliases alias ON alias.alias=lower(input.keyword)
            ON CONFLICT DO NOTHING`, payload); err != nil {
			return 0, err
		}
	}

	pairs := make([]campaignCoverage, 0, len(coverage))
	for pair, count := range coverage {
		if count > 0 {
			pairs = append(pairs, campaignCoverage{pair[0], pair[1], count})
		}
	}
	// Consistent history locking also protects concurrent overlapping batches.
	sort.Slice(pairs, func(i, j int) bool {
		if pairs[i].Keyword != pairs[j].Keyword {
			return pairs[i].Keyword < pairs[j].Keyword
		}
		return pairs[i].State < pairs[j].State
	})
	if len(pairs) > 0 {
		payload, err = json.Marshal(pairs)
		if err != nil {
			return 0, err
		}
		if _, err := tx.Exec(ctx, `INSERT INTO keyword_history(keyword,state,last_enqueued)
			SELECT input.keyword,input.state,$2::date
			FROM jsonb_to_recordset($1::jsonb) AS input(keyword text,state text,expected_cells int)
			WHERE (SELECT count(DISTINCT cell) FROM enqueue_log ledger
			       WHERE ledger.keyword=input.keyword AND ledger.state=input.state AND ledger.day=$2::date AND ledger.status='posted') >= input.expected_cells
			  AND COALESCE((SELECT status='legacy' FROM keyword_yield_evaluations evaluation
			      WHERE evaluation.keyword=input.keyword AND evaluation.state=input.state
			      ORDER BY started_on DESC LIMIT 1),true)
			ORDER BY input.keyword,input.state
			ON CONFLICT(keyword,state) DO UPDATE SET
			  last_enqueued=greatest(keyword_history.last_enqueued,EXCLUDED.last_enqueued)`,
			payload, day.Format(time.DateOnly)); err != nil {
			return 0, err
		}
	}
	if err := tx.Commit(ctx); err != nil {
		return 0, err
	}
	return len(params), nil
}
