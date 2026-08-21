package enrich

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// The pass, and the two moments it runs.
//
// One body of code, two triggers. **Eager** fires immediately after a capture
// transaction commits, out of band; **batch** runs inside dreaming over anything
// still `unfiled`. They are not two implementations that agree — they are the
// same implementation called twice, which is the only arrangement where "both
// triggers produce identical output" is a property rather than a hope.
//
// The trigger is visible to the pass for exactly one reason: alias vocabulary
// rules differ by it. At the eager trigger the asking session's phrasing is
// available and permitted; at the batch trigger there is no asker, so aliases
// must be derivable from the note itself. The cold scheduled backfill is banned
// outright, and that ban is measured rather than preferred — −3.85 R@5 at
// p = 0.0411 over six replicates.
//
// # What "never on the critical path" costs
//
// Capture writes the file, commits, and returns. Enrichment starts after that
// and cannot make it slower, because nothing in the capture path waits on it.
// The guarantee is structural rather than a budget: `FireEager` hands the work
// to a goroutine and returns immediately, so the only way enrichment could delay
// a capture is if it held a lock capture also wants, which it does not — it
// re-reads the note from disk rather than sharing state with the writer.
//
// A failure anywhere leaves the note exactly as capture wrote it, `unfiled`, for
// the nightly pass to pick up. That is not error handling bolted on; it is the
// reason the status exists.

// Trigger says which moment a run belongs to.
type Trigger int

const (
	// TriggerEager fires just after a capture commits, where the asking
	// session's context still exists.
	TriggerEager Trigger = iota
	// TriggerBatch runs over the standing `unfiled` queue, with no asker.
	TriggerBatch
)

func (t Trigger) String() string {
	switch t {
	case TriggerEager:
		return "eager"
	case TriggerBatch:
		return "batch"
	}
	return fmt.Sprintf("trigger(%d)", int(t))
}

// Request is one note offered to the pass.
type Request struct {
	// Rel is the vault-relative POSIX path — the note's identity.
	Rel string
	// Raw is the note's full bytes, frontmatter included.
	Raw string
	// Trigger says which moment this run belongs to.
	Trigger Trigger
	// AskerPhrasing carries the words the operator actually used, when there
	// was an operator. Only ever populated at the eager trigger, and only ever
	// read by the alias rules.
	AskerPhrasing string
}

// Outcome is what one run did, and it distinguishes three things a caller would
// otherwise have to guess at: the note changed, the note was correctly left
// alone, or something went wrong.
type Outcome struct {
	// Rel identifies the note.
	Rel string
	// Enriched is true only when the note was rewritten.
	Enriched bool
	// Skipped is true when a pre-gate declined the note — already enriched,
	// ineligible, over budget. Not a failure: a skip is the pass working.
	Skipped bool
	// Reason says why, in words meant for a human reading a log. Populated for
	// a skip and for a failure, empty for a plain success.
	Reason string
	// Body is the enriched note, present only when Enriched.
	Body string
	// Calls is how many model calls this run spent. Zero on a skip, and the
	// idempotency property is stated in exactly this number.
	Calls int
	// Elapsed is wall time for the run.
	Elapsed time.Duration
}

// ErrNotEligible is returned by a gate that declines a note. It is an outcome
// rather than an error at the boundary — Run converts it into a skip.
var ErrNotEligible = errors.New("enrich: note is not eligible")

// Gate is one deterministic check.
//
// Pre-gates run before any model call and can decline the note. Post-gates run
// on the model's output and can reject it. Both return ErrNotEligible (wrapped,
// with a reason) to decline; any other error is a genuine failure.
//
// A slice of these rather than a fixed sequence of method calls because the
// order is the specification: five before, six after, and a reader should be
// able to see the order in one place rather than reconstruct it from control
// flow.
type Gate interface {
	Name() string
	Check(ctx context.Context, req Request, body string) error
}

// Pass is the enrichment pass.
type Pass struct {
	caller *Caller
	pre    []Gate
	post   []Gate

	// types renders the contract's memory-type enum into the prompt. Supplied
	// rather than imported: the enum is whatever the rules file says at the
	// moment of the call, and this package has no business reading that file.
	types func() []string

	// enabled gates the whole pass. Off in the shipped configuration: the eager
	// trigger fires on real captures, which is real spend on the operator's
	// machine, so turning it on is a deliberate act rather than a consequence of
	// updating the binary.
	enabled atomic.Bool

	// calls counts model calls since boot, for the status surface. The
	// idempotency property this pass claims — an unchanged note at the current
	// version makes zero calls — is only checkable against a number somebody
	// keeps.
	calls atomic.Int64
	// runs, skips and failures are the same story from the other side.
	runs     atomic.Int64
	skips    atomic.Int64
	failures atomic.Int64

	// inflight bounds concurrent eager runs. A capture burst — the migration
	// rewrote 9,899 notes in an afternoon — would otherwise start one
	// subprocess per note and take the machine down. Bounded rather than
	// queued: a run that cannot start is skipped and the note stays `unfiled`,
	// which is exactly what the nightly batch pass exists to collect.
	inflight chan struct{}
	wg       sync.WaitGroup
}

// NewPass builds the pass. `concurrency` bounds simultaneous eager runs.
func NewPass(caller *Caller, concurrency int) *Pass {
	if concurrency < 1 {
		concurrency = 1
	}
	return &Pass{caller: caller, inflight: make(chan struct{}, concurrency)}
}

// SetTypes supplies the contract's memory-type enum for the prompt. Without it
// the prompt says the contract did not resolve, rather than offering nothing —
// an empty list reads to a model as "any string will do".
func (p *Pass) SetTypes(f func() []string) { p.types = f }

