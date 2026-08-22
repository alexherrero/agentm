package main

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/ledger"
	"github.com/alexherrero/agentm/daemon/internal/note"
)

// The declare-a-type worked path, end to end.
//
// Everything this part built is one arc, and this is the arc: an operator edits
// `standards/storage-rules.md`, and the next cycle notices, queues the work,
// drains it under a cap without starving anything, and watches coverage come
// back. Each piece has its own tests; none of them can catch the pieces being
// right and the seam between them being wrong.
//
// Written against the real index, the real ledger, the real queue and the real
// rules holder. The only stand-in is the handler the drain calls, because what
// enrichment actually does to a note is part 4's subject and running it here
// would spend model calls in a test.

// arc is the fixture the worked path runs over: a vault, an index, and the two
// tables, all real.
type arc struct {
	vault  string
	cfg    *config.Config
	idx    *index.Index
	led    *ledger.Ledger
	queue  *ledger.Queue
	bodies map[string]string
}

func newArc(t *testing.T, notes []string, types ...string) *arc {
	t.Helper()
	vault := t.TempDir()
	cfg := configOverRules(t, vault, types...)
	a := &arc{vault: vault, cfg: cfg, bodies: map[string]string{}}

	x, err := index.Open(t.TempDir()+"/index.db", vault, "", false)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { x.Close() })
	a.idx = x

	a.led = newTestLedger(t)
	q, err := ledger.OpenQueue(x.DB())
	if err != nil {
		t.Fatal(err)
	}
	a.queue = q

	// The notes, enriched under the contract that is current now, and recorded.
	// Timestamps a minute apart so "oldest first" is a fact about the fixture
	// rather than a fact about how fast the test ran.
	at := time.Date(2026, 8, 20, 9, 0, 0, 0, time.UTC)
	for i, rel := range notes {
		body := writeNote(t, vault, rel, response(fmt.Sprintf("note %d", i), 0.8),
			enrich.Stamp{Version: enrich.PassVersion,
				RulesHash: currentRulesHash(cfg), At: at})
		a.bodies[rel] = body
		if err := x.Upsert(noteFor(rel, body), 1, int64(len(body))); err != nil {
			t.Fatal(err)
		}
		if err := a.led.Record(context.Background(), ledger.Entry{
			Stage: ledger.StageEnrich, Target: rel, Version: enrich.PassVersion,
			RulesHash: currentRulesHash(cfg),
			OutputKey: enrichFingerprint(cfg, nil).Key(body),
			Outcome:   ledger.Done, At: at.Add(time.Duration(i) * time.Minute),
		}); err != nil {
			t.Fatal(err)
		}
	}
	return a
}

func (a *arc) coverage(t *testing.T) ledger.Report {
	t.Helper()
	rep, err := pendingFor(context.Background(), ledger.StageEnrich, a.cfg,
		a.idx, a.led)
	if err != nil {
		t.Fatal(err)
	}
	return rep
}

