package main

import (
	"context"
	"database/sql"
	"errors"
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
	"github.com/alexherrero/agentm/daemon/internal/rules"

	_ "modernc.org/sqlite"
)

// The seam where both halves are visible.
//
// Everything here runs the shipped key function against the shipped table over a
// real vault on disk. The ledger's own tests use invented keys, which is right
// for testing the table; they cannot catch a rebuild that recovers rows keyed
// differently from the ones the gate looks up, because inventing both sides of a
// comparison is how a test stops being able to fail.

func newTestLedger(t *testing.T) *ledger.Ledger {
	t.Helper()
	dsn := "file:" + filepath.Join(t.TempDir(), "index.db") + "?_pragma=busy_timeout(10000)"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		t.Fatalf("opening the test database: %v", err)
	}
	db.SetMaxOpenConns(1)
	t.Cleanup(func() { db.Close() })
	l, err := ledger.Open(db)
	if err != nil {
		t.Fatalf("opening the ledger: %v", err)
	}
	return l
}

// writeNote renders one enriched note into the vault and returns its bytes.
func writeNote(t *testing.T, vault, rel string, r enrich.Response, s enrich.Stamp) string {
	t.Helper()
	body := enrich.RenderNote(r, s)
	abs := filepath.Join(vault, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(abs, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return body
}

func response(title string, confidence float64) enrich.Response {
	return enrich.Response{
		Title: title, Type: "fact", Altitude: "artifact",
		Body: "the body of " + title, Confidence: confidence,
	}
}

// The durability bar, read the only way it can honestly be read.
//
// Not row equality — the input key is gone by the time a rebuild runs, because
// the stage overwrote what it read, so no rebuild can reproduce it and asserting
// otherwise would be asserting something impossible. What has to survive is the
// decision: `Seen` gives the same answer for every note before and after the
// table is wiped and rebuilt from the corpus. That is what "deleting the ledger
// loses nothing" actually means, and it can fail.
func TestRebuildPreservesEverySeenDecision(t *testing.T) {
	ctx := context.Background()
	vault := t.TempDir()
	led := newTestLedger(t)

	stamp := enrich.Stamp{
		Version:   enrich.PassVersion,
		RulesHash: "rules-abc",
		At:        time.Date(2026, 8, 20, 9, 0, 0, 0, time.UTC),
	}
	keyer := &enrich.Fingerprint{Version: stamp.Version, RulesHash: stamp.RulesHash}

	// Three notes, covering the three shapes that behave differently.
	notes := map[string]string{}
	// Enriched above the floor: lands `active`, out of the queue.
	notes["memory/confident.md"] = writeNote(t, vault, "memory/confident.md",
		response("confident", 0.9), stamp)
	// Enriched below the floor: lands `unfiled`, so the queue offers it again —
	// this is the note the output key exists for.
	notes["memory/low.md"] = writeNote(t, vault, "memory/low.md",
		response("low", 0.3), stamp)
	// Never enriched: no stamp at all.
	notes["memory/raw.md"] = "---\nstatus: unfiled\n---\n\njust a capture\n"
	if err := os.WriteFile(filepath.Join(vault, "memory", "raw.md"),
		[]byte(notes["memory/raw.md"]), 0o644); err != nil {
		t.Fatal(err)
	}

	// Record the two enriched notes the way the write path does.
	for _, rel := range []string{"memory/confident.md", "memory/low.md"} {
		if err := led.Record(ctx, ledger.Entry{
			Stage: ledger.StageEnrich, Target: rel, Version: stamp.Version,
			RulesHash: stamp.RulesHash,
			InputKey:  keyer.Key("the raw capture for " + rel),
			OutputKey: keyer.Key(notes[rel]),
			Outcome:   ledger.Done, At: stamp.At,
		}); err != nil {
			t.Fatal(err)
		}
	}

	before := map[string]bool{}
	for rel, body := range notes {
		got, err := led.Seen(ctx, ledger.StageEnrich, rel, keyer.Key(body))
		if err != nil {
			t.Fatal(err)
		}
		before[rel] = got
	}
	// The premise: the recorded notes are seen and the unrecorded one is not. If
	// this ever stopped holding, the equality below would pass trivially.
	if !before["memory/confident.md"] || !before["memory/low.md"] {
		t.Fatalf("the live ledger does not recognise its own writes: %v", before)
	}
	if before["memory/raw.md"] {
		t.Fatalf("a note nothing enriched was reported as done: %v", before)
	}

	// Lose the table entirely, then put back what the corpus can prove.
	if _, err := led.ForgetStage(ctx, ledger.StageEnrich); err != nil {
		t.Fatal(err)
	}
	rep, err := led.Rebuild(ctx, ledger.StageEnrich, enrichRebuilder(vault))
	if err != nil {
		t.Fatalf("Rebuild: %v", err)
	}
	if rep.Recovered != 2 {
		t.Errorf("recovered %d rows, want 2 (the two stamped notes)", rep.Recovered)
	}

	for rel, body := range notes {
		got, err := led.Seen(ctx, ledger.StageEnrich, rel, keyer.Key(body))
		if err != nil {
			t.Fatal(err)
		}
		if got != before[rel] {
			t.Errorf("%s: Seen was %v before the rebuild and %v after — the cache "+
				"lost a decision, not just a row", rel, before[rel], got)
		}
	}
}

// The live cost leak, closed. A note enriched below the confidence floor keeps
// `status: unfiled`, which is exactly what the batch queue selects on, so it
// comes back round every cycle. On the corpus as it stands that is 23 of the 25
// notes the first real batches wrote.
func TestALowConfidenceNoteIsNotEnrichedTwice(t *testing.T) {
	ctx := context.Background()
	vault := t.TempDir()
	led := newTestLedger(t)

	stamp := enrich.Stamp{Version: enrich.PassVersion, RulesHash: "rh",
		At: time.Date(2026, 8, 20, 9, 0, 0, 0, time.UTC)}
	keyer := &enrich.Fingerprint{Version: stamp.Version, RulesHash: stamp.RulesHash}

	rel := "memory/low.md"
	body := writeNote(t, vault, rel, response("low", 0.3), stamp)
	if got := enrich.FrontmatterValue(body, "status"); got != "unfiled" {
		t.Fatalf("the fixture is not the case under test: status is %q, want "+
			"unfiled — a note above the floor never re-enters the queue", got)
	}

	if err := led.Record(ctx, ledger.Entry{
		Stage: ledger.StageEnrich, Target: rel, Version: stamp.Version,
		RulesHash: stamp.RulesHash, InputKey: keyer.Key("raw capture"),
		OutputKey: keyer.Key(body), Outcome: ledger.Done, At: stamp.At,
	}); err != nil {
		t.Fatal(err)
	}

	// The next cycle: the queue offers the note, the gate hashes what is on disk.
	gate := &enrich.Fingerprint{
		Version: stamp.Version, RulesHash: stamp.RulesHash,
		Seen: func(r, key string) bool {
			seen, err := led.Seen(ctx, ledger.StageEnrich, r, key)
			if err != nil {
				t.Fatal(err)
			}
			return seen
		},
	}
	err := gate.Check(ctx, enrich.Request{Rel: rel, Raw: body}, body)
	if err == nil {
		t.Fatal("the gate let a note through that this pass already wrote; that is " +
			"a full-price model call on every cycle for as long as it stays unfiled")
	}
	if !errors.Is(err, enrich.ErrNotEligible) {
		t.Errorf("the gate declined for the wrong reason: %v", err)
	}
}

// The other direction, and it matters as much: a note somebody edited is not
// what the pass wrote, so it goes back in the queue.
func TestAnEditedNoteReturnsToTheQueue(t *testing.T) {
	ctx := context.Background()
	vault := t.TempDir()
	led := newTestLedger(t)

	stamp := enrich.Stamp{Version: enrich.PassVersion, RulesHash: "rh",
		At: time.Date(2026, 8, 20, 9, 0, 0, 0, time.UTC)}
	keyer := &enrich.Fingerprint{Version: stamp.Version, RulesHash: stamp.RulesHash}

	rel := "memory/edited.md"
	body := writeNote(t, vault, rel, response("edited", 0.3), stamp)
	if err := led.Record(ctx, ledger.Entry{
		Stage: ledger.StageEnrich, Target: rel, Version: stamp.Version,
		RulesHash: stamp.RulesHash, InputKey: keyer.Key("raw"),
		OutputKey: keyer.Key(body), Outcome: ledger.Done, At: stamp.At,
	}); err != nil {
		t.Fatal(err)
	}

	edited := body + "\nA sentence somebody added afterwards.\n"
	seen, err := led.Seen(ctx, ledger.StageEnrich, rel, keyer.Key(edited))
	if err != nil {
		t.Fatal(err)
	}
	if seen {
		t.Error("a note that changed after enrichment was reported as still done")
	}
}

// A version bump re-queues the whole corpus, and it does so as arithmetic rather
// than as an intention: the version is inside the key, so changing it changes
// every note's key at once.
func TestAVersionBumpUnseesEverything(t *testing.T) {
	ctx := context.Background()
	vault := t.TempDir()
	led := newTestLedger(t)

	stamp := enrich.Stamp{Version: "enrich/1+prompt/old", RulesHash: "rh",
		At: time.Date(2026, 8, 20, 9, 0, 0, 0, time.UTC)}
	old := &enrich.Fingerprint{Version: stamp.Version, RulesHash: stamp.RulesHash}

	rel := "memory/a.md"
	body := writeNote(t, vault, rel, response("a", 0.3), stamp)
	if err := led.Record(ctx, ledger.Entry{
		Stage: ledger.StageEnrich, Target: rel, Version: stamp.Version,
		RulesHash: stamp.RulesHash, OutputKey: old.Key(body),
		Outcome: ledger.Done, At: stamp.At,
	}); err != nil {
		t.Fatal(err)
	}

	for name, fp := range map[string]*enrich.Fingerprint{
		"a new prompt":     {Version: "enrich/1+prompt/new", RulesHash: "rh"},
		"a new rules hash": {Version: stamp.Version, RulesHash: "rh-2"},
	} {
		seen, err := led.Seen(ctx, ledger.StageEnrich, rel, fp.Key(body))
		if err != nil {
			t.Fatal(err)
		}
		if seen {
			t.Errorf("%s left the note looking current; a changed pass has to "+
				"re-queue the population it changed", name)
		}
	}
	// And the unchanged version still matches, so the bump is what moved it
	// rather than the key never matching at all.
	if seen, _ := led.Seen(ctx, ledger.StageEnrich, rel, old.Key(body)); !seen {
		t.Error("the note is not recognised under the version that wrote it")
	}
}

// The rebuilder keys each note under the version the note claims, not the one
// running now. That is what makes a note enriched by an older prompt come back
// as stale rather than as current.
func TestTheRebuilderKeysUnderTheStampedVersion(t *testing.T) {
	ctx := context.Background()
	vault := t.TempDir()
	led := newTestLedger(t)

	oldStamp := enrich.Stamp{Version: "enrich/1+prompt/old", RulesHash: "rh-old",
		At: time.Date(2026, 8, 20, 9, 0, 0, 0, time.UTC)}
	body := writeNote(t, vault, "memory/old.md", response("old", 0.3), oldStamp)

	if _, err := led.Rebuild(ctx, ledger.StageEnrich, enrichRebuilder(vault)); err != nil {
		t.Fatal(err)
	}

	// Under the version that wrote it, the note is recognised.
	oldKey := (&enrich.Fingerprint{Version: oldStamp.Version,
		RulesHash: oldStamp.RulesHash}).Key(body)
	if seen, _ := led.Seen(ctx, ledger.StageEnrich, "memory/old.md", oldKey); !seen {
		t.Error("the rebuilder keyed the note under something other than its own stamp")
	}

	// And under the current one it is pending, as stale.
	nowKey := (&enrich.Fingerprint{Version: enrich.PassVersion, RulesHash: "rh-now"}).Key(body)
	rep, err := led.Pending(ctx, ledger.StageEnrich,
		ledger.Version{Stage: enrich.PassVersion},
		[]ledger.Target{{Rel: "memory/old.md", Key: nowKey}})
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.Pending) != 1 || rep.Pending[0].Reason != ledger.ReasonStale {
		t.Errorf("a note at an older version reads as %+v, want one stale item", rep.Pending)
	}
}

// A note with no `enriched_by` is not recovered. Inventing a row for it would
// claim work that may never have happened, which is the one failure this table
// exists to prevent.
func TestTheRebuilderIgnoresUnstampedNotes(t *testing.T) {
	ctx := context.Background()
	vault := t.TempDir()
	led := newTestLedger(t)

	for rel, body := range map[string]string{
		"memory/raw.md":    "---\nstatus: unfiled\n---\n\njust a capture\n",
		"memory/nofm.md":   "no frontmatter at all\n",
		"memory/prose.md":  "---\ntitle: about enrichment\n---\n\nenriched_by: not-frontmatter\n",
		".hidden/skip.md":  "---\nenriched_by: v1\n---\n\nhidden\n",
		"memory/notes.txt": "---\nenriched_by: v1\n---\n\nnot markdown\n",
	} {
		abs := filepath.Join(vault, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(abs, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	rep, err := led.Rebuild(ctx, ledger.StageEnrich, enrichRebuilder(vault))
	if err != nil {
		t.Fatal(err)
	}
	if rep.Recovered != 0 {
		t.Errorf("recovered %d rows from a corpus with no durable stamps, want 0",
			rep.Recovered)
	}
}

// A ledger that cannot be read answers "not seen", which costs a call that might
// not have been needed. The other direction would skip work that never happened
// and report it as finished, and a wrong "finished" is unrecoverable in a way a
// wasted call is not.
func TestAnUnreadableLedgerFailsTowardSpending(t *testing.T) {
	dsn := "file:" + filepath.Join(t.TempDir(), "index.db") + "?_pragma=busy_timeout(1000)"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		t.Fatal(err)
	}
	db.SetMaxOpenConns(1)
	led, err := ledger.Open(db)
	if err != nil {
		t.Fatal(err)
	}
	if err := led.Record(context.Background(), ledger.Entry{
		Stage: ledger.StageEnrich, Target: "a.md", InputKey: "k", Outcome: ledger.Done,
	}); err != nil {
		t.Fatal(err)
	}
	// Close the handle out from under it, which is what a database going away
	// mid-run looks like from here.
	db.Close()

	fp := &enrich.Fingerprint{Version: "v1", RulesHash: "rh"}
	fp.Seen = func(rel, key string) bool {
		seen, err := led.Seen(context.Background(), ledger.StageEnrich, rel, key)
		return err == nil && seen
	}
	if fp.Seen("a.md", "k") {
		t.Error("an unreadable ledger reported a note as already done")
	}
}

// A nil ledger leaves the gate inert rather than half-wired. That is the state
// the pass shipped in before this part, and it has to stay reachable — the
// one-shot paths that construct a gate without a database depend on it.
func TestANilLedgerLeavesTheGateInert(t *testing.T) {
	// A config with no vault: Rules.Get falls through to the embedded default,
	// so the gate resolves a real hash rather than "unresolved".
	cfg := &config.Config{Rules: rules.NewHolder("", time.Now())}
	fp := enrichFingerprint(cfg, nil)
	if fp.Seen != nil {
		t.Error("a gate built with no ledger has a lookup function")
	}
	if fp.Version != enrich.PassVersion {
		t.Errorf("Version = %q, want the current pass version", fp.Version)
	}
}

// The worked path, from the operator's side.
//
// Someone edits `standards/storage-rules.md` — declares a type, adds a warrant,
// changes a threshold. The next coverage question has to notice, and has to say
// it was the contract rather than the notes.
//
// Through `pendingFor` rather than the ledger directly, because the ledger was
// already provably right about this and the command was the part that could
// quietly ask under no contract at all. Handing it a contract it never used
// would leave every ledger test green while the queue stayed empty.
func TestEditingTheFilingContractRequeuesWhatItJudged(t *testing.T) {
	ctx := context.Background()
	vault := t.TempDir()
	led := newTestLedger(t)

	cfg := configOverRules(t, vault, "preference", "convention")
	first := currentRulesHash(cfg)

	// A note, enriched and recorded under that contract.
	rel := "memory/a.md"
	body := writeNote(t, vault, rel, response("a", 0.8),
		enrich.Stamp{Version: enrich.PassVersion, RulesHash: first})
	idx := indexOverVault(t, vault, rel, body)
	if err := led.Record(ctx, ledger.Entry{
		Stage: ledger.StageEnrich, Target: rel, Version: enrich.PassVersion,
		RulesHash: first, OutputKey: enrichFingerprint(cfg, nil).Key(body),
		Outcome: ledger.Done, At: time.Date(2026, 8, 20, 9, 0, 0, 0, time.UTC),
	}); err != nil {
		t.Fatal(err)
	}

	// Nothing pending: coverage is complete under the contract that judged it.
	rep, err := pendingFor(ctx, ledger.StageEnrich, cfg, idx, led)
	if err != nil {
		t.Fatal(err)
	}
	// The population is the control. Without this the "nothing pending" above and
	// the "one pending" below are both true of an empty queue, and the test would
	// pass whether or not the edit was ever noticed.
	if rep.Eligible != 1 {
		t.Fatalf("the eligible population is %d, want the one note — everything "+
			"below is vacuous over an empty queue", rep.Eligible)
	}
	if len(rep.Pending) != 0 {
		t.Fatalf("a note enriched under the current contract is pending: %+v",
			rep.Pending)
	}

	// The operator declares a type.
	writeRules(t, vault, "preference", "convention", "recipe")
	if _, err := cfg.Rules.Refresh(time.Now()); err != nil {
		t.Fatal(err)
	}
	second := currentRulesHash(cfg)
	if second == first {
		t.Fatal("editing the contract did not change its hash, so this test " +
			"cannot tell whether the queue noticed")
	}

	rep, err = pendingFor(ctx, ledger.StageEnrich, cfg, idx, led)
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.Pending) != 1 {
		t.Fatalf("after a contract edit, pending = %+v, want the one note it "+
			"judged", rep.Pending)
	}
	if rep.Pending[0].Reason != ledger.ReasonStale {
		t.Errorf("the note reads as %q; nothing about it changed — the contract "+
			"did", rep.Pending[0].Reason)
	}
	if !strings.Contains(rep.Pending[0].Detail, "filing contract") {
		t.Errorf("Detail = %q, want the contract named", rep.Pending[0].Detail)
	}
	if rep.RulesHash != second {
		t.Errorf("Report.RulesHash = %q, want the edited contract %q",
			rep.RulesHash, second)
	}
}

// writeRules writes a whole valid contract whose memory types are the ones
// named. Declaring a type is the design's own worked example of an edit, and it
// is the smallest real one: a single list entry, a different hash.
func writeRules(t *testing.T, vault string, types ...string) {
	t.Helper()
	dir := filepath.Join(vault, "standards")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	routing := ""
	for _, ty := range types {
		routing += "  " + ty + ": memory/semantic\n"
	}
	body := "# Storage rules\n\n```storage-rules\n" +
		"classes:\n  semantic: Facts.\n  procedural: How.\n  episodic: Traces.\n" +
		"  entities: Referents.\n  crystallized: Lessons.\n  mocs: Maps.\n" +
		"memory_types: [" + strings.Join(types, ", ") + "]\n" +
		"default_type: " + types[0] + "\n" +
		"routing:\n" + routing +
		"record_kinds: [brief]\ndeprecations: {}\nwarrants: {}\n" +
		"thresholds: {low_confidence: 0.65}\n```\n"
	if err := os.WriteFile(filepath.Join(dir, "storage-rules.md"),
		[]byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

// configOverRules builds the smallest config `pendingFor` actually reads: a
// vault path and a live contract.
func configOverRules(t *testing.T, vault string, types ...string) *config.Config {
	t.Helper()
	t.Setenv("AGENTM_STORAGE_RULES", "")
	os.Unsetenv("AGENTM_STORAGE_RULES")
	writeRules(t, vault, types...)
	cfg := &config.Config{VaultPath: vault}
	cfg.Rules = rules.NewHolder(vault, time.Now())
	if _, err := cfg.Rules.Get(); err != nil {
		t.Fatalf("the test's own contract does not parse: %v", err)
	}
	return cfg
}

func indexOverVault(t *testing.T, vault, rel, body string) *index.Index {
	t.Helper()
	x, err := index.Open(filepath.Join(t.TempDir(), "index.db"), vault, "", false)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { x.Close() })
	if err := x.Upsert(note.Note{
		Rel: rel, Title: "a", Body: body, Status: "unfiled",
		Captured: time.Date(2026, 8, 20, 9, 0, 0, 0, time.UTC), CapturedSource: "mtime",
	}, 1, int64(len(body))); err != nil {
		t.Fatal(err)
	}
	return x
}