// SetEnabled turns the pass on. See config.EnrichEnabled for why it ships off.
func (p *Pass) SetEnabled(on bool) { p.enabled.Store(on) }

// Enabled reports whether the pass will do anything.
func (p *Pass) Enabled() bool { return p.enabled.Load() }

// AddPre and AddPost register gates in the order they run.
func (p *Pass) AddPre(g ...Gate)  { p.pre = append(p.pre, g...) }
func (p *Pass) AddPost(g ...Gate) { p.post = append(p.post, g...) }

// Stats is the counter set, for the status surface.
type Stats struct {
	Runs     int64 `json:"runs"`
	Calls    int64 `json:"calls"`
	Skips    int64 `json:"skips"`
	Failures int64 `json:"failures"`
}

// Stats reports what the pass has done since boot.
func (p *Pass) Stats() Stats {
	return Stats{
		Runs:     p.runs.Load(),
		Calls:    p.calls.Load(),
		Skips:    p.skips.Load(),
		Failures: p.failures.Load(),
	}
}

// Run performs one enrichment, synchronously.
//
// Both triggers land here. Everything that differs between them is carried in
// the Request rather than branched on at the top, so there is no second code
// path to keep in agreement with the first.
func (p *Pass) Run(ctx context.Context, req Request) (Outcome, error) {
	started := time.Now()
	out := Outcome{Rel: req.Rel}

	if !p.enabled.Load() {
		out.Skipped = true
		out.Reason = "enrichment is disabled"
		out.Elapsed = time.Since(started)
		return out, nil
	}

	p.runs.Add(1)

	// Pre-gates, in order. A decline is a skip and costs nothing; any other
	// error is a failure and the note stays exactly as capture wrote it.
	for _, g := range p.pre {
		if err := g.Check(ctx, req, req.Raw); err != nil {
			out.Elapsed = time.Since(started)
			if errors.Is(err, ErrNotEligible) {
				p.skips.Add(1)
				out.Skipped = true
				out.Reason = fmt.Sprintf("%s: %v", g.Name(), unwrapReason(err))
				return out, nil
			}
			p.failures.Add(1)
			out.Reason = fmt.Sprintf("%s failed: %v", g.Name(), err)
			return out, fmt.Errorf("enrich: pre-gate %s: %w", g.Name(), err)
		}
	}

	body, err := p.call(ctx, req)
	out.Calls = 1
	p.calls.Add(1)
	if err != nil {
		p.failures.Add(1)
		out.Elapsed = time.Since(started)
		out.Reason = err.Error()
		return out, err
	}

	// Post-gates, in order, on what the model produced. A rejection here is a
	// failure rather than a skip: the model was asked and answered badly, which
	// is a different fact from the note not being worth asking about.
	for _, g := range p.post {
		if err := g.Check(ctx, req, body); err != nil {
			p.failures.Add(1)
			out.Elapsed = time.Since(started)
			out.Reason = fmt.Sprintf("%s rejected the response: %v", g.Name(),
				unwrapReason(err))
			return out, fmt.Errorf("enrich: post-gate %s: %w", g.Name(), err)
		}
	}

	out.Enriched = true
	out.Body = body
	out.Elapsed = time.Since(started)
	return out, nil
}

// FireEager runs the pass out of band and returns immediately.
//
// This is the whole "never on the critical path" guarantee, and it is structural
// rather than budgeted: the capture path calls this and continues, so no amount
// of slowness here can show up in a capture's latency. The only way it could is
// by contending on a lock capture also holds, which is why the pass re-reads the
// note from disk instead of sharing anything with the writer.
//
// `done` is optional and exists for tests, which otherwise have no way to know
// the goroutine finished. Production passes nil.
func (p *Pass) FireEager(ctx context.Context, req Request, done func(Outcome, error)) {
	req.Trigger = TriggerEager
	if !p.enabled.Load() {
		if done != nil {
			done(Outcome{Rel: req.Rel, Skipped: true, Reason: "enrichment is disabled"}, nil)
		}
		return
	}

	select {
	case p.inflight <- struct{}{}:
	default:
		// At capacity. The note stays `unfiled` and the batch pass collects it,
		// which is the designed fallback rather than a dropped write.
		p.skips.Add(1)
		if done != nil {
			done(Outcome{
				Rel: req.Rel, Skipped: true,
				Reason: "at concurrency limit; left for the batch pass",
			}, nil)
		}
		return
	}

	p.wg.Add(1)
	go func() {
		defer p.wg.Done()
		defer func() { <-p.inflight }()
		out, err := p.Run(ctx, req)
		if done != nil {
			done(out, err)
		}
	}()
}

// Wait blocks until every in-flight eager run has finished. For shutdown and for
// tests; nothing on the capture path calls it.
func (p *Pass) Wait() { p.wg.Wait() }

// call asks the model. Split out so a test can drive the pass without one.
func (p *Pass) call(ctx context.Context, req Request) (string, error) {
	if p.caller == nil {
		return "", errors.New("enrich: no model caller configured")
	}
	var types []string
	if p.types != nil {
		types = p.types()
	}
	return p.caller.Call(ctx, BuildPrompt(req, types))
}

// unwrapReason strips the sentinel so a log line reads as a sentence rather than
// as "note is not eligible: note is not eligible: already enriched".
func unwrapReason(err error) string {
	s := err.Error()
	if i := strings.Index(s, ErrNotEligible.Error()+": "); i >= 0 {
		return s[i+len(ErrNotEligible.Error())+2:]
	}
	return s
}
