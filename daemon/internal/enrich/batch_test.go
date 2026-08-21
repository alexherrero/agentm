package enrich

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"
)

// queue builds a Lister over a fixed set of notes, ordered by Rel, honouring the
// cursor the way a real index query would.
func queue(notes []Candidate) Lister {
	return func(_ context.Context, after string, limit int) ([]Candidate, error) {
		var out []Candidate
		for _, n := range notes {
			if n.Rel > after && len(out) < limit {
				out = append(out, n)
			}
		}
		return out, nil
	}
}

func fixture(n int) []Candidate {
	out := make([]Candidate, n)
	for i := range out {
		out[i] = Candidate{Rel: fmt.Sprintf("Agent/memory/n%03d.md", i), Raw: "raw"}
	}
	return out
}

// collector records what was written.
func collector() (Writer, func() []string) {
	var mu sync.Mutex
	var got []string
	w := func(_ context.Context, rel, _ string) error {
		mu.Lock()
		got = append(got, rel)
		mu.Unlock()
		return nil
	}
	return w, func() []string {
		mu.Lock()
		defer mu.Unlock()
		return append([]string(nil), got...)
	}
}

// The queue is 8,407 notes. A run that tried to drain it would spend hours in
// one unattended stretch, so the budget stops it and the cursor says where the
// next run starts. A capped run that reported itself finished would be worse
// than one that never ran.
func TestABudgetStopsTheRunAndSaysSo(t *testing.T) {
	p := passWith(t, "enriched")
	write, written := collector()

	rep, err := p.RunBatch(context.Background(), queue(fixture(20)), write, "",
		Budget{MaxCalls: 5, PageSize: 4})
	if err != nil {
		t.Fatalf("RunBatch: %v", err)
	}
	if rep.Calls != 5 {
		t.Errorf("spent %d calls against a budget of 5", rep.Calls)
	}
	if !rep.Deferred {
		t.Error("a run stopped by its budget did not report itself deferred — a " +
			"silent early stop is indistinguishable from a completed drain")
	}
	if rep.Cursor == "" {
		t.Error("a deferred run left no cursor, so the next run restarts from the top")
	}
	if n := len(written()); n != 5 {
		t.Errorf("wrote %d notes on a 5-call budget", n)
	}
}

// The next run picks up where the last stopped, and does not redo the work.
func TestTheCursorResumesWithoutRepeating(t *testing.T) {
	notes := fixture(12)
	p := passWith(t, "enriched")
	write, written := collector()

	first, err := p.RunBatch(context.Background(), queue(notes), write, "",
		Budget{MaxCalls: 5, PageSize: 4})
	if err != nil {
		t.Fatalf("first run: %v", err)
	}
	second, err := p.RunBatch(context.Background(), queue(notes), write, first.Cursor,
		Budget{MaxCalls: 5, PageSize: 4})
	if err != nil {
		t.Fatalf("second run: %v", err)
	}

	seen := map[string]int{}
	for _, rel := range written() {
		seen[rel]++
	}
	for rel, n := range seen {
		if n > 1 {
			t.Errorf("%s was enriched %d times across two runs", rel, n)
		}
	}
	if len(seen) != first.Enriched+second.Enriched {
		t.Errorf("%d distinct notes for %d+%d reported enrichments",
			len(seen), first.Enriched, second.Enriched)
	}
}

// A note that reliably fails must not become the permanent head of the queue.
// Advancing the cursor only past successes would spend every later run's whole
// budget on the same broken note.
func TestAFailingNoteDoesNotBlockTheQueueForever(t *testing.T) {
	notes := fixture(4)
	// The model fails on everything, so every note is a failure.
	p := NewPass(newStubCaller(t, stubOpts{stderr: "boom", exit: 1}), 1)
	p.SetEnabled(true)
	write, _ := collector()

	rep, err := p.RunBatch(context.Background(), queue(notes), write, "",
		Budget{MaxCalls: 10, PageSize: 10})
	if err != nil {
		t.Fatalf("RunBatch: %v", err)
	}
	if rep.Failed != 4 {
		t.Errorf("failed = %d, want 4", rep.Failed)
	}
	if rep.Cursor != notes[len(notes)-1].Rel {
		t.Errorf("cursor stuck at %q after four failures; it must advance past "+
			"every note the run finished, whatever the outcome", rep.Cursor)
	}
	// And the failures are reported, but not 8,407 of them.
	if len(rep.Errors) == 0 {
		t.Error("a run where everything failed reported no errors")
	}
	if len(rep.Errors) > 5 {
		t.Errorf("%d error strings — a run where everything fails should say so "+
			"once, not once per note", len(rep.Errors))
	}
}

