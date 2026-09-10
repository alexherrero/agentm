package enrich

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// A gate that records what it saw and answers however the test needs.
type stubGate struct {
	name string
	err  error
	mu   sync.Mutex
	saw  []string
}

func (g *stubGate) Name() string { return g.name }
func (g *stubGate) Check(_ context.Context, _ Request, body string) error {
	g.mu.Lock()
	g.saw = append(g.saw, body)
	g.mu.Unlock()
	return g.err
}
func (g *stubGate) calls() int {
	g.mu.Lock()
	defer g.mu.Unlock()
	return len(g.saw)
}

// passWith builds an enabled pass whose model returns a fixed body.
func passWith(t *testing.T, response string) *Pass {
	t.Helper()
	p := NewPass(newStubCaller(t, stubOpts{stdout: response}), 2)
	p.SetEnabled(true)
	return p
}

// The trigger is carried into the gates rather than branched on at the top,
// because exactly one thing ever depended on it (alias vocabulary) and a
// top-level branch would invite a second.
func TestTheTriggerReachesTheGates(t *testing.T) {
	seen := make(chan Trigger, 1)
	p := passWith(t, "body")
	p.AddPre(gateFunc("record", func(req Request) error {
		seen <- req.Trigger
		return nil
	}))

	if _, err := p.Run(context.Background(), Request{
		Rel: "x.md", Raw: "raw", Trigger: TriggerBatch,
	}); err != nil {
		t.Fatalf("run: %v", err)
	}
	if got := <-seen; got != TriggerBatch {
		t.Errorf("gate saw trigger %v, want %v", got, TriggerBatch)
	}
}

// Runs are bounded, so a pass over many notes cannot start one `claude`
// subprocess per note — the type-collapse migration rewrote 9,899 notes in an
// afternoon, and unbounded that is 9,899 live processes.
//
// The bound used to sit in the eager trigger, which is where the fan-out was.
// It sits in Run now, which is the only place left that can hold it. The batch
// runs sequentially, so this asserts the bound itself rather than a batch
// behaviour: three goroutines against a limit of one, and never more than one
// inside at a time.
func TestRunsAreBoundedByTheConcurrencyLimit(t *testing.T) {
	p := NewPass(newStubCaller(t, stubOpts{
		stdout: "body", sleep: 200 * time.Millisecond,
	}), 1)
	p.SetEnabled(true)

	// Counted from inside the bound: a pre-gate runs after the limit is taken
	// and before it is released, so it sees exactly what is in flight.
	var live, worst int32
	p.AddPre(gateFunc("count", func(Request) error {
		n := atomic.AddInt32(&live, 1)
		for {
			w := atomic.LoadInt32(&worst)
			if n <= w || atomic.CompareAndSwapInt32(&worst, w, n) {
				break
			}
		}
		time.Sleep(50 * time.Millisecond)
		atomic.AddInt32(&live, -1)
		return nil
	}))

	var wg sync.WaitGroup
	for i := 0; i < 3; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			_, _ = p.Run(context.Background(), Request{
				Rel: fmt.Sprint(i, ".md"), Raw: "raw",
			})
		}(i)
	}
	wg.Wait()

	if worst != 1 {
		t.Errorf("%d runs were inside the pass at once, past a limit of 1", worst)
	}
}

// A model failure leaves the note alone. `unfiled` is not error handling bolted
// on; it is the reason the status exists.
func TestAModelFailureLeavesTheNoteUntouched(t *testing.T) {
	p := NewPass(newStubCaller(t, stubOpts{stderr: "usage limit", exit: 1}), 1)
	p.SetEnabled(true)

	out, err := p.Run(context.Background(), Request{Rel: "x.md", Raw: "raw"})
	if err == nil {
		t.Fatal("a failed model call reported success")
	}
	if out.Enriched {
		t.Error("a failed run reported the note enriched")
	}
	if out.Body != "" {
		t.Errorf("a failed run returned a body: %q", out.Body)
	}
	if p.Stats().Failures != 1 {
		t.Errorf("failures = %d, want 1", p.Stats().Failures)
	}
}

// A pre-gate declining is a skip, and a skip costs nothing. This is the shape
// the idempotency gate will use, and the zero is the whole claim.
func TestAPreGateDeclineCostsNoModelCall(t *testing.T) {
	p := passWith(t, "body")
	p.AddPre(gateFunc("already-enriched", func(Request) error {
		return fmt.Errorf("%w: already enriched at this version", ErrNotEligible)
	}))

	out, err := p.Run(context.Background(), Request{Rel: "x.md", Raw: "raw"})
	if err != nil {
		t.Fatalf("a decline was reported as an error: %v", err)
	}
	if !out.Skipped {
		t.Error("a declined note was not marked skipped")
	}
	if out.Calls != 0 {
		t.Errorf("a declined note cost %d model calls", out.Calls)
	}
	if p.Stats().Calls != 0 {
		t.Errorf("the pass counted %d calls on a decline", p.Stats().Calls)
	}
	// And the reason reads as a sentence rather than as a doubled sentinel.
	if strings.Count(out.Reason, "not eligible") > 0 {
		t.Errorf("the reason carries the sentinel: %q", out.Reason)
	}
	if !strings.Contains(out.Reason, "already enriched") {
		t.Errorf("the reason does not say why: %q", out.Reason)
	}
}

