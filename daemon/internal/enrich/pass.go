package enrich

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync/atomic"
	"time"
)

// The pass, and the one moment it runs.
//
// **Batch**: a nightly run over the notes that are owed a pass, in one place,
// under a budget, with a report. There used to be a second trigger — eager,
// fired just after a capture committed — and it retires here. It spent a model
// call per note as the note landed, it was never attached to a running daemon,
// and it never fired once.
//
// The alias rule is what the trigger used to be visible for: an eager run had
// an asker whose phrasing counted as evidence, and a batch run has nobody, so
// an alias must be derivable from the note itself. Only the second half is
// left, which is the half that was measured. The cold scheduled backfill — a
// pass over the whole corpus writing aliases with no note-level trigger at all
// — stays banned outright, measured rather than preferred: −3.85 R@5 at
// p = 0.0411 over six replicates.
//
// # Nothing waits on this
//
// Capture writes the file, commits, and returns, and enrichment is not on that
// path at all any more. A failure anywhere leaves the note exactly as capture
// wrote it, `unfiled`, for the next night to pick up. That is not error
// handling bolted on; it is the reason the status exists.

// Trigger says which moment a run belongs to. One value, kept as a named type
// rather than dropped: the journal records it on every write, and a row that
// says which pass wrote it is worth more than the field costs.
type Trigger int

const (
	// TriggerBatch runs over the standing queue, with no asker. The zero
	// value, so a Request built without one cannot claim a trigger that does
	// not exist.
	TriggerBatch Trigger = iota
)

func (t Trigger) String() string {
	switch t {
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
	// Depth is how much of the pass this note is owed, read from its stamp by
	// PassDepth. Set by the pass itself just before the gates run, so a caller
	// cannot ask for a deep pass over a note that has already had one — the
	// note's own frontmatter decides, and it is the only thing that does.
	Depth Depth
	// Route is the model and tier the tier table chose for this depth. Set by
	// the pass beside Depth, for the same reason.
	Route Route
	// Neighbours are the notes the prompt offers beside the card — the only
	// ids `related` may name. Set by the pass once the pre-gates agree the note
	// is worth a call, so a skipped note costs no search.
	Neighbours []Neighbour
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
	// CallFailed is true when the enrichment call itself failed — the model
	// did not answer — as distinct from answering and being rejected by a
	// post-gate. The batch's fuse counts only these.
	CallFailed bool
	// Neighbours are what the prompt offered, carried to the write path so it
	// keeps only `related` ids from this list.
	Neighbours []Neighbour
	// Depth is the shape the note was judged in.
	Depth Depth
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

	// enabled gates the whole pass. Off in the shipped configuration, because
	// a pass that runs spends on the operator's machine, and turning that on is
	// a deliberate act rather than a consequence of updating the binary.
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

	// observer, when set, is told what happened to every note the pass ran —
	// both triggers, every outcome. It exists so the coverage ledger can be
	// written without this package knowing a ledger exists: the pass reports,
	// and whoever owns the record decides what to keep.
	//
	// It deliberately receives the Request as well as the Outcome. An observer
	// that only saw the Outcome could not compute the key for the content that
	// was read, and "what did this stage process" is the whole question a
	// ledger row answers.
	observer func(Request, Outcome, error)

	// inflight bounds concurrent runs, so a batch cannot start one subprocess
	// per note and take the machine down — the type-collapse migration rewrote
	// 9,899 notes in an afternoon. Bounded rather than queued: a run that
	// cannot start is skipped and the note keeps waiting, which is what the
	// next night exists to collect.
	inflight chan struct{}

	// router asks the tier table where a note of this depth runs. Nil keeps
	// the caller's own model on the strong tier.
	router func(Depth) Route

	// neighbours finds the notes to offer beside a card, and rubric reads the
	// contract's importance paragraph. Both supplied, for the same reason the
	// type enum is: this package has no business opening the index or the
	// rules file.
	neighbours func(context.Context, Request) []Neighbour
	rubric     func() string
}

// SetNeighbours supplies the search the deep pass reads beside a card.
func (p *Pass) SetNeighbours(f func(context.Context, Request) []Neighbour) { p.neighbours = f }

// SetRubric supplies the contract's importance rubric for the prompt.
func (p *Pass) SetRubric(f func() string) { p.rubric = f }

// SetRouter supplies the tier table's answer for each depth: the deep pass
// and the light pass may run on different tiers, and the table decides which.
func (p *Pass) SetRouter(f func(Depth) Route) { p.router = f }

// routeFor is where a note with these bytes would run.
func (p *Pass) routeFor(raw string) Route {
	if p.router == nil {
		model := ""
		if p.caller != nil {
			model = p.caller.Model
		}
		return Route{Model: model, Tier: TierStrong}
	}
	r := p.router(PassDepth(raw))
	if r.Tier == "" {
		r.Tier = TierStrong
	}
	return r
}

// NewPass builds the pass. `concurrency` bounds simultaneous runs.
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

// SetObserver registers a callback told the result of every run.
//
// One observer rather than a list. Two would raise the question of what happens
// when the first one fails, and the only caller is the thing that writes the
// coverage ledger.
func (p *Pass) SetObserver(f func(Request, Outcome, error)) { p.observer = f }

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
	out, err := p.run(ctx, req)
	// Fired after the run rather than at each exit, so there is one place the
	// observer is called from and no way for a future return path to forget it.
	if p.observer != nil {
		p.observer(req, out, err)
	}
	return out, err
}

func (p *Pass) run(ctx context.Context, req Request) (Outcome, error) {
	started := time.Now()
	out := Outcome{Rel: req.Rel}

	if !p.enabled.Load() {
		out.Skipped = true
		out.Reason = "enrichment is disabled"
		out.Elapsed = time.Since(started)
		return out, nil
	}

	// What this note is owed, from its own stamp rather than from the caller,
	// and where the tier table says that depth runs.
	req.Depth = PassDepth(req.Raw)
	req.Route = p.routeFor(req.Raw)
	out.Depth = req.Depth

	// The concurrency bound, taken here rather than at a fan-out helper. It
	// used to sit in the eager trigger, which is where the fan-out was; with
	// that gone, a bound anywhere else would be a config key with no reader.
	// The batch runs one note at a time, so today this never waits — what it
	// does is make `daemon.enrich_concurrency` true for whoever runs notes in
	// parallel next.
	p.inflight <- struct{}{}
	defer func() { <-p.inflight }()

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

	// The neighbours, once every free gate has agreed: a search is cheap, but
	// it is not free, and a skipped note should cost nothing at all. Set on the
	// request so the post-gates and the write path see the same list the model
	// was shown.
	if p.neighbours != nil {
		req.Neighbours = p.neighbours(ctx, req)
	}
	out.Neighbours = req.Neighbours

	body, err := p.call(ctx, req)
	out.Calls = 1
	p.calls.Add(1)
	if err != nil {
		p.failures.Add(1)
		out.Elapsed = time.Since(started)
		out.Reason = err.Error()
		out.CallFailed = true
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

// call asks the model. Split out so a test can drive the pass without one.
func (p *Pass) call(ctx context.Context, req Request) (string, error) {
	if p.caller == nil {
		return "", errors.New("enrich: no model caller configured")
	}
	var types []string
	if p.types != nil {
		types = p.types()
	}
	rubric := ""
	if p.rubric != nil {
		rubric = p.rubric()
	}
	c := p.caller.With(req.Route, req.Rel+" · "+req.Depth.String())
	return c.Call(ctx, BuildPrompt(req, types, rubric))
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