// An empty queue is a clean, cheap no-op rather than a deferred run.
func TestAnEmptyQueueFinishesRatherThanDefers(t *testing.T) {
	p := passWith(t, "enriched")
	write, written := collector()

	rep, err := p.RunBatch(context.Background(), queue(nil), write, "", DefaultBudget())
	if err != nil {
		t.Fatalf("RunBatch: %v", err)
	}
	if rep.Deferred {
		t.Error("an empty queue reported a deferred run")
	}
	if rep.Considered != 0 || rep.Calls != 0 || len(written()) != 0 {
		t.Errorf("an empty queue did work: %+v", rep)
	}
}

// A drained queue finishes without deferring, even with budget to spare — the
// two conditions have to be distinguishable or nobody can tell whether the
// backlog is gone.
func TestADrainedQueueDoesNotReportDeferred(t *testing.T) {
	p := passWith(t, "enriched")
	write, _ := collector()

	rep, err := p.RunBatch(context.Background(), queue(fixture(3)), write, "",
		Budget{MaxCalls: 100, PageSize: 10})
	if err != nil {
		t.Fatalf("RunBatch: %v", err)
	}
	if rep.Deferred {
		t.Error("a fully drained queue reported itself deferred")
	}
	if rep.Enriched != 3 {
		t.Errorf("enriched %d of 3", rep.Enriched)
	}
}

// The time budget bounds a run whose notes are individually cheap but numerous.
func TestATimeBudgetStopsTheRun(t *testing.T) {
	p := NewPass(newStubCaller(t, stubOpts{
		stdout: "enriched", sleep: 300 * time.Millisecond,
	}), 1)
	p.SetEnabled(true)
	write, _ := collector()

	start := time.Now()
	rep, err := p.RunBatch(context.Background(), queue(fixture(50)), write, "",
		Budget{MaxDuration: 600 * time.Millisecond, PageSize: 50})
	if err != nil {
		t.Fatalf("RunBatch: %v", err)
	}
	if !rep.Deferred {
		t.Error("a run stopped by its time budget did not report itself deferred")
	}
	if elapsed := time.Since(start); elapsed > 3*time.Second {
		t.Errorf("the time budget took %s to stop a 600ms run", elapsed)
	}
	if rep.Considered >= 50 {
		t.Errorf("considered %d of 50 — the time budget never fired", rep.Considered)
	}
}

// A write failure is a failure, not a silent success. The pass produced a good
// body and the vault refused it; reporting that as enriched would make the queue
// look drained while the notes were untouched.
func TestAWriteFailureCountsAsAFailure(t *testing.T) {
	p := passWith(t, "enriched")
	refuse := func(context.Context, string, string) error {
		return fmt.Errorf("read-only vault")
	}

	rep, err := p.RunBatch(context.Background(), queue(fixture(3)), refuse, "",
		Budget{MaxCalls: 10, PageSize: 10})
	if err != nil {
		t.Fatalf("RunBatch: %v", err)
	}
	if rep.Enriched != 0 {
		t.Errorf("enriched = %d despite every write being refused", rep.Enriched)
	}
	if rep.Failed != 3 {
		t.Errorf("failed = %d, want 3", rep.Failed)
	}
	if len(rep.Errors) == 0 || !strings.Contains(rep.Errors[0], "read-only") {
		t.Errorf("the write failure is not in the report: %v", rep.Errors)
	}
}

// A disabled pass costs nothing here either.
func TestBatchIsOffUnlessThePassIs(t *testing.T) {
	p := NewPass(newStubCaller(t, stubOpts{stdout: "enriched"}), 1) // not enabled
	write, written := collector()

	rep, err := p.RunBatch(context.Background(), queue(fixture(5)), write, "",
		DefaultBudget())
	if err != nil {
		t.Fatalf("RunBatch: %v", err)
	}
	if rep.Calls != 0 || rep.Considered != 0 || len(written()) != 0 {
		t.Errorf("a disabled pass ran a batch: %+v", rep)
	}
}
