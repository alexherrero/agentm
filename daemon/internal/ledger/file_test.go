package ledger

import (
	"context"
	"database/sql"
	"path/filepath"
	"reflect"
	"testing"
	"time"
)

// allRows reads a ledger's whole table, in a stable order, for comparisons
// that have to be about every row rather than the ones a test thought of.
func allRows(t *testing.T, l *Ledger) []Entry {
	t.Helper()
	rows, err := l.db.Query(`SELECT stage, target, version, rules_hash, input_key,
		output_key, outcome, reason, at FROM ledger ORDER BY stage, target`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var out []Entry
	for rows.Next() {
		e, err := scanEntry(rows)
		if err != nil {
			t.Fatal(err)
		}
		out = append(out, e)
	}
	return out
}

// The ledger's own file keeps its rows across a close and a reopen, and leaves
// no journal beside it once closed: the engine state directory is a git
// repository the runner commits, and a sidecar would be churn in it.
func TestOpenFileKeepsItsRowsAndLeavesNoSidecar(t *testing.T) {
	path := filepath.Join(t.TempDir(), FileName)
	l, err := OpenFile(path)
	if err != nil {
		t.Fatal(err)
	}
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "a.md", Version: "v1",
		RulesHash: "r1", InputKey: "in", OutputKey: "out", Outcome: Done,
		At: time.Date(2026, 9, 30, 9, 0, 0, 0, time.UTC)})
	before := allRows(t, l)
	if err := l.Close(); err != nil {
		t.Fatal(err)
	}
	for _, side := range []string{"-journal", "-wal", "-shm"} {
		if matches, _ := filepath.Glob(path + side); len(matches) > 0 {
			t.Errorf("a closed ledger left %s beside it", matches[0])
		}
	}

	l, err = OpenFile(path)
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	if after := allRows(t, l); !reflect.DeepEqual(before, after) {
		t.Errorf("rows changed across a reopen:\nbefore %+v\nafter  %+v", before, after)
	}
}

// The move out of the index carries every row exactly as it stood, its time
// included, and reports how many.
func TestCarryFromCopiesEveryRowVerbatim(t *testing.T) {
	src := newLedger(t)
	mustRecord(t, src, Entry{Stage: StageEnrich, Target: "a.md", Version: "v1",
		RulesHash: "r1", InputKey: "in", OutputKey: "out", Outcome: Done,
		At: time.Date(2026, 9, 30, 9, 0, 0, 0, time.UTC)})
	mustRecord(t, src, Entry{Stage: StageEnrich, Target: "b.md", Version: "v1",
		InputKey: "k", Outcome: Failed, Reason: "the call timed out",
		At: time.Date(2026, 10, 1, 9, 5, 0, 0, time.UTC)})

	dst, err := OpenFile(filepath.Join(t.TempDir(), FileName))
	if err != nil {
		t.Fatal(err)
	}
	defer dst.Close()
	n, err := dst.CarryFrom(context.Background(), src.db)
	if err != nil {
		t.Fatal(err)
	}
	if n != 2 {
		t.Errorf("carried %d row(s), want 2", n)
	}
	if got, want := allRows(t, dst), allRows(t, src); !reflect.DeepEqual(got, want) {
		t.Errorf("the carried rows differ:\ngot  %+v\nwant %+v", got, want)
	}
}

// A database that never held a ledger carries nothing, without error: that is
// a machine whose index never had the table.
func TestCarryFromADatabaseWithNoLedgerCarriesNothing(t *testing.T) {
	db, err := sql.Open("sqlite", "file:"+filepath.Join(t.TempDir(), "x.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	dst, err := OpenFile(filepath.Join(t.TempDir(), FileName))
	if err != nil {
		t.Fatal(err)
	}
	defer dst.Close()
	if n, err := dst.CarryFrom(context.Background(), db); n != 0 || err != nil {
		t.Errorf("CarryFrom = %d, %v; want 0, nil", n, err)
	}
}

// --- following a move -------------------------------------------------------

// fakeVault is a population of paths and their content, with the key function
// the follow reads: content plus the version and contract, as the real key is.
type fakeVault map[string]string

func (v fakeVault) exists(rel string) bool { _, ok := v[rel]; return ok }

func (v fakeVault) keyAt(rel, version, rules string) (string, bool) {
	body, ok := v[rel]
	if !ok {
		return "", false
	}
	return version + "|" + rules + "|" + body, true
}

func (v fakeVault) paths() []string {
	var out []string
	for rel := range v {
		out = append(out, rel)
	}
	return out
}

func follow(t *testing.T, l *Ledger, v fakeVault) int {
	t.Helper()
	n, err := l.Follow(context.Background(), StageEnrich, v.paths(), v.exists, v.keyAt)
	if err != nil {
		t.Fatal(err)
	}
	return n
}

// A note that moved with its body takes its row to the new path, with its
// time, so it reads as judged rather than as never judged.
func TestAMovedNoteTakesItsRow(t *testing.T) {
	l := newLedger(t)
	judged := time.Date(2026, 9, 30, 9, 0, 0, 0, time.UTC)
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "Agent/memory/a.md", // root-casing: the old side of a case-only rename
		Version: "v1", RulesHash: "r1", OutputKey: "v1|r1|the body",
		Outcome: Done, At: judged})
	v := fakeVault{"agent/memory/a.md": "the body"}

	if n := follow(t, l, v); n != 1 {
		t.Fatalf("followed %d row(s), want 1", n)
	}
	got, ok, err := l.Lookup(context.Background(), StageEnrich, "agent/memory/a.md")
	if err != nil || !ok || got.Outcome != Done || !got.At.Equal(judged) {
		t.Errorf("the moved note's row = %+v (found %v, %v)", got, ok, err)
	}
	if _, ok, _ := l.Lookup(context.Background(), StageEnrich, "Agent/memory/a.md"); ok { // root-casing: the old side
		t.Error("the row also stayed at the old path")
	}
}

