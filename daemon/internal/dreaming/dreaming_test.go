package dreaming

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
)

// ── the lock ─────────────────────────────────────────────────────────────────

func TestASecondStartIsRefusedWhileTheFirstHoldsTheLock(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "dreaming", "lock")
	first, err := Acquire(dir, 30*time.Second, 200*time.Millisecond)
	if err != nil {
		t.Fatalf("first acquire: %v", err)
	}
	defer first.Release()
	_, err = Acquire(dir, 30*time.Second, 200*time.Millisecond)
	var held *ErrHeld
	if !errors.As(err, &held) {
		t.Fatalf("second acquire should be refused as held, got %v", err)
	}
	if held.Age > 30*time.Second {
		t.Errorf("the holder is alive; age %v reads as stale", held.Age)
	}
	first.Release()
	third, err := Acquire(dir, 30*time.Second, 200*time.Millisecond)
	if err != nil {
		t.Fatalf("after release the lock must be free: %v", err)
	}
	third.Release()
}

func TestADeadHolderIsTakenOverAfterTheStaleWindow(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "lock")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	old := time.Now().Add(-2 * time.Minute)
	if err := os.Chtimes(dir, old, old); err != nil {
		t.Fatal(err)
	}
	l, err := Acquire(dir, 30*time.Second, 200*time.Millisecond)
	if err != nil {
		t.Fatalf("a holder two minutes past its heartbeat is dead; acquire should take over: %v", err)
	}
	l.Release()
	if _, err := os.Stat(dir); !os.IsNotExist(err) {
		t.Errorf("release should remove the lock directory")
	}
}

func TestADeadHoldersLockIsTakenOverAtOnceByItsPid(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "lock")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	// A fresh heartbeat but a pid nobody has: the holder was killed.
	os.WriteFile(filepath.Join(dir, "pid"), []byte("4194304"), 0o644) // Linux pid_max; never a live pid
	start := time.Now()
	l, err := Acquire(dir, 30*time.Second, 200*time.Millisecond)
	if err != nil {
		t.Fatalf("a dead pid's lock should be taken over without waiting: %v", err)
	}
	defer l.Release()
	if time.Since(start) > 150*time.Millisecond {
		t.Errorf("takeover waited %v; a dead holder should not cost the stale window", time.Since(start))
	}
	blob, _ := os.ReadFile(filepath.Join(dir, "pid"))
	if strings.TrimSpace(string(blob)) != strconv.Itoa(os.Getpid()) {
		t.Errorf("the new holder should write its own pid, got %q", blob)
	}
}

func TestTheVaultLockDirIsThePythonProtocolsPath(t *testing.T) {
	cache := t.TempDir()
	t.Setenv("XDG_CACHE_HOME", cache)
	vault := t.TempDir()
	got, err := VaultLockDir(vault)
	if err != nil {
		t.Fatal(err)
	}
	real, _ := filepath.EvalSymlinks(vault)
	sum := sha256.Sum256([]byte(real))
	want := filepath.Join(cache, "agentm", "locks", hex.EncodeToString(sum[:]), "lock")
	if got != want {
		t.Errorf("VaultLockDir = %s, want %s (sha256 of the realpath, hex, under the cache root)", got, want)
	}
}

// ── the gate ─────────────────────────────────────────────────────────────────

func TestTheGateNeedsBothTimeAndActivity(t *testing.T) {
	now := time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC)
	week := 7 * 24 * time.Hour
	done := State{LastDone: now.Add(-8 * 24 * time.Hour)}
	recent := State{LastDone: now.Add(-2 * time.Hour)}
	cases := []struct {
		name string
		st   State
		act  Activity
		due  bool
	}{
		{"never run, nothing happened", State{}, Activity{}, false},
		{"never run, a capture happened", State{}, Activity{Captures: 1}, true},
		{"too soon, even with activity", recent, Activity{Captures: 5, Recalls: 5}, false},
		{"a week on, nothing happened", done, Activity{}, false},
		{"a week on, a recall happened", done, Activity{Recalls: 1}, true},
		{"a week on, the instrument is missing", done, Activity{Unknown: true}, true},
	}
	for _, c := range cases {
		d := Due(c.st, now, week, c.act)
		if d.Due != c.due {
			t.Errorf("%s: due=%v (%s), want %v", c.name, d.Due, d.Reason, c.due)
		}
		if d.Reason == "" {
			t.Errorf("%s: every decision names its reason", c.name)
		}
	}
}