// TestDeclaringATypeFallsThroughTheWholeArc is the part's own worked example.
func TestDeclaringATypeFallsThroughTheWholeArc(t *testing.T) {
	ctx := context.Background()
	a := newArc(t, []string{"memory/a.md", "memory/b.md", "memory/c.md"},
		"preference", "convention")

	// 1. Coverage is complete, and over a population that exists. Without the
	//    second half every number below is true of an empty corpus.
	before := a.coverage(t)
	if before.Eligible != 3 || before.Current != 3 {
		t.Fatalf("coverage starts at %d/%d, want 3/3", before.Current, before.Eligible)
	}

	// 2. The operator declares a type.
	writeRules(t, a.vault, "preference", "convention", "recipe")
	if _, err := a.cfg.Rules.Refresh(time.Now()); err != nil {
		t.Fatal(err)
	}

	// 3. Coverage falls, all of it, and the report says the contract moved
	//    rather than the notes.
	fallen := a.coverage(t)
	if fallen.Current != 0 || len(fallen.Pending) != 3 {
		t.Fatalf("after the edit coverage is %d/%d with %d pending, want 0/3 and 3",
			fallen.Current, fallen.Eligible, len(fallen.Pending))
	}
	for _, it := range fallen.Pending {
		if it.Reason != ledger.ReasonStale {
			t.Errorf("%s reads as %q; the note did not change, the contract did",
				it.Target, it.Reason)
		}
		if !strings.Contains(it.Detail, "filing contract") {
			t.Errorf("%s: Detail = %q, want the contract named", it.Target, it.Detail)
		}
	}

	// 4. The scan enqueues the work and moves on, which is the decoupling the
	//    queues exist for: discovery does not repair.
	for _, it := range fallen.Pending {
		if err := a.queue.Enqueue(ctx, "enrich", it.Target, string(it.Reason)); err != nil {
			t.Fatal(err)
		}
	}
	depth, _, err := a.queue.Depth(ctx, "enrich")
	if err != nil {
		t.Fatal(err)
	}
	if depth != 3 {
		t.Fatalf("queue depth = %d, want the 3 pending notes", depth)
	}

	// 5. It drains under a budget, oldest first, and says it deferred the rest.
	//    A cap that quietly finished and one that quietly stopped early look
	//    identical from outside, which is why the report carries it.
	var order []string
	handled := func(_ context.Context, it ledger.WorkItem) error {
		order = append(order, it.Target)
		a.reenrich(t, it.Target)
		return nil
	}
	first, err := a.queue.Drain(ctx, "enrich", 2, handled)
	if err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(order, ","); got != "memory/a.md,memory/b.md" {
		t.Errorf("the drain took %s, want the two oldest in order", got)
	}
	if !first.Deferred {
		t.Error("a drain that spent its cap with work still owed did not say so")
	}

	// 6. Coverage climbs — part way, because the drain was capped. A cycle that
	//    reported full coverage after a capped drain would be reporting work it
	//    did not do.
	midway := a.coverage(t)
	if midway.Current != 2 {
		t.Fatalf("after draining 2 of 3, coverage is %d/%d, want 2/3",
			midway.Current, midway.Eligible)
	}

	// 7. The next cycle reaches the item the last one deferred, and coverage
	//    completes. Which is not, on its own, proof that the cursor works:
	//    these two also completed, and a completed item leaves the pending page
	//    whether or not a cursor moved. Deleting the cursor entirely leaves this
	//    test green — measured, not assumed. What the cursor actually prevents
	//    is a *failing* item's queue starving behind it, and that is what the
	//    all-failures starvation test in the queue package exists for.
	order = nil
	if _, err := a.queue.Drain(ctx, "enrich", 2, handled); err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(order, ","); got != "memory/c.md" {
		t.Errorf("the second drain took %s, want the item the first one deferred", got)
	}

	after := a.coverage(t)
	if after.Current != 3 || len(after.Pending) != 0 {
		t.Fatalf("coverage ends at %d/%d with %d pending, want 3/3 and none",
			after.Current, after.Eligible, len(after.Pending))
	}
}

// reenrich stands in for what the enrichment pass does to a note: rewrite it
// under the contract running now, and record the row.
//
// The stamp is written into the note as well as into the table, because the
// ledger is a cache and the note is the durable record. A stand-in that only
// wrote the row would leave a corpus a rebuild could not recover, and the arc
// would pass over a vault that had not actually been re-enriched.
func (a *arc) reenrich(t *testing.T, rel string) {
	t.Helper()
	stamp := enrich.Stamp{Version: enrich.PassVersion,
		RulesHash: currentRulesHash(a.cfg), At: time.Now().UTC()}
	body := writeNote(t, a.vault, rel, response(rel, 0.8), stamp)
	a.bodies[rel] = body
	if err := a.idx.Upsert(noteFor(rel, body), 1, int64(len(body))); err != nil {
		t.Fatal(err)
	}
	if err := a.led.Record(context.Background(), ledger.Entry{
		Stage: ledger.StageEnrich, Target: rel, Version: stamp.Version,
		RulesHash: stamp.RulesHash,
		OutputKey: enrichFingerprint(a.cfg, nil).Key(body),
		Outcome:   ledger.Done, At: stamp.At,
	}); err != nil {
		t.Fatal(err)
	}
}

// A contract edit that changes nothing a stage reads must not requeue the
// corpus. Prose in the rules file is for people; re-enriching eight thousand
// notes because somebody fixed a typo in a heading is a real bill.
func TestEditingProseAroundTheContractDoesNotRequeue(t *testing.T) {
	a := newArc(t, []string{"memory/a.md"}, "preference", "convention")
	if got := a.coverage(t); got.Current != 1 {
		t.Fatalf("coverage starts at %d/%d, want 1/1", got.Current, got.Eligible)
	}

	appendProse(t, a.vault, "\n\nA paragraph explaining the above to a human.\n")
	if _, err := a.cfg.Rules.Refresh(time.Now()); err != nil {
		t.Fatal(err)
	}

	if got := a.coverage(t); got.Current != 1 {
		t.Errorf("prose around the block requeued the corpus: coverage %d/%d",
			got.Current, got.Eligible)
	}
}

// noteFor is the index row for a note the vault already holds.
func noteFor(rel, body string) note.Note {
	return note.Note{
		Rel: rel, Title: rel, Body: body, Status: "unfiled",
		Captured:       time.Date(2026, 8, 20, 9, 0, 0, 0, time.UTC),
		CapturedSource: "mtime",
	}
}

// appendProse adds text after the contract's fenced block, leaving what the
// block says untouched.
func appendProse(t *testing.T, vault, text string) {
	t.Helper()
	path := filepath.Join(vault, "standards", "storage-rules.md")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, append(raw, []byte(text)...), 0o644); err != nil {
		t.Fatal(err)
	}
}
