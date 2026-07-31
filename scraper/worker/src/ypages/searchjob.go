package ypages

import (
	"bytes"
	"context"
	"fmt"
	"net/http"
	"strings"

	"github.com/PuerkitoBio/goquery"
	"github.com/google/uuid"
	"github.com/gosom/google-maps-scraper/gmaps"
	"github.com/gosom/scrapemate"
)

const baseURL = "https://www.yellowpages.com/search"

// SearchJob fetches one page of YellowPages results and returns gmaps.Entry
// items so they flow into the same rotating CSV writer as Google Maps results.
type SearchJob struct {
	scrapemate.Job
	SearchTerms string
	GeoLocation string
	Page        int
	MaxPages    int
}

// NewSearchJob creates the first-page job for the given search terms and location.
// maxPages controls how many result pages to scrape (30 results each).
func NewSearchJob(searchTerms, geoLocation string, maxPages int) *SearchJob {
	return newPage(searchTerms, geoLocation, 1, maxPages)
}

func newPage(searchTerms, geoLocation string, page, maxPages int) *SearchJob {
	params := map[string]string{
		"search_terms":      searchTerms,
		"geo_location_terms": geoLocation,
	}

	if page > 1 {
		params["page"] = fmt.Sprintf("%d", page)
	}

	return &SearchJob{
		Job: scrapemate.Job{
			ID:         uuid.New().String(),
			Method:     http.MethodGet,
			URL:        baseURL,
			URLParams:  params,
			MaxRetries: 3,
			Priority:   scrapemate.PriorityMedium,
		},
		SearchTerms: searchTerms,
		GeoLocation: geoLocation,
		Page:        page,
		MaxPages:    maxPages,
	}
}

func (j *SearchJob) Process(_ context.Context, resp *scrapemate.Response) (any, []scrapemate.IJob, error) {
	defer func() {
		resp.Document = nil
		resp.Body = nil
	}()

	if resp.Error != nil {
		return nil, nil, resp.Error
	}

	doc, err := goquery.NewDocumentFromReader(bytes.NewReader(resp.Body))
	if err != nil {
		return nil, nil, fmt.Errorf("ypages: parse HTML: %w", err)
	}

	var entries []*gmaps.Entry

	doc.Find(".result").Each(func(_ int, s *goquery.Selection) {
		name := strings.TrimSpace(s.Find("a.business-name").Text())
		phone := strings.TrimSpace(s.Find(".phone").Text())
		street := strings.TrimSpace(s.Find(".street-address").Text())
		locality := strings.TrimSpace(s.Find(".locality").Text())

		if name == "" {
			return
		}

		address := street
		if locality != "" {
			if address != "" {
				address += ", " + locality
			} else {
				address = locality
			}
		}

		entries = append(entries, &gmaps.Entry{
			Title:   name,
			Phone:   phone,
			Address: address,
			Source:  "yellow_pages",
		})
	})

	var nextJobs []scrapemate.IJob

	if len(entries) > 0 && j.Page < j.MaxPages {
		nextJobs = append(nextJobs, newPage(j.SearchTerms, j.GeoLocation, j.Page+1, j.MaxPages))
	}

	return entries, nextJobs, nil
}
