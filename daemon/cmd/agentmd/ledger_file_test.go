package main

import (
	"bytes"
	"context"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/ledger"
	"github.com/alexherrero/agentm/daemon/internal/note"
)

// The ledger in its own file (task 181 step 2, #783), through the shipped
// opener over a real index and vault on disk.

// ledgerFixture is a vault with one judged note, an index over it and a config
// whose engine state directory is the test's own.
type ledgerFixture struct {
	cfg   *config.Config
	vault string
	rel   string
	body  string
}

func newLedgerFixture(t *testing.T) ledgerFixture {
	t.Helper()
	vault := t.TempDir()
	cfg := configOverRules(t, vault, "fact")
	cfg.IndexPath = filepath.Join(t.TempDir(), "index.db")
	cfg.EngineStateDir = t.TempDir()
	rel := "memory/semantic/judged.md"
	body := writeNote(t, vault, rel, response("judged", 0.9), enrich.Stamp{
		Version: enrich.PassVersion, RulesHash: currentJudgmentHash(cfg),
		At: time.Date(2026, 9, 30, 9, 0, 0, 0, time.UTC),
	})
	return ledgerFixture{cfg: cfg, vault: vault, rel: rel, body: body}
}

// openIndex opens the fixture's index and indexes its note, as the daemon's
// reconcile would after a fresh start.
func (f ledgerFixture) openIndex(t *testing.T) *index.Index {
	t.Helper()
	x, err := index.Open(f.cfg.IndexPath, f.vault, "", false)
	if err != nil {
		t.Fatal(err)
	}
	if err := x.Upsert(note.Note{
		Rel: f.rel, Title: "judged", Body: f.body, Status: "active",
		Captured: time.Date(2026, 8, 20, 9, 0, 0, 0, time.UTC), CapturedSource: "mtime",
	}, 1, int64(len(f.body))); err != nil {
		t.Fatal(err)
	}
	return x
}