func TestRecallsSinceReadsTheHistoryLedger(t *testing.T) {
	p := filepath.Join(t.TempDir(), "recall-history.jsonl")
	lines := []string{
		`{"ts": "2026-09-01T10:00:00+00:00", "hit_count": 1}`,
		`{"ts": "2026-09-04T10:00:00+00:00", "hit_count": 2}`,
		`{"ts": 1788600000, "hit_count": 0}`, // 2026-09-05T09:20:00Z as epoch
		`not json at all`,
	}
	if err := os.WriteFile(p, []byte(strings.Join(lines, "\n")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	n, err := RecallsSince(p, time.Date(2026, 9, 3, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if n != 2 {
		t.Errorf("recalls since 2026-09-03 = %d, want 2 (one ISO, one epoch; the older row and the junk line excluded)", n)
	}
	if n, _ := RecallsSince(filepath.Join(t.TempDir(), "absent.jsonl"), time.Time{}); n != 0 {
		t.Errorf("a missing ledger is zero recalls, got %d", n)
	}
}

// ── the journal ──────────────────────────────────────────────────────────────

func TestTheJournalDropsATornTailAndFindsTheUnfinishedRun(t *testing.T) {
	state := t.TempDir()
	j, err := OpenJournal(state)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	must := func(e Entry) {
		t.Helper()
		if err := j.Append(e); err != nil {
			t.Fatal(err)
		}
	}
	must(Entry{Kind: KindRunStart, RunID: "r1", TS: now, Mode: "apply"})
	must(Entry{Kind: KindIntent, RunID: "r1", TS: now, ID: "r1-0001", Rel: "memory/semantic/a.md"})
	must(Entry{Kind: KindApplied, RunID: "r1", TS: now, ID: "r1-0001", Rel: "memory/semantic/a.md"})
	must(Entry{Kind: KindIntent, RunID: "r1", TS: now, ID: "r1-0002", Rel: "memory/semantic/b.md"})
	// A crash mid-append: half a line.
	f, _ := os.OpenFile(j.Path, os.O_APPEND|os.O_WRONLY, 0o644)
	f.WriteString(`{"kind":"intent","run_id":"r1","id":"r1-00`)
	f.Close()
	entries, err := j.Read()
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 4 {
		t.Fatalf("read %d entries, want 4 (the torn tail dropped)", len(entries))
	}
	runID, pending := Unfinished(entries)
	if runID != "r1" || len(pending) != 1 || pending[0].ID != "r1-0002" {
		t.Errorf("unfinished = %q %v, want r1 with the one intent that never applied", runID, pending)
	}
	must(Entry{Kind: KindRunDone, RunID: "r1", TS: now, Outcome: "resumed"})
	entries, _ = j.Read()
	if runID, pending := Unfinished(entries); runID != "" || pending != nil {
		t.Errorf("after run-done nothing is unfinished, got %q %v", runID, pending)
	}
}

func TestResolveIsIdempotentAgainstTheHashes(t *testing.T) {
	vault := t.TempDir()
	state := t.TempDir()
	j, _ := OpenJournal(state)
	rel := "memory/semantic/n.md"
	p := filepath.Join(vault, rel)
	os.MkdirAll(filepath.Dir(p), 0o755)
	before := []byte("---\nlifecycle: active\n---\nbody\n")
	after := []byte("---\nlifecycle: dormant\nlifecycle_since: 2026-09-05\n---\nbody\n")
	os.WriteFile(p, before, 0o644)
	intent := Entry{Kind: KindIntent, RunID: "r", ID: "r-1", Job: "lifecycle", Rel: rel,
		BeforeHash: Hash(before), AfterHash: Hash(after), After: base64.StdEncoding.EncodeToString(after)}
	now := time.Now().UTC()

	kind, err := j.Resolve(vault, intent, now)
	if err != nil || kind != KindApplied {
		t.Fatalf("a target still at `before` is applied on resume: kind=%s err=%v", kind, err)
	}
	if got, _ := os.ReadFile(p); string(got) != string(after) {
		t.Errorf("resume did not write the journaled content")
	}
	kind, _ = j.Resolve(vault, intent, now)
	if kind != KindApplied {
		t.Errorf("a target already at `after` is recorded applied, never rewritten: %s", kind)
	}
	os.WriteFile(p, []byte("---\nlifecycle: active\n---\nsomeone edited this\n"), 0o644)
	kind, _ = j.Resolve(vault, intent, now)
	if kind != KindSkipped {
		t.Errorf("a target that hashes as neither is a conflict, skipped: %s", kind)
	}
	if got, _ := os.ReadFile(p); !strings.Contains(string(got), "someone edited this") {
		t.Errorf("a conflict must leave the operator's edit alone")
	}
	entries, _ := j.Read()
	notes := []string{}
	for _, e := range entries {
		notes = append(notes, e.Kind+":"+e.Note)
	}
	want := []string{"applied:applied on resume", "applied:found applied on resume", "skipped:" + ErrConflict.Error()}
	if strings.Join(notes, "|") != strings.Join(want, "|") {
		t.Errorf("journal = %v, want %v", notes, want)
	}
}

func TestCommitJournalsBeforeItWritesAndSkipsAMovedTarget(t *testing.T) {
	vault := t.TempDir()
	state := t.TempDir()
	j, _ := OpenJournal(state)
	rel := "memory/semantic/n.md"
	p := filepath.Join(vault, rel)
	os.MkdirAll(filepath.Dir(p), 0o755)
	before := []byte("---\nlifecycle: active\n---\nbody\n")
	os.WriteFile(p, before, 0o644)
	in := Intent{Job: "lifecycle", Rel: rel, Before: before, After: []byte(SetLifecycle(string(before), "dormant", "2026-09-05")), Summary: "s"}
	kind, err := j.Commit(vault, "r", "r-1", in, time.Now().UTC())
	if err != nil || kind != KindApplied {
		t.Fatalf("commit: kind=%s err=%v", kind, err)
	}
	entries, _ := j.Read()
	if len(entries) != 2 || entries[0].Kind != KindIntent || entries[1].Kind != KindApplied || entries[0].ID != entries[1].ID {
		t.Fatalf("a commit is an intent line then an applied line with the same id: %v", entries)
	}
	if entries[0].BeforeHash != Hash(before) || entries[0].AfterHash != Hash(in.After) {
		t.Errorf("the intent carries both hashes")
	}
	// The target changed under a second intent planned against the old bytes.
	os.WriteFile(p, []byte("---\nlifecycle: active\n---\nchanged\n"), 0o644)
	kind, _ = j.Commit(vault, "r", "r-2", in, time.Now().UTC())
	if kind != KindSkipped {
		t.Errorf("a target that moved since the plan is skipped, not overwritten: %s", kind)
	}
	if got, _ := os.ReadFile(p); !strings.Contains(string(got), "changed") {
		t.Errorf("the skipped target must be untouched")
	}
}

// ── the edit, byte for byte with the Python port ────────────────────────────

func TestSetLifecycleMatchesThePythonEdit(t *testing.T) {
	cases := []struct{ name, text, to, since, want string }{
		{"replace and date, body untouched",
			"---\ntitle: T\nlifecycle: active\ntags: [a]\n---\n\nBody stays.\n", "dormant", "2026-09-05",
			"---\ntitle: T\nlifecycle: dormant\nlifecycle_since: 2026-09-05\ntags: [a]\n---\n\nBody stays.\n"},
		{"insert when absent",
			"---\ntitle: T\n---\nBody.\n", "dormant", "2026-09-05",
			"---\ntitle: T\nlifecycle: dormant\nlifecycle_since: 2026-09-05\n---\nBody.\n"},
		{"a second move updates the date",
			"---\nlifecycle: dormant\nlifecycle_since: 2026-01-01\n---\n", "active", "2026-09-05",
			"---\nlifecycle: active\nlifecycle_since: 2026-09-05\n---\n"},
		{"no frontmatter is left alone", "just a body\n", "dormant", "2026-09-05", "just a body\n"},
		{"a list item or comment is not a key",
			"---\ntags:\n- lifecycle: x\n# lifecycle: y\ntitle: T\n---\nb\n", "dormant", "2026-09-05",
			"---\ntags:\n- lifecycle: x\n# lifecycle: y\ntitle: T\nlifecycle: dormant\nlifecycle_since: 2026-09-05\n---\nb\n"},
	}
	for _, c := range cases {
		if got := SetLifecycle(c.text, c.to, c.since); got != c.want {
			t.Errorf("%s:\n got %q\nwant %q", c.name, got, c.want)
		}
	}
}

// ── the plan ─────────────────────────────────────────────────────────────────

func writeNote(t *testing.T, root, rel, lifecycle string, daysSilent int, now time.Time, extra string) string {
	t.Helper()
	p := filepath.Join(root, rel)
	os.MkdirAll(filepath.Dir(p), 0o755)
	fm := "---\ntitle: " + filepath.Base(rel) + "\nkind: reference\nstatus: active\nslug: " + strings.TrimSuffix(filepath.Base(rel), ".md") + "\n"
	if lifecycle != "" {
		fm += "lifecycle: " + lifecycle + "\n"
	}
	if daysSilent >= 0 {
		fm += "created: " + now.Add(-time.Duration(daysSilent)*24*time.Hour).Format("2006-01-02") + "\n"
	}
	if err := os.WriteFile(p, []byte(fm+extra+"---\n\nA plain body.\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	return rel
}

func TestThePlanSinksTheSilentLiftsTheRecalledAndNamesTheCold(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC)
	old := writeNote(t, root, "memory/semantic/old.md", "active", 400, now, "")
	writeNote(t, root, "memory/semantic/fresh.md", "active", 30, now, "")
	bare := writeNote(t, root, "memory/semantic/bare.md", "", 400, now, "")
	back := writeNote(t, root, "memory/semantic/back.md", "dormant", 900, now, "")
	cold := writeNote(t, root, "memory/semantic/cold.md", "dormant", 2000, now, "")
	near := writeNote(t, root, "memory/semantic/near.md", "dormant", 1700, now, "")
	writeNote(t, root, "memory/semantic/pinned.md", "pinned", 3000, now, "")
	writeNote(t, root, "memory/semantic/sup.md", "superseded", 3000, now, "")
	writeNote(t, root, "memory/semantic/arch.md", "archived", 3000, now, "")
	writeNote(t, root, "memory/semantic/durable.md", "active", 3000, now, "lifecycle_tier: durable\n")
	// A genuine recall of `back` today, in the sidecar the daemon reads.
	sidecar := map[string]any{"version": 1, "entries": map[string]any{"back": map[string]any{"last_access": now.Format("2006-01-02")}}}
	blob, _ := json.Marshal(sidecar)
	os.WriteFile(filepath.Join(root, ".lifecycle.json"), blob, 0o644)

	plan, err := PlanLifecycle(root, nil, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	rels := func(ms []Move) []string {
		var out []string
		for _, m := range ms {
			out = append(out, m.Rel)
		}
		return out
	}
	if got := rels(plan.Demoted); strings.Join(got, ",") != bare+","+old {
		t.Errorf("demoted = %v, want the silent active pair (bare, old)", got)
	}
	if got := rels(plan.Revived); strings.Join(got, ",") != back {
		t.Errorf("revived = %v, want the recalled dormant note", got)
	}
	if got := rels(plan.Candidates); strings.Join(got, ",") != cold {
		t.Errorf("candidates = %v, want the dormant note past the archive line", got)
	}
	if got := rels(plan.Previews); strings.Join(got, ",") != near {
		t.Errorf("previews = %v, want the dormant note nearing the line", got)
	}
	if len(plan.Intents) != 3 {
		t.Errorf("intents = %d, want 3 (two demotions, one revival; candidates are never moved)", len(plan.Intents))
	}
	if plan.Considered != 10 {
		t.Errorf("considered = %d, want 10", plan.Considered)
	}
	for _, in := range plan.Intents {
		if !strings.Contains(string(in.After), "lifecycle_since: 2026-09-05") {
			t.Errorf("%s: the intent's after-content is not dated", in.Rel)
		}
		if got, _ := os.ReadFile(filepath.Join(root, in.Rel)); string(got) != string(in.Before) {
			t.Errorf("%s: planning wrote something", in.Rel)
		}
	}
	capped, _ := PlanLifecycle(root, nil, now, 1)
	if len(capped.Demoted) != 1 || capped.Capped != 1 {
		t.Errorf("cap 1: demoted %d capped %d, want 1 and 1", len(capped.Demoted), capped.Capped)
	}
}

// ── the run ──────────────────────────────────────────────────────────────────

func scratchConfig(t *testing.T) (*config.Config, string) {
	t.Helper()
	root := t.TempDir()
	cfg := &config.Config{VaultPath: root, MemoryRoot: "", EngineStateDir: filepath.Join(t.TempDir(), "state")}
	return cfg, root
}

func lifecycleOf(t *testing.T, p string) string {
	t.Helper()
	b, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	for _, l := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(l, "lifecycle: ") {
			return strings.TrimPrefix(l, "lifecycle: ")
		}
	}
	return "active"
}

func TestReportOnlyDecidesAndWritesNothing(t *testing.T) {
	cfg, root := scratchConfig(t)
	now := time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC)
	old := writeNote(t, root, "memory/semantic/old.md", "active", 400, now, "")
	rep, err := Run(cfg, Options{Now: now})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Outcome != OutcomeReported || rep.Mode != "report" || len(rep.Plan.Demoted) != 1 {
		t.Errorf("report-only: outcome=%s mode=%s demoted=%d", rep.Outcome, rep.Mode, len(rep.Plan.Demoted))
	}
	if lifecycleOf(t, filepath.Join(root, old)) != "active" {
		t.Errorf("report-only wrote a transition")
	}
	j := Journal{Path: JournalPath(cfg.EngineStateDir)}
	entries, _ := j.Read()
	if len(entries) != 2 || entries[0].Kind != KindRunStart || entries[0].Mode != "report" || entries[1].Kind != KindRunDone {
		t.Errorf("a report-only run journals its start and end and no intents: %v", entries)
	}
	if _, err := os.Stat(filepath.Join(cfg.EngineStateDir, LifecycleJournalName)); !os.IsNotExist(err) {
		t.Errorf("report-only must not write the lifecycle journal")
	}
	st, _ := LoadState(cfg.EngineStateDir)
	if !st.LastDone.Equal(now) || st.Runs != 1 || st.LastOutcome != OutcomeReported {
		t.Errorf("state after the run = %+v", st)
	}
}

func TestApplyWritesJournalsAndIsThenNotDueUntilTheIntervalPasses(t *testing.T) {
	cfg, root := scratchConfig(t)
	now := time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC)
	old := writeNote(t, root, "memory/semantic/old.md", "active", 400, now, "")
	rep, err := Run(cfg, Options{Now: now, Apply: true, RunID: "run-a"})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Outcome != OutcomeApplied || rep.Applied != 1 || rep.Skipped != 0 {
		t.Errorf("apply: %+v", rep)
	}
	if lifecycleOf(t, filepath.Join(root, old)) != "dormant" {
		t.Errorf("the silent note should be dormant now")
	}
	blob, err := os.ReadFile(filepath.Join(cfg.EngineStateDir, LifecycleJournalName))
	if err != nil {
		t.Fatal("the governance journal was not written")
	}
	line := strings.TrimSpace(string(blob))
	want := `{"actor":"policy","from":"active","reason":"silent 400 days, past dormant_after_days 365 → dormant","rel":"memory/semantic/old.md","run_id":"run-a","to":"dormant","ts":"2026-09-05T09:00:00+00:00"}`
	if line != want {
		t.Errorf("lifecycle journal line\n got %s\nwant %s", line, want)
	}
	again, err := Run(cfg, Options{Now: now.Add(time.Hour), Apply: true})
	if err != nil {
		t.Fatal(err)
	}
	if again.Outcome != OutcomeNotDue {
		t.Errorf("an hour later the gate should hold: %s (%s)", again.Outcome, again.Decision.Reason)
	}
	forced, err := Run(cfg, Options{Now: now.Add(time.Hour), Apply: true, Force: true})
	if err != nil {
		t.Fatal(err)
	}
	if forced.Outcome != OutcomeApplied || forced.Applied != 0 {
		t.Errorf("forced, with nothing left to do: %+v", forced)
	}
}

func TestACrashedPassIsResumedBeforeTheGateAndNothingIsAppliedTwice(t *testing.T) {
	cfg, root := scratchConfig(t)
	now := time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC)
	a := writeNote(t, root, "memory/semantic/a.md", "active", 400, now, "")
	b := writeNote(t, root, "memory/semantic/b.md", "active", 400, now, "")
	// Simulate a pass that journaled two intents, applied the first, and died.
	plan, _ := PlanLifecycle(root, nil, now, 0)
	j, _ := OpenJournal(cfg.EngineStateDir)
	j.Append(Entry{Kind: KindRunStart, RunID: "crashed", TS: now, Mode: "apply"})
	for i, in := range plan.Intents {
		id := "crashed-" + string(rune('1'+i))
		j.Append(Entry{Kind: KindIntent, RunID: "crashed", TS: now, ID: id, Job: in.Job, Rel: in.Rel,
			BeforeHash: Hash(in.Before), AfterHash: Hash(in.After), After: base64.StdEncoding.EncodeToString(in.After)})
		if i == 0 {
			os.WriteFile(filepath.Join(root, in.Rel), in.After, 0o644) // written, but the applied line never made it
		}
	}
	SaveState(cfg.EngineStateDir, State{LastStarted: now, LastDone: now.Add(-time.Hour), LastRunID: "crashed", Runs: 1})

	rep, err := Run(cfg, Options{Now: now.Add(time.Minute), Apply: true})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Resumed != 2 {
		t.Errorf("resumed = %d, want both pending intents settled", rep.Resumed)
	}
	if rep.Outcome != OutcomeNotDue {
		t.Errorf("after the resume the gate holds (an hour since the last pass): %s", rep.Outcome)
	}
	for _, rel := range []string{a, b} {
		text, _ := os.ReadFile(filepath.Join(root, rel))
		if lifecycleOf(t, filepath.Join(root, rel)) != "dormant" {
			t.Errorf("%s not dormant after the resume", rel)
		}
		if strings.Count(string(text), "lifecycle_since:") != 1 {
			t.Errorf("%s carries %d lifecycle_since lines — a double application", rel, strings.Count(string(text), "lifecycle_since:"))
		}
	}
	entries, _ := j.Read()
	var notes []string
	for _, e := range entries {
		if e.Kind == KindApplied {
			notes = append(notes, e.Rel+"="+e.Note)
		}
	}
	if strings.Join(notes, "|") != a+"=found applied on resume|"+b+"=applied on resume" {
		t.Errorf("resume record = %v", notes)
	}
	if runID, pending := Unfinished(entries); runID != "" || len(pending) != 0 {
		t.Errorf("the crashed run should be closed: %q %v", runID, pending)
	}
}

func TestRunRefusesWhileAnotherPassHoldsTheLock(t *testing.T) {
	cfg, _ := scratchConfig(t)
	holder, err := Acquire(SingletonLockDir(cfg.EngineStateDir), 30*time.Second, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer holder.Release()
	rep, err := Run(cfg, Options{LockWait: 100 * time.Millisecond, Force: true})
	if !errors.Is(err, ErrRefused) || rep.Refused == "" || rep.Outcome != "refused" {
		t.Errorf("a second pass must be refused with the holder named: err=%v rep=%+v", err, rep)
	}
}