// A row from an older pass or contract is matched by the key it stores, so a
// moved note that is stale stays stale at its new path rather than becoming
// never judged, and keeps the time the pending order reads.
func TestAMovedStaleNoteTakesItsRowUnderItsOwnVersion(t *testing.T) {
	l := newLedger(t)
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "old/a.md", Version: "v1",
		RulesHash: "r0", OutputKey: "v1|r0|the body", Outcome: Done,
		At: time.Date(2026, 9, 20, 9, 0, 0, 0, time.UTC)})
	v := fakeVault{"new/a.md": "the body"}
	follow(t, l, v)

	rep, err := l.Pending(context.Background(), StageEnrich,
		Version{Stage: "v1", Rules: "r1"}, []Target{{Rel: "new/a.md", Key: "v1|r1|the body"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.Pending) != 1 || rep.Pending[0].Reason != ReasonStale {
		t.Errorf("the moved stale note reads as %+v, want stale", rep.Pending)
	}
}

// A body at two paths is a copy rather than a move. Pairing it would pick one
// at random, so neither gets the row.
func TestABodyAtTwoPathsIsNotFollowed(t *testing.T) {
	l := newLedger(t)
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "gone.md", Version: "v1",
		RulesHash: "r1", OutputKey: "v1|r1|twin", Outcome: Done})
	v := fakeVault{"one.md": "twin", "two.md": "twin"}
	if n := follow(t, l, v); n != 0 {
		t.Errorf("followed %d row(s) into a pair of twins, want 0", n)
	}
}

// A note that still exists keeps its row, and a deleted note's row is left
// alone when nothing carries its body.
func TestOnlyAVanishedPathIsFollowed(t *testing.T) {
	l := newLedger(t)
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "kept.md", Version: "v1",
		RulesHash: "r1", OutputKey: "v1|r1|same", Outcome: Done})
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "deleted.md", Version: "v1",
		RulesHash: "r1", OutputKey: "v1|r1|gone", Outcome: Done})
	// A copy of kept.md's body elsewhere is a copy, not kept.md moving.
	v := fakeVault{"kept.md": "same", "copy.md": "same", "other.md": "unrelated"}
	if n := follow(t, l, v); n != 0 {
		t.Errorf("followed %d row(s), want 0", n)
	}
	if _, ok, _ := l.Lookup(context.Background(), StageEnrich, "deleted.md"); !ok {
		t.Error("a deleted note's row was dropped by the follow")
	}
}

// The night may meet the moved note before the follow does — a budget
// deferral at its new path. The finished row it is missing replaces that.
func TestAFinishedRowReplacesASkipAtTheNewPath(t *testing.T) {
	l := newLedger(t)
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "old.md", Version: "v1",
		RulesHash: "r1", OutputKey: "v1|r1|body", Outcome: Done})
	mustRecord(t, l, Entry{Stage: StageEnrich, Target: "new.md", Version: "v1",
		RulesHash: "r1", InputKey: "v1|r1|body", Outcome: Skipped,
		Reason: "budget: deferred"})
	v := fakeVault{"new.md": "body"}
	if n := follow(t, l, v); n != 1 {
		t.Fatalf("followed %d row(s), want 1", n)
	}
	if !seen(t, l, StageEnrich, "new.md", "v1|r1|body") {
		t.Error("the moved note is not seen at its new path")
	}
}
