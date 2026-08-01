package webrunner

import (
	"context"
	"encoding/csv"
	"fmt"
	"os"
	"path/filepath"
	"sync"

	"github.com/gosom/google-maps-scraper/gmaps"
	"github.com/gosom/scrapemate"
)

const defaultRowLimit = 10000

type rotatingWriter struct {
	mu         sync.Mutex
	dataFolder string
	jobName    string
	rowLimit   int
	fileNum    int
	rowCount   int
	csvWriter  *csv.Writer
	file       *os.File
}

func newRotatingWriter(dataFolder, jobName string) *rotatingWriter {
	return &rotatingWriter{
		dataFolder: dataFolder,
		jobName:    jobName,
		rowLimit:   defaultRowLimit,
	}
}

func (r *rotatingWriter) openNext() error {
	if r.file != nil {
		r.csvWriter.Flush()
		_ = r.file.Close()
	}

	r.fileNum++
	fname := fmt.Sprintf("%s_%d.csv", r.jobName, r.fileNum)
	fpath := filepath.Join(r.dataFolder, fname)

	f, err := os.Create(fpath)
	if err != nil {
		return err
	}

	r.file = f
	r.csvWriter = csv.NewWriter(f)
	r.rowCount = 0

	// Write header to each new file
	_ = r.csvWriter.Write((&gmaps.Entry{}).CsvHeaders())

	return nil
}

func (r *rotatingWriter) writeEntry(entry *gmaps.Entry) error {
	r.mu.Lock()
	defer r.mu.Unlock()

	if r.file == nil {
		if err := r.openNext(); err != nil {
			return err
		}
	}

	if r.rowCount >= r.rowLimit {
		if err := r.openNext(); err != nil {
			return err
		}
	}

	if err := r.csvWriter.Write(entry.CsvRow()); err != nil {
		return err
	}

	r.rowCount++

	return nil
}

func (r *rotatingWriter) close() {
	r.mu.Lock()
	defer r.mu.Unlock()

	if r.csvWriter != nil {
		r.csvWriter.Flush()
	}

	if r.file != nil {
		_ = r.file.Close()
		r.file = nil
	}
}

// Run implements scrapemate.ResultWriter.
func (r *rotatingWriter) Run(ctx context.Context, in <-chan scrapemate.Result) error {
	defer r.close()

	for {
		select {
		case <-ctx.Done():
			return nil
		case result, ok := <-in:
			if !ok {
				return nil
			}

			switch data := result.Data.(type) {
			case *gmaps.Entry:
				_ = r.writeEntry(data)
			case []*gmaps.Entry:
				for _, e := range data {
					_ = r.writeEntry(e)
				}
			}
		}
	}
}
