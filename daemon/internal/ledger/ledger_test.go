package ledger

import (
	"context"
	"database/sql"
	"path/filepath"
	"testing"
	"time"

	_ "modernc.org/sqlite"
)

// newLedger opens a ledger on a throwaway database file.
//
// A file rather than `:memory:`, because the ledger's real home is a database
// the index also holds open with MaxOpenConns(1), and an in-memory database
// behaves differently enough about connections that a test passing on one says
// less than it looks like it does.
func newLedger(t *testing.T) *Ledger {
	t.Helper()
	dsn := "file:" + filepath.Join(t.TempDir(), "index.db") +
		"?_pragma=journal_mode(WAL)&_pragma=busy_timeout(10000)"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		t.Fatalf("opening the test database: %v", err)
	}
	db.SetMaxOpenConns(1)
	t.Cleanup(func() { db.Close() })
	l, err := Open(db)
	if err != nil {
		t.Fatalf("opening the ledger: %v", err)
	}
	return l
}

func mustRecord(t *testing.T, l *Ledger, e Entry) {
	t.Helper()
	if err := l.Record(context.Background(), e); err != nil {
		t.Fatalf("recording %s/%s: %v", e.Stage, e.Target, err)
	}
}

func seen(t *testing.T, l *Ledger, stage, target, key string) bool {
	t.Helper()
	got, err := l.Seen(context.Background(), stage, target, key)
	if err != nil {
		t.Fatalf("Seen(%s/%s): %v", stage, target, err)
	}
	return got
}

// The lookup the money depends on. A finished target answers for the content it
// was handed and for the content it produced, and for nothing else.
func TestSeenMatchesEitherKeyAndNothingElse(t *testing.T) {
	l := newLedger(t)
	mustRecord(t, l, Entry{
		Stage: StageEnrich, Target: "a.md", Version: "v1",
		InputKey: "in", OutputKey: "out", Outcome: Done,
	})

	if !seen(t, l, StageEnrich, "a.md", "in") {
		t.Error("a target was not recognised by the content the stage read")
	}
	if !seen(t, l, StageEnrich, "a.md", "out") {
		t.Error("a target was not recognised by the content the stage wrote")
	}
	if seen(t, l, StageEnrich, "a.md", "something-else") {
		t.Error("a target with different content was reported as already done")
	}
	if seen(t, l, StageEnrich, "b.md", "in") {
		t.Error("one target's row answered for another")
	}
}

// The output-key half is the one that stops the live cost leak: an enrichment
// below the confidence floor rewrites the note and leaves it `unfiled`, which is
// the status the batch queue selects on, so the note comes back round every
// cycle carrying content that is no longer what was read.
func TestARewrittenTargetIsRecognisedByWhatWasWritten(t *testing.T) {
	l := newLedger(t)
	mustRecord(t, l, Entry{
		Stage: StageEnrich, Target: "low-confidence.md", Version: "v1",
		InputKey: "raw-capture", OutputKey: "enriched-body", Outcome: Done,
	})
	// The next cycle reads the file and hashes what is there now, which is the
	// enriched body rather than the capture.
	if !seen(t, l, StageEnrich, "low-confidence.md", "enriched-body") {
		t.Fatal("a note the stage rewrote was offered as unprocessed, which is a " +
			"full-price re-run on every cycle for as long as it stays unfiled")
	}
}

// An empty key never matches. A rebuilt row has no input key, and treating "we
// do not know what was read" as a match would turn a cache rebuild into a
// corpus-wide claim that every stage is finished.
func TestAnEmptyKeyNeverMatches(t *testing.T) {
	l := newLedger(t)
	mustRecord(t, l, Entry{
		Stage: StageEnrich, Target: "rebuilt.md", Version: "v1",
		InputKey: "", OutputKey: "out", Outcome: Done,
	})
	if seen(t, l, StageEnrich, "rebuilt.md", "") {
		t.Error("an empty key matched an empty stored key; a rebuild would then " +
			"report the whole corpus as finished")
	}
}

// Only Done makes a target seen. A failure that counted as seen would never be
// retried, and this arc has already paid once for work that silently stopped
// happening.
func TestOnlyADoneRowMakesATargetSeen(t *testing.T) {
	l := newLedger(t)
	for _, tc := range []struct {
		outcome Outcome
		want    bool
	}{
		{Done, true},
		{Failed, false},
		{Skipped, false},
	} {
		t.Run(string(tc.outcome), func(t *testing.T) {
			l := l
			target := "n-" + string(tc.outcome) + ".md"
			mustRecord(t, l, Entry{
				Stage: StageEnrich, Target: target, Version: "v1",
				InputKey: "k", Outcome: tc.outcome,
			})
			if got := seen(t, l, StageEnrich, target, "k"); got != tc.want {
				t.Errorf("Seen on a %s row = %v, want %v", tc.outcome, got, tc.want)
			}
		})
	}
}

// One row per target per stage, the latest state rather than a history.
func TestRecordReplacesRatherThanAccumulating(t *testing.T) {
	l := newLedger(t)
	mustRecord(t, l, Entry{
		Stage: StageEnrich, Target: "a.md", Version: "v1",
		InputKey: "first", Outcome: Done,
	})
	mustRecord(t, l, Entry{
		Stage: StageEnrich, Target: "a.md", Version: "v2",
		InputKey: "second", Outcome: Done,
	})

	if seen(t, l, StageEnrich, "a.md", "first") {
		t.Error("the superseded row still answers; the table is accumulating history")
	}
	if !seen(t, l, StageEnrich, "a.md", "second") {
		t.Error("the replacing row does not answer")
	}
	n, err := l.Count(context.Background(), StageEnrich, "")
	if err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Errorf("two writes to one target left %d rows, want 1", n)
	}
}

