package enrich

import (
	"context"
	"fmt"
	"time"
)

// The batch trigger: the same pass, over the standing queue.
//
// Eager catches a note while the asking session still exists. Batch catches
// everything eager missed — a capture during a burst, one whose model call
// failed, one written before enrichment existed at all. Between them, `unfiled`
// is a state notes pass through rather than one they accumulate in, and the
// nightly run is what makes that true.
//
// # Why this takes a lister instead of an index
//
// The queue is "every note whose status is `unfiled`", which the index can
// answer and this package has no business knowing how to ask. Passing the
// question in keeps `enrich` free of an index import — the seam matters because
// the batch runner is also what a one-shot command drives, and a command should
// not have to construct an index to enrich a file it already has.
//
// # Why it defers instead of finishing
//
// The queue is 8,407 notes. A run that tried to drain it would spend hours and
// thousands of calls in one unattended stretch, and the first thing anyone would
// want is for it to have stopped sooner. So a run takes a budget, stops when the
// budget is spent, and reports where it got to. The remainder is not lost — it
// is the next run's work, and the cursor says where that starts.

// Candidate is one note the queue offered.
type Candidate struct {
	Rel string
	Raw string
}

// Lister answers "what is still unfiled, starting after this cursor?".
//
// `after` is the last Rel a previous run finished, or empty to start at the
// beginning. Returning fewer than `limit` means the queue is exhausted.
type Lister func(ctx context.Context, after string, limit int) ([]Candidate, error)

// Writer persists an enriched note. Separate from the pass because the pass's
// job ends at "this body is good"; deciding where it goes and journalling the
// write belong to the caller that owns the vault.
type Writer func(ctx context.Context, rel, body string) error

// BatchReport is what one run did, in the numbers someone would actually ask
// for afterwards.
type BatchReport struct {
	// Considered is how many notes the queue offered.
	Considered int `json:"considered"`
	// Enriched is how many were rewritten.
	Enriched int `json:"enriched"`
	// Skipped is how many a pre-gate declined — already enriched, ineligible.
	Skipped int `json:"skipped"`
	// Failed is how many were asked about and answered badly, or errored.
	Failed int `json:"failed"`
	// Calls is the model spend.
	Calls int `json:"calls"`
	// Cursor is the last note this run finished. The next run starts after it.
	Cursor string `json:"cursor,omitempty"`
	// Deferred says the budget stopped this run before the queue ran out. It is
	// reported rather than logged because a run that quietly stopped early and
	// one that quietly finished look identical from outside.
	Deferred bool `json:"deferred"`
	// Elapsed is wall time.
	Elapsed time.Duration `json:"elapsed"`
	// Errors carries the failures verbatim, up to maxReportedErrors. Truncated,
	// because a run where everything failed should say so once rather than 8,407
	// times.
	Errors []string `json:"errors,omitempty"`

	// Pairs is what the run actually rewrote — source and result per note, kept
	// so the dispersion measurement runs over exactly what landed rather than
	// over a re-read of the vault, which would pick up anything else that
	// touched it in between.
	Pairs []Pair `json:"-"`
}

// Pair is one note's before and after.
type Pair struct {
	Rel    string
	Source string
	Result string
}

// Budget bounds one batch run.
type Budget struct {
	// MaxCalls stops the run after this many model calls. Zero means unbounded,
	// which no scheduled caller should pass.
	MaxCalls int
	// MaxDuration stops the run after this long. Zero means unbounded.
	MaxDuration time.Duration
	// PageSize is how many candidates to fetch at a time.
	PageSize int
}

// DefaultBudget is what the nightly run uses when nothing says otherwise.
//
// Deliberately small. The queue took months to accumulate and does not have to
// drain in one night; a run that is cheap enough to be boring is a run nobody
// switches off.
func DefaultBudget() Budget {
	return Budget{MaxCalls: 50, MaxDuration: 15 * time.Minute, PageSize: 25}
}

// RunBatch works the queue until the budget is spent or the queue is empty.
//
// Sequential on purpose. The concurrency limit on the eager path exists to
// survive a burst; here there is no burst to survive, and running one note at a
// time keeps the cursor meaningful — with several in flight, "where this run got
// to" stops being a single answer.
func (p *Pass) RunBatch(ctx context.Context, list Lister, write Writer,
	after string, b Budget) (BatchReport, error) {
	started := time.Now()
	rep := BatchReport{Cursor: after}

	if b.PageSize < 1 {
		b.PageSize = 25
	}
	if !p.enabled.Load() {
		rep.Elapsed = time.Since(started)
		return rep, nil
	}

	deadline := time.Time{}
	if b.MaxDuration > 0 {
		deadline = started.Add(b.MaxDuration)
	}

	for {
		if b.MaxCalls > 0 && rep.Calls >= b.MaxCalls {
			rep.Deferred = true
			break
		}
		if !deadline.IsZero() && time.Now().After(deadline) {
			rep.Deferred = true
			break
		}
		if ctx.Err() != nil {
			rep.Deferred = true
			break
		}

		page, err := list(ctx, rep.Cursor, b.PageSize)
		if err != nil {
			rep.Elapsed = time.Since(started)
			return rep, fmt.Errorf("enrich: listing the queue: %w", err)
		}
		if len(page) == 0 {
			break
		}

		for _, cand := range page {
			if b.MaxCalls > 0 && rep.Calls >= b.MaxCalls {
				rep.Deferred = true
				break
			}
			if !deadline.IsZero() && time.Now().After(deadline) {
				rep.Deferred = true
				break
			}

			rep.Considered++
			out, err := p.Run(ctx, Request{
				Rel: cand.Rel, Raw: cand.Raw, Trigger: TriggerBatch,
			})
			rep.Calls += out.Calls
			// The cursor advances past every note this run *finished*, whatever
			// the outcome. Advancing only past successes would make a note that
			// reliably fails the permanent head of the queue, and every later run
			// would spend its whole budget on it.
			rep.Cursor = cand.Rel

			switch {
			case err != nil:
				rep.Failed++
				if len(rep.Errors) < maxReportedErrors {
					rep.Errors = append(rep.Errors, fmt.Sprintf("%s: %v", cand.Rel, err))
				}
			case out.Skipped:
				rep.Skipped++
			case out.Enriched:
				if err := write(ctx, cand.Rel, out.Body); err != nil {
					rep.Failed++
					if len(rep.Errors) < maxReportedErrors {
						rep.Errors = append(rep.Errors,
							fmt.Sprintf("%s: writing: %v", cand.Rel, err))
					}
					continue
				}
				rep.Enriched++
				rep.Pairs = append(rep.Pairs, Pair{
					Rel: cand.Rel, Source: cand.Raw, Result: out.Body,
				})
			}
		}
		if rep.Deferred {
			break
		}
		if len(page) < b.PageSize {
			break
		}
	}

	rep.Elapsed = time.Since(started)
	return rep, nil
}

// maxReportedErrors bounds the failures a report carries.
//
// Twenty rather than five. The cap exists so a drain over the whole queue does
// not print a line per note, and the first bounded batch showed five is the
// wrong number for the other case: 8 notes failed and only 5 were explained, so
// three failures in a 30-note proof had no recorded reason at all.
const maxReportedErrors = 20
