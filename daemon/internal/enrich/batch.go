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
// job ends at "this response is good"; deciding where it goes and journalling
// the write belong to the caller that owns the vault. It gets the whole
// outcome — the response in Body, and beside it the depth the note was judged
// in and the neighbours it was shown, which composing the note needs.
type Writer func(ctx context.Context, rel string, out Outcome) error

// BatchReport is what one run did, in the numbers someone would actually ask
// for afterwards.
type BatchReport struct {
	// Considered is how many notes the queue offered.
	Considered int `json:"considered"`
	// Enriched is how many were rewritten.
	Enriched int `json:"enriched"`
	// Skipped is how many a pre-gate declined — already enriched, ineligible.
	Skipped int `json:"skipped"`
	// Refused is the part of Skipped the refusal record accounts for: cards a
	// post-gate rejected on an earlier night, offered again because a refused
	// card carries no stamp, and declined here for nothing. Counted apart from
	// the rest because it is the number that says what the record saved — two
	// model calls and about forty cents a card, every night, for as long as
	// nothing about the card or the prompt changes.
	Refused int `json:"refused"`
	// Failed is how many were asked about and answered badly, or errored.
	Failed int `json:"failed"`
	// Calls is how many notes were sent to the model — one enrichment call
	// each.
	Calls int `json:"calls"`
	// ModelCalls is every call the night made, the faithfulness judge's
	// included. It is what the call guard counts.
	ModelCalls int `json:"model_calls"`
	// Usage is what each tier spent, from the calls' own envelopes.
	Usage map[string]Usage `json:"usage,omitempty"`
	// Tokens is the night's total across tiers.
	Tokens int64 `json:"tokens"`
	// TotalCostUSD is the night's cost as the calls reported it. The field
	// name is the one the runner reads from a job's last line of output, so
	// the runner's spend line and this report read the same number.
	TotalCostUSD float64 `json:"total_cost_usd"`
	// StoppedBy says what ended the run early, in words: the call guard, a
	// tier's token line, the time limit, a run of failed calls. Empty when the
	// queue ran out first.
	StoppedBy string `json:"stopped_by,omitempty"`
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

// Pair is one note's before and after: the note as it stood, and the model's
// response. With the depth and the neighbours, the after can be composed
// exactly as the write composed it.
type Pair struct {
	Rel        string
	Source     string
	Result     string
	Depth      Depth
	Neighbours []Neighbour
}

// Budget bounds one batch run.
type Budget struct {
	// MaxCalls is the call guard: the run stops after this many model calls.
	// Zero means unbounded, which no scheduled caller should pass. With a
	// Meter it counts every call the meter saw, the judge's included; without
	// one, the notes sent.
	MaxCalls int
	// MaxDuration stops the run after this long. Zero means unbounded.
	MaxDuration time.Duration
	// PageSize is how many candidates to fetch at a time.
	PageSize int
	// TokenLines is the most each tier may spend, in tokens. A tier with no
	// line is unbounded by tokens. Read from Meter.
	TokenLines map[string]int64
	// Meter is where every call's spend is added up. Without one, the token
	// lines cannot be read and do nothing.
	Meter *Meter
	// MaxFailuresInARow stops the run after this many notes in a row whose
	// model call failed. A lapsed login or an exhausted allowance fails every
	// call the same way, and a run that kept going would spend its whole
	// window producing the same error. Zero means never.
	MaxFailuresInARow int
}

// DefaultBudget is what the nightly run uses when nothing says otherwise: the
// operator's line (usage.go), a window-sized time limit, and a short fuse on a
// model that has stopped answering.
//
// The time limit is three and a half hours so a run the runner starts at the
// window's opening ends inside it; the window is 02:00-06:00.
func DefaultBudget() Budget {
	return Budget{
		MaxCalls:          CallGuard,
		MaxDuration:       210 * time.Minute,
		PageSize:          25,
		TokenLines:        DefaultTokenLines(),
		MaxFailuresInARow: 5,
	}
}

// RunBatch works the queue until the budget is spent or the queue is empty.
//
// Sequential, and that is decided rather than inherited (agentm-vault plan 04).
// `daemon.enrich_concurrency` still bounds `Pass.Run`, but the batch does not
// fan out, for three reasons. The cursor stays one answer — "where this run got
// to" — which is what makes a deferred run resumable with `--after`. The token
// line and the call guard are read before each note, so a sequential run
// overshoots the operator's line by at most the note in flight, where N in
// flight would overshoot by N. And a steady-state night is under fifty notes,
// which one at a time finishes inside the window with hours to spare; only a
// prompt change re-owes the whole corpus, and that batch is run by hand.
func (p *Pass) RunBatch(ctx context.Context, list Lister, write Writer,
	after string, b Budget) (rep BatchReport, err error) {
	started := time.Now()
	rep = BatchReport{Cursor: after}
	// On every return, so a run that failed half way still reports what it
	// spent getting there.
	defer func() { rep.fillUsage(b.Meter) }()

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
	failedInARow := 0

	// stop says whether the budget ends the run before the note whose raw
	// bytes are given, and records why. Read before every note.
	stop := func(raw string) bool {
		calls := rep.Calls
		if b.Meter != nil {
			calls = b.Meter.Total().Calls
		}
		switch {
		case b.MaxCalls > 0 && calls >= b.MaxCalls:
			rep.StoppedBy = fmt.Sprintf("the call guard (%d calls)", b.MaxCalls)
		case raw != "" && p.overLine(raw, b):
			tier := p.routeFor(raw).Tier
			rep.StoppedBy = fmt.Sprintf("the %s-tier token line (%s tokens)",
				tier, commas(b.TokenLines[tier]))
		case !deadline.IsZero() && time.Now().After(deadline):
			rep.StoppedBy = fmt.Sprintf("the time limit (%s)", b.MaxDuration)
		case ctx.Err() != nil:
			rep.StoppedBy = "cancelled"
		case b.MaxFailuresInARow > 0 && failedInARow >= b.MaxFailuresInARow:
			rep.StoppedBy = fmt.Sprintf("%d notes in a row whose model call failed — "+
				"the model is not answering; the last error is below", failedInARow)
		default:
			return false
		}
		rep.Deferred = true
		return true
	}

	for {
		if stop("") {
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
			if stop(cand.Raw) {
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

			// A failure counts towards the fuse only when the model call itself
			// failed. A response a post-gate rejected is the model answering
			// badly, which is information about the note rather than a sign the
			// model has stopped answering at all.
			if err != nil && out.Calls > 0 && out.CallFailed {
				failedInARow++
			} else {
				failedInARow = 0
			}

			switch {
			case err != nil:
				rep.Failed++
				if len(rep.Errors) < maxReportedErrors {
					rep.Errors = append(rep.Errors, fmt.Sprintf("%s: %v", cand.Rel, err))
				}
			case out.Skipped:
				rep.Skipped++
				if out.SkippedBy == GateRefusal {
					rep.Refused++
				}
			case out.Enriched:
				if err := write(ctx, cand.Rel, out); err != nil {
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
					Depth: out.Depth, Neighbours: out.Neighbours,
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

// overLine reports whether the tier the next note routes to has spent its line.
func (p *Pass) overLine(raw string, b Budget) bool {
	if b.Meter == nil || len(b.TokenLines) == 0 {
		return false
	}
	tier := p.routeFor(raw).Tier
	line, ok := b.TokenLines[tier]
	return ok && line > 0 && b.Meter.Tier(tier).Added() >= line
}

// fillUsage copies the meter's readings into the report.
func (r *BatchReport) fillUsage(m *Meter) {
	if m == nil {
		return
	}
	r.Usage = m.ByTier()
	total := m.Total()
	r.ModelCalls = total.Calls
	r.Tokens = total.Tokens()
	r.TotalCostUSD = total.CostUSD
}

// maxReportedErrors bounds the failures a report carries.
//
// Twenty rather than five. The cap exists so a drain over the whole queue does
// not print a line per note, and the first bounded batch showed five is the
// wrong number for the other case: 8 notes failed and only 5 were explained, so
// three failures in a 30-note proof had no recorded reason at all.
const maxReportedErrors = 20