// A row that does not say what happened is worse than no row: it occupies the
// target's one slot while answering nothing.
func TestRecordRefusesAnIncompleteRow(t *testing.T) {
	l := newLedger(t)
	for name, e := range map[string]Entry{
		"no outcome": {Stage: StageEnrich, Target: "a.md"},
		"no stage":   {Target: "a.md", Outcome: Done},
		"no target":  {Stage: StageEnrich, Outcome: Done},
	} {
		t.Run(name, func(t *testing.T) {
			if err := l.Record(context.Background(), e); err == nil {
				t.Errorf("a row with %s was accepted", name)
			}
		})
	}
}

// Stages do not answer for each other. Two stages processing the same note is
// the ordinary case — enrichment rewrites it, the footer pass appends to it —
// and one finishing must not report the other as finished.
func TestStagesAreIndependent(t *testing.T) {
	l := newLedger(t)
	mustRecord(t, l, Entry{
		Stage: StageEnrich, Target: "a.md", InputKey: "k", Outcome: Done,
	})
	if seen(t, l, "footer", "a.md", "k") {
		t.Error("one stage's row answered for another stage")
	}
}

// The deliberate-re-run door, and the cache loss the durability bar is written
// against.
func TestForgetAndForgetStage(t *testing.T) {
	ctx := context.Background()
	l := newLedger(t)
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "a.md", InputKey: "k", Outcome: Done})
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "b.md", InputKey: "k", Outcome: Done})
	mustRecord(t, l, Entry{Stage: "footer", Target: "a.md", InputKey: "k", Outcome: Done})

	if err := l.Forget(ctx, StageEnrich, "a.md"); err != nil {
		t.Fatal(err)
	}
	if seen(t, l, StageEnrich, "a.md", "k") {
		t.Error("a forgotten target still answers")
	}
	if !seen(t, l, StageEnrich, "b.md", "k") {
		t.Error("forgetting one target took another with it")
	}

	n, err := l.ForgetStage(ctx, StageEnrich)
	if err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Errorf("ForgetStage dropped %d rows, want 1", n)
	}
	if !seen(t, l, "footer", "a.md", "k") {
		t.Error("wiping one stage took another stage's rows with it")
	}
}

// Every field survives the round trip, the timestamp included — the digest reads
// it, and a row whose time did not come back would report every item as equally
// old.
func TestLookupRoundTripsEveryField(t *testing.T) {
	l := newLedger(t)
	// Truncated to the second because that is the resolution the column stores;
	// asserting finer would be asserting against the format rather than the row.
	at := time.Date(2026, 8, 21, 14, 30, 5, 0, time.UTC)
	want := Entry{
		Stage: StageEnrich, Target: "a.md", Version: "v1", RulesHash: "rh",
		InputKey: "in", OutputKey: "out", Outcome: Failed,
		Reason: "the judge refused it", At: at,
	}
	mustRecord(t, l, want)

	got, ok, err := l.Lookup(context.Background(), StageEnrich, "a.md")
	if err != nil || !ok {
		t.Fatalf("Lookup: ok=%v err=%v", ok, err)
	}
	if got != want {
		t.Errorf("round trip changed the row:\n got %+v\nwant %+v", got, want)
	}

	if _, ok, err := l.Lookup(context.Background(), StageEnrich, "missing.md"); ok || err != nil {
		t.Errorf("Lookup of an absent target: ok=%v err=%v, want false/nil", ok, err)
	}
}

// The digest's summary, split by version rather than folded — a stage sitting at
// two versions is a backfill in progress, and folding them hides exactly the
// state someone is checking for.
func TestStagesSummarySplitsByVersion(t *testing.T) {
	l := newLedger(t)
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "a.md", Version: "v1", Outcome: Done})
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "b.md", Version: "v2", Outcome: Done})
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "c.md", Version: "v2", Outcome: Failed})
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "d.md", Version: "v2", Outcome: Skipped})

	stats, err := l.Stages(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(stats) != 2 {
		t.Fatalf("got %d stage/version rows, want 2: %+v", len(stats), stats)
	}
	if stats[0].Version != "v1" || stats[0].Done != 1 {
		t.Errorf("v1 row wrong: %+v", stats[0])
	}
	if stats[1].Version != "v2" ||
		stats[1].Done != 1 || stats[1].Failed != 1 || stats[1].Skipped != 1 {
		t.Errorf("v2 row wrong: %+v", stats[1])
	}
}

// Count is the coverage meter's numerator, and it counts finished work only.
func TestCountCountsFinishedWorkAtAVersion(t *testing.T) {
	ctx := context.Background()
	l := newLedger(t)
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "a.md", Version: "v1", Outcome: Done})
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "b.md", Version: "v1", Outcome: Failed})
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "c.md", Version: "v2", Outcome: Done})

	if n, _ := l.Count(ctx, StageEnrich, "v1"); n != 1 {
		t.Errorf("Count at v1 = %d, want 1 (the failure is not finished work)", n)
	}
	if n, _ := l.Count(ctx, StageEnrich, ""); n != 2 {
		t.Errorf("Count across versions = %d, want 2", n)
	}
}

// Open refuses a nil handle rather than producing a ledger that panics on first
// use, which would surface as a crash in the middle of a batch that had already
// spent money.
func TestOpenRefusesANilHandle(t *testing.T) {
	if _, err := Open(nil); err == nil {
		t.Error("Open(nil) returned a ledger")
	}
}
