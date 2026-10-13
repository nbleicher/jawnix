package rqueue

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/gosom/google-maps-scraper/gmaps"
	"github.com/gosom/google-maps-scraper/log"
	"github.com/gosom/scrapemate/adapters/fetchers/stealth"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/riverqueue/river"
)

// One durable guard covers workers sharing the same acquisition egress.
// Lease-based probes never publish inventory or modify operator pause controls.
type AcquisitionHealth struct {
	db    *pgxpool.Pool
	probe func(context.Context, ScrapeJobArgs) error
}

func NewAcquisitionHealth(db *pgxpool.Pool) *AcquisitionHealth {
	return &AcquisitionHealth{db: db, probe: probeMaps}
}

func (h *AcquisitionHealth) Delay(ctx context.Context) (time.Duration, error) {
	var blocked bool
	err := h.db.QueryRow(ctx, `SELECT blocked_until IS NOT NULL FROM acquisition_health WHERE id`).Scan(&blocked)
	if blocked {
		return time.Minute, err
	}
	return 0, err
}

func (h *AcquisitionHealth) Record(job *river.Job[ScrapeJobArgs], failure error, results, phones int) error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	outcome := "success"
	if failure != nil {
		outcome = gmaps.FailureOutcome(failure)
	} else if results == 0 {
		outcome = "empty"
	}
	knownGood, err := json.Marshal(job.Args)
	if err != nil {
		return err
	}
	tx, err := h.db.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	tag, err := tx.Exec(ctx, `INSERT INTO scrape_attempts (job_id,attempt,keyword,state,cell,outcome,result_count,phone_count)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING`,
		job.ID, job.Attempt, job.Args.Keyword, job.Args.State, job.Args.GeoCoordinates, outcome, results, phones)
	if err != nil {
		return err
	}
	if tag.RowsAffected() > 0 {
		_, err = tx.Exec(ctx, `UPDATE acquisition_health SET
            failure_streak=CASE WHEN $1 IN ('success','empty') THEN 0 ELSE failure_streak+1 END,
            empty_streak=CASE WHEN $1='empty' THEN empty_streak+1 WHEN $1='success' THEN 0 ELSE empty_streak END,
			known_good=CASE WHEN $1='success' AND $2>=3 THEN $3::jsonb ELSE known_good END,
            probe_after=CASE WHEN (failure_streak>=5 OR empty_streak>=49)
                AND (last_probe_at IS NULL OR last_probe_at < now()-interval '2 minutes')
                THEN COALESCE(probe_after,now()) ELSE probe_after END
            WHERE id`, outcome, results, string(knownGood))
		if err != nil {
			return err
		}
	}
	return tx.Commit(ctx)
}

func (h *AcquisitionHealth) Run(ctx context.Context) {
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()
	for {
		if err := h.check(ctx); err != nil && ctx.Err() == nil {
			log.Error("acquisition health probe failed", "error", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func recoveryDelay(level int) time.Duration {
	return min(2*time.Minute*time.Duration(1<<min(max(level, 0), 4)), 30*time.Minute)
}

func (h *AcquisitionHealth) check(ctx context.Context) error {
	var raw []byte
	var level int
	rows, err := h.db.Query(ctx, `UPDATE acquisition_health SET lease_until=now()+interval '90 seconds'
        WHERE id AND probe_after<=now() AND (lease_until IS NULL OR lease_until<now())
        RETURNING known_good, cooldown_level`)
	if err != nil {
		return err
	}
	if !rows.Next() {
		rows.Close()
		return rows.Err()
	}
	err = rows.Scan(&raw, &level)
	rows.Close()
	if err != nil {
		return err
	}
	args := ScrapeJobArgs{Keyword: "grocery store", Lang: "en", GeoCoordinates: "36.1627,-86.7816", Zoom: 15, Radius: 10000, FastMode: true}
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &args); err != nil {
			return err
		}
	}
	probeCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	failure := h.probe(probeCtx, args)
	if failure != nil && len(raw) > 0 && probeCtx.Err() == nil {
		failure = h.probe(probeCtx, ScrapeJobArgs{Keyword: "grocery store", Lang: "en", GeoCoordinates: "36.1627,-86.7816", Zoom: 15, Radius: 10000, FastMode: true})
	}
	cancel()
	outcome := "success"
	if failure != nil {
		outcome = gmaps.FailureOutcome(failure)
	}
	// Release a stale lease only after its owner exits. Shutdown leaves it to expire.
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if failure == nil {
		_, err = h.db.Exec(ctx, `UPDATE acquisition_health SET blocked_until=NULL,probe_after=NULL,lease_until=NULL,
            failure_streak=0,empty_streak=0,cooldown_level=0,last_probe_at=now(),last_probe_outcome=$1 WHERE id`, outcome)
	} else {
		delay := recoveryDelay(level)
		_, err = h.db.Exec(ctx, `UPDATE acquisition_health SET blocked_until=now()+($1*interval '1 second'),
            probe_after=now()+($1*interval '1 second'),lease_until=NULL,cooldown_level=cooldown_level+1,
            last_probe_at=now(),last_probe_outcome=$2 WHERE id`, int(delay.Seconds()), outcome)
	}
	log.Info("acquisition recovery probe", "outcome", outcome, "paused", failure != nil)
	return err
}

func probeMaps(ctx context.Context, args ScrapeJobArgs) error {
	lat, lon := parseGeoCoordinates(args.GeoCoordinates)
	job := gmaps.NewSearchJob(&gmaps.MapSearchParams{Query: args.Keyword, Hl: args.Lang,
		Location: gmaps.MapLocation{Lat: lat, Lon: lon, ZoomLvl: float64(args.Zoom), Radius: args.Radius}})
	fetcher := stealth.New("firefox", nil)
	defer fetcher.Close()
	response := fetcher.Fetch(ctx, job)
	data, _, err := job.Process(ctx, &response)
	if err != nil {
		return err
	}
	if len(data.([]*gmaps.Entry)) == 0 {
		return &gmaps.AcquisitionError{Outcome: "empty", Cause: fmt.Errorf("known-good search returned no businesses")}
	}
	return nil
}
