package ledger

import (
	"context"
	"errors"
	"testing"
	"time"
)

// scannerOf returns a Scanner emitting the given stamps in order.
func scannerOf(stamps ...Stamped) Scanner {
	return func(_ context.Context, emit func(Stamped) error) error {
		for _, s := range stamps {
			if err := emit(s); err != nil {
				return err
			}
		}
		return nil
	}
}

// The whole durability claim in one test: what the corpus can prove comes back,
// and what it cannot is gone rather than wrong.
func TestRebuildReplacesRowsWithWhatTheCorpusProves(t *testing.T) {
	ctx := context.Background()
	l := newLedger(t)
	at := time.Date(2026, 8, 20, 9, 0, 0, 0, time.UTC)

	// Two rows written live. One note still carries its stamp; the other's was
	// removed, which stands for a note nothing can prove was ever enriched.
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "kept.md", Version: "v1",
		RulesHash: "rh", InputKey: "in-kept", OutputKey: "out-kept", Outcome: Done, At: at})
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "unstamped.md", Version: "v1",
		InputKey: "in-unstamped", OutputKey: "out-unstamped", Outcome: Done, At: at})

	rep, err := l.Rebuild(ctx, StageEnrich, scannerOf(Stamped{
		Target: "kept.md", Version: "v1", RulesHash: "rh",
		OutputKey: "out-kept", At: at,
	}))
	if err != nil {
		t.Fatalf("Rebuild: %v", err)
	}
	if rep.Dropped != 2 || rep.Recovered != 1 {
		t.Errorf("Rebuild dropped %d and recovered %d, want 2 and 1", rep.Dropped, rep.Recovered)
	}

	// The decision is what has to survive, and it does — for the note whose
	// stamp is still there.
	if !seen(t, l, StageEnrich, "kept.md", "out-kept") {
		t.Error("a note whose stamp survived was not recovered")
	}
	// And the note nothing could prove is simply un-seen. That is the acceptable
	// loss: its work gets done again, and nothing about the note is gone.
	if seen(t, l, StageEnrich, "unstamped.md", "out-unstamped") {
		t.Error("a row with no durable stamp survived a rebuild, which means the " +
			"table is claiming work the corpus cannot account for")
	}
}

// A rebuilt row carries no input key, because the input is gone. Inventing one
// would be a claim about content nobody can check.
func TestARebuiltRowHasNoInputKey(t *testing.T) {
	ctx := context.Background()
	l := newLedger(t)
	if _, err := l.Rebuild(ctx, StageEnrich, scannerOf(Stamped{
		Target: "a.md", Version: "v1", OutputKey: "out",
	})); err != nil {
		t.Fatal(err)
	}
	got, ok, err := l.Lookup(ctx, StageEnrich, "a.md")
	if err != nil || !ok {
		t.Fatalf("Lookup: ok=%v err=%v", ok, err)
	}
	if got.InputKey != "" {
		t.Errorf("a rebuilt row claims an input key of %q", got.InputKey)
	}
	if got.Outcome != Done {
		t.Errorf("a rebuilt row is %s, want done", got.Outcome)
	}
}

// Rebuilding one stage leaves every other stage alone. A wipe that reached
// further would destroy rows the command was never asked about, and the stages
// it reached have no rebuilder to put them back.
func TestRebuildTouchesOnlyItsOwnStage(t *testing.T) {
	ctx := context.Background()
	l := newLedger(t)
	mustRecord(t, l, Entry{Stage: "footer", Target: "a.md", InputKey: "k", Outcome: Done})
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "a.md", InputKey: "k", Outcome: Done})

	if _, err := l.Rebuild(ctx, StageEnrich, scannerOf()); err != nil {
		t.Fatal(err)
	}
	if !seen(t, l, "footer", "a.md", "k") {
		t.Error("rebuilding enrich wiped another stage's rows")
	}
}