// A post-gate rejection is a failure, not a skip. The model was asked and
// answered badly, which is a different fact from the note not being worth asking
// about — and a caller that conflates them cannot tell a broken prompt from a
// quiet corpus.
func TestAPostGateRejectionIsAFailureNotASkip(t *testing.T) {
	p := passWith(t, "body missing a token")
	p.AddPost(gateFunc("token-preservation", func(Request) error {
		return fmt.Errorf("%w: dropped `idx_timestamp_desc`", ErrNotEligible)
	}))

	out, err := p.Run(context.Background(), Request{Rel: "x.md", Raw: "raw"})
	if err == nil {
		t.Fatal("a rejected response reported success")
	}
	if out.Skipped {
		t.Error("a rejected response was counted as a skip")
	}
	if out.Enriched {
		t.Error("a rejected response was written")
	}
	if s := p.Stats(); s.Failures != 1 || s.Skips != 0 {
		t.Errorf("stats = %+v, want 1 failure and 0 skips", s)
	}
}

// Gates run in the order they were added, because the order is the
// specification — five before, six after — and a reader should see it in one
// place rather than reconstruct it from control flow.
func TestGatesRunInOrder(t *testing.T) {
	var order []string
	var mu sync.Mutex
	record := func(name string) Gate {
		return gateFunc(name, func(Request) error {
			mu.Lock()
			order = append(order, name)
			mu.Unlock()
			return nil
		})
	}
	p := passWith(t, "body")
	p.AddPre(record("pre-1"), record("pre-2"), record("pre-3"))
	p.AddPost(record("post-1"), record("post-2"))

	if _, err := p.Run(context.Background(), Request{Rel: "x.md", Raw: "raw"}); err != nil {
		t.Fatalf("run: %v", err)
	}
	want := []string{"pre-1", "pre-2", "pre-3", "post-1", "post-2"}
	if strings.Join(order, ",") != strings.Join(want, ",") {
		t.Errorf("gates ran %v, want %v", order, want)
	}
}

// Post-gates see the model's output, pre-gates see the source. Handing a
// post-gate the source would make token preservation compare the note to itself,
// which passes every time and checks nothing.
func TestPreGatesSeeTheSourceAndPostGatesSeeTheResponse(t *testing.T) {
	pre := &stubGate{name: "pre"}
	post := &stubGate{name: "post"}
	p := passWith(t, "the enriched body")
	p.AddPre(pre)
	p.AddPost(post)

	if _, err := p.Run(context.Background(), Request{Rel: "x.md", Raw: "the raw source"}); err != nil {
		t.Fatalf("run: %v", err)
	}
	if pre.calls() != 1 || pre.saw[0] != "the raw source" {
		t.Errorf("pre-gate saw %q, want the raw source", pre.saw)
	}
	if post.calls() != 1 || post.saw[0] != "the enriched body" {
		t.Errorf("post-gate saw %q, want the model's response — a post-gate handed "+
			"the source compares the note to itself and passes every time", post.saw)
	}
}

// Off unless asked for. The eager trigger fires on real captures, which is real
// spend on the operator's machine, so turning it on is a deliberate act rather
// than a consequence of updating the binary.
func TestThePassIsOffUnlessAskedFor(t *testing.T) {
	p := NewPass(newStubCaller(t, stubOpts{stdout: "body"}), 1) // not enabled

	if p.Enabled() {
		t.Fatal("a fresh pass is enabled")
	}
	out, err := p.Run(context.Background(), Request{Rel: "x.md", Raw: "raw"})
	if err != nil {
		t.Fatalf("a disabled pass errored: %v", err)
	}
	if out.Enriched || out.Calls != 0 {
		t.Errorf("a disabled pass did work: %+v", out)
	}

	if p.Stats().Calls != 0 {
		t.Errorf("a disabled pass spent %d calls", p.Stats().Calls)
	}
}

// gateFunc adapts a plain function into a Gate.
func gateFunc(name string, f func(Request) error) Gate {
	return &funcGate{name: name, f: f}
}

type funcGate struct {
	name string
	f    func(Request) error
}

func (g *funcGate) Name() string { return g.name }
func (g *funcGate) Check(_ context.Context, req Request, _ string) error {
	return g.f(req)
}
