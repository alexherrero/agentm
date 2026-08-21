package ledger

import (
	"context"
	"reflect"
	"testing"
	"time"
)

func pendingReport(t *testing.T, l *Ledger, version string, targets []Target) Report {
	t.Helper()
	rep, err := l.Pending(context.Background(), StageEnrich, version, targets)
	if err != nil {
		t.Fatalf("Pending: %v", err)
	}
	return rep
}

// reasonFor finds one target's reason, or empty if it is not pending.
func reasonFor(rep Report, target string) Reason {
	for _, it := range rep.Pending {
		if it.Target == target {
			return it.Reason
		}
	}
	return ""
}

// The three reasons the design names, plus the two states it does not enumerate,
// each reached by the state that produces it.
func TestPendingClassifiesEveryState(t *testing.T) {
	l := newLedger(t)
	old := time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC)

	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "current.md",
		Version: "v2", InputKey: "k-current", Outcome: Done, At: old})
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "changed.md",
		Version: "v2", InputKey: "k-was", Outcome: Done, At: old})
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "stale.md",
		Version: "v1", InputKey: "k-stale", Outcome: Done, At: old})
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "failed.md",
		Version: "v2", InputKey: "k-failed", Outcome: Failed,
		Reason: "the judge refused it", At: old})
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "skipped.md",
		Version: "v2", InputKey: "k-skipped", Outcome: Skipped,
		Reason: "privacy", At: old})

	rep := pendingReport(t, l, "v2", []Target{
		{Rel: "current.md", Key: "k-current"},
		{Rel: "changed.md", Key: "k-now"},
		{Rel: "stale.md", Key: "k-stale"},
		{Rel: "failed.md", Key: "k-failed"},
		{Rel: "skipped.md", Key: "k-skipped"},
		{Rel: "never.md", Key: "k-never"},
	})

	want := map[string]Reason{
		"current.md": "",
		"changed.md": ReasonChanged,
		"stale.md":   ReasonStale,
		"failed.md":  ReasonRetry,
		"skipped.md": ReasonSkipped,
		"never.md":   ReasonNever,
	}
	for target, wantReason := range want {
		if got := reasonFor(rep, target); got != wantReason {
			t.Errorf("%s: reason %q, want %q", target, got, wantReason)
		}
	}
	if rep.Current != 1 {
		t.Errorf("Current = %d, want 1", rep.Current)
	}
	if rep.Eligible != 6 {
		t.Errorf("Eligible = %d, want 6", rep.Eligible)
	}
	if len(rep.Pending) != 5 {
		t.Errorf("Pending = %d items, want 5", len(rep.Pending))
	}
}

// Version is checked before the key, and it has to be. A key folds the version
// in, so a row at an older version can never match a current key — reporting
// that as "changed" would blame the note for a prompt edit and hide the fact
// that the whole corpus went stale at once.
func TestAStaleRowIsStaleRatherThanChanged(t *testing.T) {
	l := newLedger(t)
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "a.md",
		Version: "v1", InputKey: "old-key", Outcome: Done})

	rep := pendingReport(t, l, "v2", []Target{{Rel: "a.md", Key: "new-key"}})
	if got := reasonFor(rep, "a.md"); got != ReasonStale {
		t.Errorf("reason %q, want %q", got, ReasonStale)
	}
	if rep.Pending[0].Version != "v1" {
		t.Errorf("the stale item does not say what it is behind: %+v", rep.Pending[0])
	}
}

// A failed row keeps the recorded reason, because "we tried and it did not work"
// is only actionable if it says what did not work.
func TestAFailedItemCarriesItsRecordedReason(t *testing.T) {
	l := newLedger(t)
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "a.md", Version: "v1",
		InputKey: "k", Outcome: Failed, Reason: "the judge refused it"})

	rep := pendingReport(t, l, "v1", []Target{{Rel: "a.md", Key: "k"}})
	if rep.Pending[0].Detail != "the judge refused it" {
		t.Errorf("the failure's reason was dropped: %+v", rep.Pending[0])
	}
}