// ledgerRows reads the ledger file's whole table, so a comparison is about
// every row rather than the ones a test thought to check.
func ledgerRows(t *testing.T, path string) [][]string {
	t.Helper()
	db, err := sql.Open("sqlite", "file:"+path+"?mode=ro")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	rows, err := db.Query(`SELECT stage, target, version, rules_hash, input_key,
		output_key, outcome, reason, at FROM ledger ORDER BY stage, target`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var out [][]string
	for rows.Next() {
		r := make([]string, 9)
		ptrs := make([]any, 9)
		for i := range r {
			ptrs[i] = &r[i]
		}
		if err := rows.Scan(ptrs...); err != nil {
			t.Fatal(err)
		}
		out = append(out, r)
	}
	return out
}

// The verification the plan names: force a schema bump, and every ledger row
// is unchanged and the next pending query is identical.
func TestASchemaBumpLeavesTheLedgerUntouched(t *testing.T) {
	ctx := context.Background()
	f := newLedgerFixture(t)
	idx := f.openIndex(t)
	led, err := openLedger(ctx, f.cfg, idx, &bytes.Buffer{})
	if err != nil {
		t.Fatal(err)
	}
	keyer := enrichFingerprint(f.cfg, nil)
	// A judgment the stamps cannot reproduce: the input key, which is what a
	// rebuild loses.
	if err := led.Record(ctx, ledger.Entry{
		Stage: ledger.StageEnrich, Target: f.rel, Version: enrich.PassVersion,
		RulesHash: currentJudgmentHash(f.cfg), InputKey: keyer.Key("the raw capture"),
		OutputKey: keyer.Key(f.body), Outcome: ledger.Done,
		At: time.Date(2026, 10, 1, 9, 0, 0, 0, time.UTC),
	}); err != nil {
		t.Fatal(err)
	}
	before := ledgerRows(t, ledgerPath(f.cfg))
	pendingBefore, err := pendingFor(ctx, ledger.StageEnrich, f.cfg, idx, led)
	if err != nil {
		t.Fatal(err)
	}
	if pendingBefore.Eligible != 1 {
		t.Fatalf("eligible %d, want the one note; the comparison below would be "+
			"vacuous over an empty queue", pendingBefore.Eligible)
	}
	led.Close()

	// The bump: the index finds a schema version that is not its own and
	// discards itself on the next open.
	if _, err := idx.DB().Exec(`UPDATE meta SET value = 'stale' WHERE key = 'schema_version'`); err != nil {
		t.Fatal(err)
	}
	idx.Close()
	idx = f.openIndex(t)
	defer idx.Close()
	var docs int
	if err := idx.DB().QueryRow(`SELECT count(*) FROM docmeta`).Scan(&docs); err != nil || docs != 1 {
		t.Fatalf("after the bump the index holds %d documents (%v); the fixture "+
			"did not rebuild it", docs, err)
	}

	log := &bytes.Buffer{}
	led, err = openLedger(ctx, f.cfg, idx, log)
	if err != nil {
		t.Fatal(err)
	}
	defer led.Close()
	if after := ledgerRows(t, ledgerPath(f.cfg)); !reflect.DeepEqual(before, after) {
		t.Errorf("a schema bump changed the ledger:\nbefore %q\nafter  %q", before, after)
	}
	if log.Len() != 0 {
		t.Errorf("reopening an existing ledger said %q; it should say nothing", log)
	}
	pendingAfter, err := pendingFor(ctx, ledger.StageEnrich, f.cfg, idx, led)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(pendingBefore, pendingAfter) {
		t.Errorf("the pending report moved across a bump:\nbefore %+v\nafter  %+v",
			pendingBefore, pendingAfter)
	}
}

// The first open moves the index's rows out, verbatim, says so, and drops the
// index's table so no second copy goes stale beside the file.
func TestTheFirstOpenMovesTheIndexRowsOut(t *testing.T) {
	ctx := context.Background()
	f := newLedgerFixture(t)
	idx := f.openIndex(t)
	defer idx.Close()
	old, err := ledger.Open(idx.DB())
	if err != nil {
		t.Fatal(err)
	}
	if err := old.Record(ctx, ledger.Entry{
		Stage: ledger.StageEnrich, Target: f.rel, Version: enrich.PassVersion,
		RulesHash: "rh", InputKey: "in", OutputKey: "out", Outcome: ledger.Done,
		At: time.Date(2026, 9, 30, 9, 0, 0, 0, time.UTC),
	}); err != nil {
		t.Fatal(err)
	}

	log := &bytes.Buffer{}
	led, err := openLedger(ctx, f.cfg, idx, log)
	if err != nil {
		t.Fatal(err)
	}
	defer led.Close()
	if !strings.Contains(log.String(), "moved 1 row(s) out of the index") {
		t.Errorf("the move said %q", log)
	}
	got, ok, err := led.Lookup(ctx, ledger.StageEnrich, f.rel)
	if err != nil || !ok || got.InputKey != "in" || got.OutputKey != "out" ||
		got.RulesHash != "rh" || !got.At.Equal(time.Date(2026, 9, 30, 9, 0, 0, 0, time.UTC)) {
		t.Errorf("the carried row = %+v (found %v, %v)", got, ok, err)
	}
	var n int
	if err := idx.DB().QueryRow(`SELECT count(*) FROM sqlite_master
		WHERE type = 'table' AND name = 'ledger'`).Scan(&n); err != nil || n != 0 {
		t.Errorf("the index still holds a ledger table (%d, %v)", n, err)
	}
	if matches, _ := filepath.Glob(ledgerPath(f.cfg) + ".new-*"); len(matches) > 0 {
		t.Errorf("the move left %v behind", matches)
	}
}

// A lost ledger file is rebuilt from the notes' stamps, and the rebuild says
// so: it has lost every input key, and the night after re-judges more.
func TestALostLedgerFileIsRebuiltFromTheStampsAndSaysSo(t *testing.T) {
	ctx := context.Background()
	f := newLedgerFixture(t)
	idx := f.openIndex(t)
	defer idx.Close()
	led, err := openLedger(ctx, f.cfg, idx, &bytes.Buffer{})
	if err != nil {
		t.Fatal(err)
	}
	led.Close()
	if err := os.Remove(ledgerPath(f.cfg)); err != nil {
		t.Fatal(err)
	}

	log := &bytes.Buffer{}
	led, err = openLedger(ctx, f.cfg, idx, log)
	if err != nil {
		t.Fatal(err)
	}
	defer led.Close()
	if !strings.Contains(log.String(), "was missing, so it was rebuilt from the notes' stamps: 1 row(s)") {
		t.Errorf("the rebuild said %q", log)
	}
	if !seenBy(t, led, f.rel, enrichFingerprint(f.cfg, nil).Key(f.body)) {
		t.Error("the rebuilt ledger does not see the stamped note")
	}
}

func seenBy(t *testing.T, led *ledger.Ledger, rel, key string) bool {
	t.Helper()
	ok, err := led.Seen(context.Background(), ledger.StageEnrich, rel, key)
	if err != nil {
		t.Fatal(err)
	}
	return ok
}

// A note the night moved keeps its row: the pending report reads it as
// current at its new path, not as never judged.
func TestAMovedNoteIsCurrentAtItsNewPath(t *testing.T) {
	ctx := context.Background()
	f := newLedgerFixture(t)
	idx := f.openIndex(t)
	defer idx.Close()
	led, err := openLedger(ctx, f.cfg, idx, &bytes.Buffer{})
	if err != nil {
		t.Fatal(err)
	}
	defer led.Close()

	moved := "memory/semantic/judged-renamed.md"
	if err := os.Rename(filepath.Join(f.vault, filepath.FromSlash(f.rel)),
		filepath.Join(f.vault, filepath.FromSlash(moved))); err != nil {
		t.Fatal(err)
	}
	if err := idx.Delete(f.rel); err != nil {
		t.Fatal(err)
	}
	if err := idx.Upsert(note.Note{
		Rel: moved, Title: "judged", Body: f.body, Status: "active",
		Captured: time.Date(2026, 8, 20, 9, 0, 0, 0, time.UTC), CapturedSource: "mtime",
	}, 1, int64(len(f.body))); err != nil {
		t.Fatal(err)
	}

	rep, err := pendingFor(ctx, ledger.StageEnrich, f.cfg, idx, led)
	if err != nil {
		t.Fatal(err)
	}
	if rep.Eligible != 1 || rep.Current != 1 {
		t.Errorf("after the move: eligible %d · current %d · pending %+v; want "+
			"the moved note current", rep.Eligible, rep.Current, rep.Pending)
	}
}

// The night's order (task 181 step 4): the notes owed by cause, never judged in
// the tiers' own order, the rest least recently judged first, and the notes the
// night is current on after all of them.
func TestTheNightTakesNotesByCause(t *testing.T) {
	day := func(d int) time.Time { return time.Date(2026, 9, d, 9, 0, 0, 0, time.UTC) }
	// The tiers' order, as enrichServeOrder gives it: inbox, cards, records.
	queue := []string{"inbox/new.md", "cards/current.md", "cards/never.md",
		"cards/judgment-new.md", "cards/judgment-old.md", "cards/changed.md",
		"records/deep.md", "records/never.md"}
	causes := map[string]ledger.Cause{
		"inbox/new.md":          ledger.CauseNever,
		"cards/never.md":        ledger.CauseNever,
		"records/never.md":      ledger.CauseNever,
		"cards/changed.md":      ledger.CauseChanged,
		"records/deep.md":       ledger.CausePass,
		"cards/judgment-new.md": ledger.CauseJudgment,
		"cards/judgment-old.md": ledger.CauseJudgment,
	}
	since := map[string]time.Time{
		"cards/changed.md": day(20), "records/deep.md": day(25),
		"cards/judgment-new.md": day(29), "cards/judgment-old.md": day(2),
	}
	got := orderByCause(queue, causes, since)
	want := []string{"inbox/new.md", "cards/never.md", "records/never.md",
		"cards/changed.md", "records/deep.md", "cards/judgment-old.md",
		"cards/judgment-new.md", "cards/current.md"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("order = %v\nwant    %v", got, want)
	}
	if len(got) != len(queue) {
		t.Errorf("the order dropped or doubled a note: %d of %d", len(got), len(queue))
	}
}

// Two contract edits in a row, each to a field a judgment reads, through the
// batch's own serve order: the second night does not re-pick the notes the
// first night just judged. The queue used to serve by the note's age alone, so
// after every edit the same oldest notes headed it again (#784).
func TestTwoContractEditsDoNotRepickTheSameHeadNotes(t *testing.T) {
	ctx := context.Background()
	vault := t.TempDir()
	cfg := configOverRules(t, vault, "fact")
	cfg.IndexPath = filepath.Join(t.TempDir(), "index.db")
	cfg.EngineStateDir = t.TempDir()
	idx, err := index.Open(cfg.IndexPath, vault, "", false)
	if err != nil {
		t.Fatal(err)
	}
	defer idx.Close()
	led, err := openLedger(ctx, cfg, idx, &bytes.Buffer{})
	if err != nil {
		t.Fatal(err)
	}
	defer led.Close()

	// Six judged cards, oldest first by age and by judgment alike.
	names := []string{"a", "b", "c", "d", "e", "f"}
	bodies := map[string]string{}
	for i, n := range names {
		rel := "memory/semantic/" + n + ".md"
		body := writeNote(t, vault, rel, response(n, 0.9), enrich.Stamp{
			Version: enrich.PassVersion, RulesHash: currentJudgmentHash(cfg)})
		bodies[rel] = body
		if err := idx.Upsert(note.Note{Rel: rel, Title: n, Body: body, Status: "active",
			Captured: time.Date(2026, 8, 1+i, 9, 0, 0, 0, time.UTC), CapturedSource: "mtime",
		}, 1, int64(len(body))); err != nil {
			t.Fatal(err)
		}
		if err := led.Record(ctx, ledger.Entry{Stage: ledger.StageEnrich, Target: rel,
			Version: enrich.PassVersion, RulesHash: currentJudgmentHash(cfg),
			OutputKey: enrichFingerprint(cfg, nil).Key(body), Outcome: ledger.Done,
			At: time.Date(2026, 9, 1+i, 9, 0, 0, 0, time.UTC)}); err != nil {
			t.Fatal(err)
		}
	}
	types := []string{"fact"}
	night := func(day int) []string {
		t.Helper()
		// An edit a judgment reads: one more memory type.
		types = append(types, fmt.Sprintf("kind%d", day))
		writeRules(t, vault, types...)
		if _, err := cfg.Rules.Refresh(time.Now()); err != nil {
			t.Fatal(err)
		}
		queue, err := enrichServeOrder(cfg, idx, true)
		if err != nil {
			t.Fatal(err)
		}
		causes, since, err := enrichCauses(ctx, cfg, led, queue)
		if err != nil {
			t.Fatal(err)
		}
		queue = orderByCause(queue, causes, since)
		took := queue[:2]
		for _, rel := range took {
			if err := led.Record(ctx, ledger.Entry{Stage: ledger.StageEnrich, Target: rel,
				Version: enrich.PassVersion, RulesHash: currentJudgmentHash(cfg),
				OutputKey: enrichFingerprint(cfg, nil).Key(bodies[rel]), Outcome: ledger.Done,
				At: time.Date(2026, 10, day, 9, 0, 0, 0, time.UTC)}); err != nil {
				t.Fatal(err)
			}
		}
		return took
	}
	first, second := night(1), night(2)
	if !reflect.DeepEqual(first, []string{"memory/semantic/a.md", "memory/semantic/b.md"}) {
		t.Errorf("the first night took %v, want the two least recently judged", first)
	}
	if !reflect.DeepEqual(second, []string{"memory/semantic/c.md", "memory/semantic/d.md"}) {
		t.Errorf("the second night took %v; it re-picked the first night's notes", second)
	}
}