// A stage with no rebuilder is refused rather than quietly emptied. Rebuild
// wipes first, so a silent no-op here would be the command destroying the very
// rows it was asked to restore.
func TestRebuildRefusesAStageItCannotRecover(t *testing.T) {
	ctx := context.Background()
	l := newLedger(t)
	mustRecord(t, l, Entry{Stage: "footer", Target: "a.md", InputKey: "k", Outcome: Done})

	if _, err := l.Rebuild(ctx, "footer", nil); err == nil {
		t.Fatal("Rebuild with no scanner succeeded")
	}
	if !seen(t, l, "footer", "a.md", "k") {
		t.Error("the refused rebuild wiped the rows anyway")
	}
}

// Rebuilding "every stage at once" is refused for the same reason: the stages
// with no rebuilder would be wiped and the result reported as a recovery.
func TestRebuildRefusesAnUnnamedStage(t *testing.T) {
	l := newLedger(t)
	if _, err := l.Rebuild(context.Background(), "", scannerOf()); err == nil {
		t.Error("Rebuild with no stage succeeded")
	}
}

// A stamp with no target is an error rather than a dropped row, because a
// rebuilder that silently emitted nothing useful would report a recovery that
// recovered nothing.
func TestRebuildRefusesAStampWithNoTarget(t *testing.T) {
	l := newLedger(t)
	rep, err := l.Rebuild(context.Background(), StageEnrich,
		scannerOf(Stamped{Target: "", Version: "v1"}))
	if err == nil {
		t.Fatal("a stamp with no target was accepted")
	}
	// And it is not counted. The command prints "recovered N from the corpus",
	// and a count that included rows the table refused would report a recovery
	// that did not happen — the exact over-claim this table exists to prevent.
	if rep.Recovered != 0 {
		t.Errorf("Recovered = %d after a refused row, want 0", rep.Recovered)
	}
}

// A scanner that fails part way leaves the table honest about what it holds —
// the rows it did emit, and nothing invented for the rest. It must report the
// failure rather than presenting a partial rebuild as a whole one.
func TestRebuildReportsAScannerFailure(t *testing.T) {
	ctx := context.Background()
	l := newLedger(t)
	boom := errors.New("the vault went away")
	rep, err := l.Rebuild(ctx, StageEnrich, func(_ context.Context, emit func(Stamped) error) error {
		if err := emit(Stamped{Target: "a.md", Version: "v1", OutputKey: "out"}); err != nil {
			return err
		}
		return boom
	})
	if !errors.Is(err, boom) {
		t.Fatalf("Rebuild error = %v, want the scanner's own", err)
	}
	if rep.Recovered != 1 {
		t.Errorf("Recovered = %d, want 1 — the count should say what did land",
			rep.Recovered)
	}
	if !seen(t, l, StageEnrich, "a.md", "out") {
		t.Error("the row the scanner did emit was lost")
	}
}

// The rebuilt row keeps the version the stamp claimed rather than taking the
// version running now. That is what makes a note enriched by an older prompt
// come back as stale and re-enter the queue on its own.
func TestARebuiltRowKeepsTheStampedVersion(t *testing.T) {
	ctx := context.Background()
	l := newLedger(t)
	if _, err := l.Rebuild(ctx, StageEnrich, scannerOf(Stamped{
		Target: "old.md", Version: "v1", OutputKey: "out-v1",
	})); err != nil {
		t.Fatal(err)
	}

	// Asserted directly rather than only through staleness. "Reads as stale under
	// v2" is true of any version that is not v2 — a constant somebody hardcoded
	// included — so a test that checked only the staleness could not tell a
	// preserved stamp from a discarded one.
	row, ok, err := l.Lookup(ctx, StageEnrich, "old.md")
	if err != nil || !ok {
		t.Fatalf("Lookup: ok=%v err=%v", ok, err)
	}
	if row.Version != "v1" {
		t.Errorf("the rebuilt row sits at %q, want the stamped v1", row.Version)
	}

	rep, err := l.Pending(ctx, StageEnrich, Version{Stage: "v2"},
		[]Target{{Rel: "old.md", Key: "out-v1"}})
	if err != nil {
		t.Fatal(err)
	}
	if got := reasonFor(rep, "old.md"); got != ReasonStale {
		t.Errorf("a note rebuilt at v1 reads as %q under v2, want %q", got, ReasonStale)
	}
}