// A row for a target the caller did not offer is not a pending item. The
// population is the caller's to define, and a coverage number computed over rows
// rather than over the eligible set would drift from anything a stage can act on.
func TestPendingReportsOnlyTheOfferedPopulation(t *testing.T) {
	l := newLedger(t)
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "gone.md",
		Version: "v1", InputKey: "k", Outcome: Done})

	rep := pendingReport(t, l, "v1", []Target{{Rel: "here.md", Key: "k2"}})
	if rep.Eligible != 1 || len(rep.Pending) != 1 {
		t.Fatalf("report covered something other than the offered set: %+v", rep)
	}
	if rep.Pending[0].Target != "here.md" {
		t.Errorf("reported %s, which was not offered", rep.Pending[0].Target)
	}
}

// The coverage meter's arithmetic, including the empty case — an empty
// population with nothing outstanding is complete coverage, and reporting zero
// would put a red number on the dashboard for a stage with nothing to be behind
// on.
func TestCoverageArithmetic(t *testing.T) {
	l := newLedger(t)
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "a.md",
		Version: "v1", InputKey: "k", Outcome: Done})

	rep := pendingReport(t, l, "v1", []Target{
		{Rel: "a.md", Key: "k"},
		{Rel: "b.md", Key: "k2"},
		{Rel: "c.md", Key: "k3"},
		{Rel: "d.md", Key: "k4"},
	})
	if got := rep.Coverage(); got != 0.25 {
		t.Errorf("Coverage = %v, want 0.25", got)
	}

	empty := pendingReport(t, l, "v1", nil)
	if got := empty.Coverage(); got != 1 {
		t.Errorf("Coverage over an empty population = %v, want 1", got)
	}
}

// The counts break the queue down by reason, so a cycle can report "forty went
// stale because the rules changed" rather than "forty pending".
func TestCountsBreakDownByReason(t *testing.T) {
	l := newLedger(t)
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "s1.md",
		Version: "v0", InputKey: "k", Outcome: Done})
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "s2.md",
		Version: "v0", InputKey: "k", Outcome: Done})

	rep := pendingReport(t, l, "v1", []Target{
		{Rel: "s1.md", Key: "k"}, {Rel: "s2.md", Key: "k"},
		{Rel: "n1.md", Key: "k"},
	})
	want := map[Reason]int{ReasonStale: 2, ReasonNever: 1}
	if !reflect.DeepEqual(rep.Counts, want) {
		t.Errorf("Counts = %v, want %v", rep.Counts, want)
	}
}

// Drain order: never-attempted first, then oldest stamp first, then by target.
//
// Never-attempted leads because it is the one state with no upper bound on how
// long it has been waiting. The tiebreak on target name is not cosmetic — a
// drain reads this order behind a cursor, and an order that is not total lets
// two runs disagree about where the cursor pointed.
func TestPendingIsOrderedForDraining(t *testing.T) {
	l := newLedger(t)
	base := time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC)
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "newest.md",
		Version: "v0", InputKey: "k", Outcome: Done, At: base.Add(48 * time.Hour)})
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "oldest.md",
		Version: "v0", InputKey: "k", Outcome: Done, At: base})
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "middle.md",
		Version: "v0", InputKey: "k", Outcome: Done, At: base.Add(24 * time.Hour)})
	// Two rows sharing a timestamp, to reach the name tiebreak.
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "tie-b.md",
		Version: "v0", InputKey: "k", Outcome: Done, At: base})
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "tie-a.md",
		Version: "v0", InputKey: "k", Outcome: Done, At: base})

	// Offered in an order deliberately unlike the answer, so a report that simply
	// echoed its input would not pass.
	targets := []Target{
		{Rel: "newest.md", Key: "k1"}, {Rel: "zz-never.md", Key: "k2"},
		{Rel: "middle.md", Key: "k3"}, {Rel: "tie-b.md", Key: "k4"},
		{Rel: "oldest.md", Key: "k5"}, {Rel: "aa-never.md", Key: "k6"},
		{Rel: "tie-a.md", Key: "k7"},
	}
	rep := pendingReport(t, l, "v1", targets)

	var got []string
	for _, it := range rep.Pending {
		got = append(got, it.Target)
	}
	want := []string{
		// Never-attempted first, among themselves by name.
		"aa-never.md", "zz-never.md",
		// Then oldest stamp first; the two sharing a stamp break by name.
		"oldest.md", "tie-a.md", "tie-b.md",
		"middle.md", "newest.md",
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("drain order:\n got %v\nwant %v", got, want)
	}
}

// The same input produces the same order every time. A drain reads this behind a
// cursor; an order that varied between runs would skip items or repeat them.
func TestPendingOrderIsStableAcrossRuns(t *testing.T) {
	l := newLedger(t)
	at := time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC)
	var targets []Target
	for _, name := range []string{"e.md", "c.md", "a.md", "d.md", "b.md"} {
		mustRecord(t, l, Entry{Stage: StageEnrich, Target: name,
			Version: "v0", InputKey: "k", Outcome: Done, At: at})
		targets = append(targets, Target{Rel: name, Key: "k-now"})
	}

	first := pendingReport(t, l, "v1", targets).Pending
	for i := 0; i < 10; i++ {
		again := pendingReport(t, l, "v1", targets).Pending
		if !reflect.DeepEqual(first, again) {
			t.Fatalf("run %d produced a different order:\n%v\n%v", i, first, again)
		}
	}
}

// The age threshold reads only items that have an age. A never-attempted target
// has none, and giving it one would fire the threshold on a brand-new population
// nothing has had a chance to touch — the false alarm that makes a threshold get
// ignored.
func TestOldestPendingIgnoresNeverAttemptedItems(t *testing.T) {
	l := newLedger(t)
	now := time.Date(2026, 8, 21, 0, 0, 0, 0, time.UTC)

	onlyNever := pendingReport(t, l, "v1", []Target{{Rel: "a.md", Key: "k"}})
	if age := onlyNever.OldestPending(now); age != 0 {
		t.Errorf("a queue of never-attempted items reported an age of %s", age)
	}

	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "b.md", Version: "v0",
		InputKey: "k", Outcome: Done, At: now.Add(-72 * time.Hour)})
	mixed := pendingReport(t, l, "v1", []Target{
		{Rel: "a.md", Key: "k"}, {Rel: "b.md", Key: "k"},
	})
	if age := mixed.OldestPending(now); age != 72*time.Hour {
		t.Errorf("OldestPending = %s, want 72h", age)
	}
}

// The guard that skips un-stamped items matters most in the order Pending never
// produces. `OldestPending` is an exported method on an exported struct whose
// Pending slice is an exported field, so a caller assembling or re-ordering a
// report reaches this — and without the guard one never-attempted item at the
// end resets the age to zero and a three-day-old queue reports as fresh.
func TestOldestPendingSurvivesANeverItemArrivingLast(t *testing.T) {
	now := time.Date(2026, 8, 21, 0, 0, 0, 0, time.UTC)
	rep := Report{Pending: []Item{
		{Target: "old.md", Reason: ReasonStale, Since: now.Add(-72 * time.Hour)},
		{Target: "never.md", Reason: ReasonNever},
	}}
	if age := rep.OldestPending(now); age != 72*time.Hour {
		t.Errorf("OldestPending = %s, want 72h — a never-attempted item at the end "+
			"reset the age and a stalled queue would report as fresh", age)
	}
}

// A target offered with no key cannot match anything, so it is pending rather
// than silently current. A caller that failed to read a note should see work,
// not a clean bill.
func TestATargetWithNoKeyIsPending(t *testing.T) {
	l := newLedger(t)
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "a.md",
		Version: "v1", InputKey: "", OutputKey: "", Outcome: Done})

	rep := pendingReport(t, l, "v1", []Target{{Rel: "a.md", Key: ""}})
	if rep.Current != 0 {
		t.Error("a target with no key was counted as current")
	}
	if got := reasonFor(rep, "a.md"); got != ReasonChanged {
		t.Errorf("reason %q, want %q", got, ReasonChanged)
	}
}

// An empty version means "do not judge staleness", which is what a stage with no
// version of its own needs. It must not silently mark every row stale.
func TestAnEmptyVersionDoesNotMakeEverythingStale(t *testing.T) {
	l := newLedger(t)
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "a.md",
		Version: "v1", InputKey: "k", Outcome: Done})

	rep := pendingReport(t, l, "", []Target{{Rel: "a.md", Key: "k"}})
	if rep.Current != 1 {
		t.Errorf("Current = %d, want 1: an unversioned query marked a matching "+
			"row stale", rep.Current)
	}
}
