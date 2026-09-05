package dreaming

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// Task 4: the mutation pass and promotion. The frontmatter reads and edits
// and the fingerprint are ports of Python functions; the values asserted
// here were recorded from those functions (scripts/health, 2026-09-05), not
// recomputed by the port.

func TestParseFrontmatterIsFirstWinsAndStripsQuotes(t *testing.T) {
	fm, body := ParseFrontmatter("---\ntitle: \"Quoted\"\nstatus: active\nstatus: superseded\n- item: no\n# key: no\n  indented: no\nreview_flags: [near-duplicate, update-candidate]\n---\nThe body.\n")
	if fm["title"] != "Quoted" || fm["status"] != "active" || fm["indented"] != "" || fm["item"] != "" || fm["key"] != "" {
		t.Errorf("fields = %v", fm)
	}
	if got := ListField(fm["review_flags"]); strings.Join(got, ",") != "near-duplicate,update-candidate" {
		t.Errorf("list = %v", got)
	}
	if body != "The body.\n" {
		t.Errorf("body = %q", body)
	}
	if fm, body := ParseFrontmatter("no block\n"); len(fm) != 0 || body != "no block\n" {
		t.Errorf("no block: %v %q", fm, body)
	}
}

func TestPatchFrontmatterMatchesThePythonPatch(t *testing.T) {
	// Recorded from dream._patch_frontmatter with the same inputs.
	got := PatchFrontmatter("---\ntitle: T\nstatus: active\ntags: [a]\n---\n\nBody.\n",
		[]Update{{"status", "superseded"}, {"supersedes", "memory/semantic/canon.md"}})
	want := "---\ntitle: T\nstatus: superseded\ntags: [a]\nsupersedes: memory/semantic/canon.md\n---\n\nBody.\n"
	if got != want {
		t.Errorf("in place + appended:\n got %q\nwant %q", got, want)
	}
	got = PatchFrontmatter("---\ntitle: T\n---\nBody.\n", []Update{{"status", "superseded"}, {"supersedes", "x.md"}})
	if want := "---\ntitle: T\nstatus: superseded\nsupersedes: x.md\n---\nBody.\n"; got != want {
		t.Errorf("appended:\n got %q\nwant %q", got, want)
	}
	got = PatchFrontmatter("no block\n", []Update{{"status", "superseded"}})
	if want := "---\nstatus: superseded\n---\nno block\n"; got != want {
		t.Errorf("no block:\n got %q\nwant %q", got, want)
	}
	got = DropFrontmatterKeys("---\ntitle: T\nreview_flags: [near-duplicate]\nrelated: x.md\ntags: [a]\n---\nBody.\n", "review_flags", "related")
	if want := "---\ntitle: T\ntags: [a]\n---\nBody.\n"; got != want {
		t.Errorf("drop:\n got %q\nwant %q", got, want)
	}
}

func TestFingerprintMatchesThePythonFingerprint(t *testing.T) {
	body := "  The  quick\tbrown fox\r\n\r\nJumps  over\n\n  the LAZY dog  \n"
	if got := NormalizeBody(body); got != "the quick brown fox\njumps over\nthe lazy dog" {
		t.Errorf("normalized = %q", got)
	}
	// fingerprint.compute_fingerprint(body), recorded.
	if got := Fingerprint(body); got != "bc7623b34d33aa4e1bbbbcc8469b5426d57b9ebb164926a3fda70d0242e76f5c" {
		t.Errorf("fingerprint = %s", got)
	}
	if LiveFingerprint("---\ntitle: T\n---\n"+body) != Fingerprint(body) {
		t.Errorf("the live fingerprint must strip the frontmatter block first")
	}
}

func writeRaw(t *testing.T, root, rel, text string) {
	t.Helper()
	p := filepath.Join(root, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestCopiesCollapseIntoTheEarliestAndLeaveTheSurvivorAlone(t *testing.T) {
	root := t.TempDir()
	body := "Run the checks, then push.\nNever tag before green.\n"
	writeRaw(t, root, "memory/procedural/copy-1.md", "---\ntitle: again\nkind: workflow\nstatus: active\ncreated: 2026-02-01\n---\n\n  Run  the checks,   then push.\n\nNEVER tag before green.\n")
	writeRaw(t, root, "memory/procedural/copy-2.md", "---\ntitle: legacy\nkind: workflow\nstatus: active\n---\n\nrun the checks, then push.\nnever tag before green.\n")
	writeRaw(t, root, "memory/procedural/copy-canon.md", "---\ntitle: the procedure\nkind: workflow\nstatus: active\ncreated: 2026-01-01\n---\n\n"+body)
	writeRaw(t, root, "memory/procedural/superseded-copy.md", "---\ntitle: old\nkind: workflow\nstatus: superseded\ncreated: 2025-01-01\n---\n\n"+body)
	writeRaw(t, root, "memory/semantic/_always-load/curated.md", "---\ntitle: curated\nstatus: active\nlifecycle: pinned\n---\n\n"+body)
	writeRaw(t, root, "memory/semantic/lonely.md", "---\ntitle: lonely\nstatus: active\n---\n\nNothing else says this.\n")
	plan, err := PlanCopies(root, 0)
	if err != nil {
		t.Fatal(err)
	}
	if plan.Considered != 4 {
		t.Errorf("considered %d active notes, want 4 (the superseded one and the curated one excluded)", plan.Considered)
	}
	if len(plan.Families) != 1 {
		t.Fatalf("families = %+v, want one", plan.Families)
	}
	f := plan.Families[0]
	if f.Canonical != "memory/procedural/copy-canon.md" || strings.Join(f.Copies, ",") != "memory/procedural/copy-1.md,memory/procedural/copy-2.md" {
		t.Errorf("family = %+v", f)
	}
	if f.Summary != "memory/procedural/copy-canon.md + 2 content-identical legacy copies — collapse into the earliest, mark the rest superseded" {
		t.Errorf("summary = %q", f.Summary)
	}
	if len(plan.Intents) != 2 {
		t.Fatalf("intents = %d, want one per copy", len(plan.Intents))
	}
	for _, in := range plan.Intents {
		after := string(in.After)
		if !strings.Contains(after, "status: superseded\n") || !strings.Contains(after, "supersedes: memory/procedural/copy-canon.md\n") {
			t.Errorf("%s: after = %q", in.Rel, after)
		}
		if got, _ := os.ReadFile(filepath.Join(root, in.Rel)); string(got) != string(in.Before) {
			t.Errorf("planning wrote %s", in.Rel)
		}
	}
	capped, _ := PlanCopies(root, 1)
	if len(capped.Families) != 1 || capped.Deferred != 0 {
		t.Errorf("cap 1 over one family: %+v", capped)
	}
}

func TestCopiesPreferTheEarliestCreatedThenThePath(t *testing.T) {
	root := t.TempDir()
	body := "same words\n"
	writeRaw(t, root, "memory/semantic/a-newer.md", "---\nstatus: active\ncreated: 2026-05-01\n---\n\n"+body)
	writeRaw(t, root, "memory/semantic/b-older.md", "---\nstatus: active\ncreated: 2024-05-01\n---\n\n"+body)
	writeRaw(t, root, "memory/semantic/c-undated.md", "---\nstatus: active\n---\n\n"+body)
	plan, _ := PlanCopies(root, 0)
	if len(plan.Families) != 1 || plan.Families[0].Canonical != "memory/semantic/b-older.md" {
		t.Errorf("the earliest dated note is canonical: %+v", plan.Families)
	}
	if strings.Join(plan.Families[0].Copies, ",") != "memory/semantic/a-newer.md,memory/semantic/c-undated.md" {
		t.Errorf("copies in walk order: %v", plan.Families[0].Copies)
	}
}

func testRules(t *testing.T) *rules.Rules {
	t.Helper()
	r, err := rules.LoadFile(filepath.Join("..", "rules", "storage-rules.default.md"))
	if err != nil {
		t.Fatalf("packaged contract: %v", err)
	}
	return r
}

func TestRefileMovesAWrongClassNoteAndClearsAStaleFlag(t *testing.T) {
	root := t.TempDir()
	r := testRules(t)
	class, ok := r.ClassFor("workflow")
	if !ok || class == "semantic" {
		t.Skipf("the packaged contract routes workflow to %q; the fixture needs a class other than semantic", class)
	}
	misfiled := "---\ntitle: a procedure filed as a fact\ntype: workflow\nstatus: active\n---\n\nFirst do this, then that.\n"
	writeRaw(t, root, "memory/semantic/misfiled.md", misfiled)
	writeRaw(t, root, "memory/semantic/stale-flag.md", "---\ntitle: flagged\ntype: fact\nstatus: active\nreview_flags: [near-duplicate]\nrelated: memory/semantic/gone.md\n---\n\nA fact whose twin is gone.\n")
	writeRaw(t, root, "memory/semantic/live-flag.md", "---\ntitle: flagged\ntype: fact\nstatus: active\nreview_flags: [near-duplicate]\nrelated: memory/semantic/twin.md\n---\n\nA fact whose twin is here.\n")
	writeRaw(t, root, "memory/semantic/twin.md", "---\ntitle: twin\ntype: fact\nstatus: active\n---\n\nA fact, nearly.\n")
	writeRaw(t, root, "memory/semantic/record.md", "---\ntitle: a record\nkind: day-index\n---\n\nNot a memory.\n")
	writeRaw(t, root, "memory/"+class+"/taken.md", "---\ntitle: taken\ntype: workflow\nstatus: active\n---\n\nAlready here.\n")
	writeRaw(t, root, "memory/semantic/taken.md", "---\ntitle: clash\ntype: workflow\nstatus: active\n---\n\nWants the same basename.\n")
	plan, err := PlanRefile(root, r)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Moves) != 1 || plan.Moves[0].Rel != "memory/semantic/misfiled.md" || plan.Moves[0].To != "memory/"+class+"/misfiled.md" {
		t.Errorf("moves = %+v", plan.Moves)
	}
	if len(plan.Blocked) != 1 || plan.Blocked[0].Rel != "memory/semantic/taken.md" {
		t.Errorf("blocked = %+v", plan.Blocked)
	}
	if len(plan.Unflags) != 1 || plan.Unflags[0].Rel != "memory/semantic/stale-flag.md" {
		t.Errorf("unflags = %+v (the live flag stays)", plan.Unflags)
	}
	if plan.Considered != 6 {
		t.Errorf("considered = %d, want 6 memories (the record kind excluded)", plan.Considered)
	}
	if nilPlan, _ := PlanRefile(root, nil); len(nilPlan.Moves) != 0 {
		t.Errorf("no contract, no moves: %+v", nilPlan.Moves)
	}

	// The plan's verification: a planted write-path misfiling is corrected by
	// the next pass — the note lands in the class the contract names, byte
	// for byte, and the old path is gone. The holder resolves the packaged
	// contract for a root that carries none, the way the binary does.
	cfg, _ := scratchConfig(t)
	cfg.VaultPath = root
	cfg.Rules = rules.NewHolder(root, time.Now())
	rep, err := Run(cfg, Options{Apply: true, Force: true, Now: time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC)})
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.Refile.Moves) != 1 || len(rep.Refile.Unflags) != 1 || rep.Skipped != 0 {
		t.Errorf("the pass should have moved one and unflagged one: %+v (skipped %d)", rep.Refile, rep.Skipped)
	}
	if _, err := os.Stat(filepath.Join(root, "memory/semantic/misfiled.md")); !os.IsNotExist(err) {
		t.Errorf("the misfiled note should have left semantic/")
	}
	if got, err := os.ReadFile(filepath.Join(root, "memory", class, "misfiled.md")); err != nil || string(got) != misfiled {
		t.Errorf("the note should sit in %s/ byte for byte: %v %q", class, err, got)
	}
	if got, _ := os.ReadFile(filepath.Join(root, "memory/semantic/stale-flag.md")); strings.Contains(string(got), "review_flags") || strings.Contains(string(got), "related:") {
		t.Errorf("the stale flag should be cleared: %q", got)
	}
	if got, _ := os.ReadFile(filepath.Join(root, "memory/semantic/live-flag.md")); !strings.Contains(string(got), "review_flags: [near-duplicate]") {
		t.Errorf("a flag whose twin exists is the reviewer's, not this pass's")
	}
}

func TestPromoteNeedsThreeDistinctSourcesOutsideCodeAndNeverOverwrites(t *testing.T) {
	root := t.TempDir()
	writeRaw(t, root, "memory/episodic/e1.md", "---\ntitle: one\nkind: session-trace\nstatus: active\n---\n\nWorked on [[shared-target]] today.\n\n```\nA fenced [[fenced-target]] is not a link.\n```\n")
	writeRaw(t, root, "memory/episodic/e2.md", "---\ntitle: two\nkind: session-trace\nstatus: active\n---\n\nBack to [[shared-target]] and a code span `[[code-target]]` that is not one either.\n")
	writeRaw(t, root, "memory/episodic/e3.md", "---\ntitle: three\nkind: session-trace\nstatus: active\nsupersedes: [[sup-target]]\n---\n\nFinished [[shared-target|the target]]; see also [[other#section]] once. And [[shared-target]] again.\n")
	writeRaw(t, root, "memory/semantic/not-episodic.md", "---\ntitle: s\nstatus: active\n---\n\n[[shared-target]] [[other]] [[other]]\n")
	recurring, read, err := RecurringTargets(root, 0)
	if err != nil {
		t.Fatal(err)
	}
	if read != 3 {
		t.Errorf("read %d episodic notes, want 3", read)
	}
	if len(recurring) != 1 || strings.Join(recurring["shared-target"], ",") != "memory/episodic/e1.md,memory/episodic/e2.md,memory/episodic/e3.md" {
		t.Errorf("recurring = %v — three distinct sources, twice in one note counts once, fenced and code-span links never", recurring)
	}
	// A wikilinked frontmatter value is seen twice — once by the wikilink
	// scan over the whole text, once by the frontmatter pass — exactly as
	// graph.py sees it; recurrence counts distinct sources, so it is harmless.
	got := Edges("---\nsupersedes: [[sup-target]]\nsuperseded_by: memory/x.md\n---\nbody\n")
	seen := map[string]bool{}
	for _, g := range got {
		seen[g] = true
	}
	if !seen["sup-target"] || !seen["memory/x.md"] || len(seen) != 2 {
		t.Errorf("frontmatter supersession edges = %v", got)
	}
	plan, err := PlanPromote(root, time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Promotions) != 1 || plan.Promotions[0].Rel != "memory/crystallized/consolidated-shared-target.md" {
		t.Fatalf("promotions = %+v", plan.Promotions)
	}
	content := string(plan.Intents[0].After)
	for _, want := range []string{
		"kind: crystallized\n", "status: active\n", "altitude: artifact\n", "created: 2026-09-05\n", "updated: 2026-09-05\n",
		"slug: consolidated-shared-target\n", "lifecycle_tier: durable\n",
		"derived_from: [memory/episodic/e1.md, memory/episodic/e2.md, memory/episodic/e3.md]\n",
		"consolidated_from: [memory/episodic/e1.md, memory/episodic/e2.md, memory/episodic/e3.md]\n",
		"## Question\n\nWhat recurring reference to 'shared-target' appears across episodic entries?\n",
		"## Investigation\n\n3 episodic entries reference 'shared-target':\n- memory/episodic/e1.md\n- memory/episodic/e2.md\n- memory/episodic/e3.md\n",
		"## Findings\n\n'shared-target' recurs across 3 distinct entries (recurrence floor: 3), a deterministic signal that this is durable, not incidental.\n",
		"## Open threads\n\n\n",
	} {
		if !strings.Contains(content, want) {
			t.Errorf("the consolidated note lacks %q:\n%s", want, content)
		}
	}
	if plan.Intents[0].Before != nil {
		t.Errorf("a promotion creates: Before must be nil")
	}
	// Already promoted: never overwritten, reported instead.
	writeRaw(t, root, "memory/crystallized/consolidated-shared-target.md", "---\nkind: crystallized\nconsolidated_from: [x]\n---\n\nsomeone's edits\n")
	again, _ := PlanPromote(root, time.Now())
	if len(again.Promotions) != 0 || len(again.Existing) != 1 {
		t.Errorf("existing entry: promotions=%v existing=%v", again.Promotions, again.Existing)
	}
	if ConsolidatedSlug("Shared Target (v2).md") != "consolidated-shared-target-v2" {
		t.Errorf("slug = %s", ConsolidatedSlug("Shared Target (v2).md"))
	}
	if pyRepr("it's") != `"it's"` || pyRepr("plain") != "'plain'" {
		t.Errorf("repr: %s %s", pyRepr("it's"), pyRepr("plain"))
	}
}

func TestTheJournalResumesMovesAndCreations(t *testing.T) {
	root := t.TempDir()
	state := t.TempDir()
	j, _ := OpenJournal(state)
	now := time.Now().UTC()
	raw := []byte("---\ntitle: m\ntype: workflow\n---\n\nbody\n")
	writeRaw(t, root, "memory/semantic/m.md", string(raw))
	move := Entry{Kind: KindIntent, RunID: "r", ID: "r-1", Job: JobRefile, Rel: "memory/semantic/m.md", To: "memory/procedural/m.md",
		BeforeHash: Hash(raw), AfterHash: Hash(raw), After: base64.StdEncoding.EncodeToString(raw)}
	if kind, _ := j.Resolve(root, move, now); kind != KindApplied {
		t.Fatalf("a move with the source at `before` and no destination is applied on resume: %s", kind)
	}
	if _, err := os.Stat(filepath.Join(root, "memory/semantic/m.md")); !os.IsNotExist(err) {
		t.Errorf("the source should be gone after the move")
	}
	if kind, _ := j.Resolve(root, move, now); kind != KindApplied {
		t.Errorf("a move already made is found applied: %s", kind)
	}
	writeRaw(t, root, "memory/semantic/m.md", "a new note took the old path\n")
	if kind, _ := j.Resolve(root, move, now); kind != KindSkipped {
		t.Errorf("both paths present is a conflict, skipped: %s", kind)
	}
	created := []byte("---\nkind: crystallized\nconsolidated_from: [a, b, c]\n---\n\ncard\n")
	create := Entry{Kind: KindIntent, RunID: "r", ID: "r-2", Job: JobPromote, Rel: "memory/crystallized/c.md", Create: true,
		BeforeHash: Hash(nil), AfterHash: Hash(created), After: base64.StdEncoding.EncodeToString(created)}
	if kind, _ := j.Resolve(root, create, now); kind != KindApplied {
		t.Errorf("a creation with nothing at the path is applied on resume: %s", kind)
	}
	if got, _ := os.ReadFile(filepath.Join(root, "memory/crystallized/c.md")); string(got) != string(created) {
		t.Errorf("created content = %q", got)
	}
	if kind, _ := j.Resolve(root, create, now); kind != KindApplied {
		t.Errorf("a creation already made is found applied: %s", kind)
	}
	writeRaw(t, root, "memory/crystallized/c.md", "edited since\n")
	if kind, _ := j.Resolve(root, create, now); kind != KindSkipped {
		t.Errorf("an edited creation is a conflict, skipped: %s", kind)
	}
	// Commit refuses to create over an existing note or move onto a taken path.
	if kind, _ := j.Commit(root, "r", "r-3", Intent{Job: JobPromote, Rel: "memory/crystallized/c.md", After: created}, now); kind != KindSkipped {
		t.Errorf("creating over an existing note must skip: %s", kind)
	}
}

// governanceLines counts the parseable governance-journal lines for one note.
func governanceLines(t *testing.T, state, rel string) int {
	t.Helper()
	blob, err := os.ReadFile(filepath.Join(state, LifecycleJournalName))
	if err != nil {
		return 0
	}
	n := 0
	for _, line := range strings.Split(string(blob), "\n") {
		var m map[string]any
		if json.Unmarshal([]byte(line), &m) == nil && m["rel"] == rel {
			n++
		}
	}
	return n
}

// The window that bit CI: a kill after the applied line had been fsynced
// but before the governance line was written left a note the resume, which
// only revisits pending intents, could never close. The order is now
// governance line, then applied line, so every kill point leaves a state
// Resolve finishes — and the line is written exactly once whichever side
// wrote it.
func TestAKillAroundTheGovernanceLineIsClosedByResumeExactlyOnce(t *testing.T) {
	root := t.TempDir()
	state := t.TempDir()
	j, _ := OpenJournal(state)
	now := time.Now().UTC()
	before := []byte("---\ntitle: n\ntype: workflow\nlifecycle: active\n---\n\nbody\n")
	after := []byte("---\ntitle: n\ntype: workflow\nlifecycle: dormant\nlifecycle_since: 2026-09-05\n---\n\nbody\n")
	meta := map[string]string{"from": "active", "to": "dormant", "reason": "silent 400 days"}
	if err := j.Append(Entry{Kind: KindRunStart, RunID: "r", TS: now, Mode: "apply"}); err != nil {
		t.Fatal(err)
	}

	// Kill between the governance line and the applied line.
	writeRaw(t, root, "memory/procedural/a.md", string(before))
	killed := errors.New("killed")
	j.crashBeforeApplied = func() error { return killed }
	if _, err := j.Commit(root, "r", "r-1", Intent{Job: JobLifecycle, Rel: "memory/procedural/a.md", Before: before, After: after, Summary: meta["reason"], Meta: meta}, now); !errors.Is(err, killed) {
		t.Fatalf("the stand-in kill should surface from Commit: %v", err)
	}
	j.crashBeforeApplied = nil
	if got, _ := os.ReadFile(filepath.Join(root, "memory/procedural/a.md")); string(got) != string(after) {
		t.Fatalf("the write landed before the kill: %q", got)
	}
	if n := governanceLines(t, state, "memory/procedural/a.md"); n != 1 {
		t.Fatalf("governance lines before the applied line = %d, want 1 — the governance line must precede the applied line", n)
	}
	entries, _ := j.Read()
	runID, pending := Unfinished(entries)
	if runID != "r" || len(pending) != 1 || pending[0].Rel != "memory/procedural/a.md" {
		t.Fatalf("the killed intent is what the resume finds pending: run %q, %d pending", runID, len(pending))
	}
	if kind, err := j.Resolve(root, pending[0], now); kind != KindApplied || err != nil {
		t.Fatalf("the resume settles it applied: %s %v", kind, err)
	}
	if n := governanceLines(t, state, "memory/procedural/a.md"); n != 1 {
		t.Errorf("governance lines after the resume = %d, want exactly 1 (idempotent by run, note and state)", n)
	}

	// Kill between the write and the governance line: the intent is pending,
	// the note is already at `after`, no governance line yet.
	writeRaw(t, root, "memory/procedural/b.md", string(after))
	intent := Entry{Kind: KindIntent, RunID: "r", ID: "r-2", Job: JobLifecycle, Rel: "memory/procedural/b.md",
		BeforeHash: Hash(before), AfterHash: Hash(after), After: base64.StdEncoding.EncodeToString(after), Meta: meta}
	if kind, err := j.Resolve(root, intent, now); kind != KindApplied || err != nil {
		t.Fatalf("a note found at `after` is applied on resume: %s %v", kind, err)
	}
	if n := governanceLines(t, state, "memory/procedural/b.md"); n != 1 {
		t.Errorf("the resume writes the governance line the pass never reached: %d, want 1", n)
	}
	entries, _ = j.Read()
	if _, pending := Unfinished(entries); len(pending) != 0 {
		t.Errorf("%d intent(s) still pending after the resume", len(pending))
	}

	// A kill mid-append leaves a torn governance line; the next line must not
	// be glued onto the fragment.
	p := filepath.Join(state, LifecycleJournalName)
	f, _ := os.OpenFile(p, os.O_APPEND|os.O_WRONLY, 0o644)
	_, _ = f.WriteString(`{"actor":"policy","rel":"memory/procedural/c.md","to":"dor`)
	_ = f.Close()
	if err := EnsureLifecycleJournal(state, "memory/procedural/c.md", "active", "dormant", "silent", "r", now); err != nil {
		t.Fatal(err)
	}
	if n := governanceLines(t, state, "memory/procedural/c.md"); n != 1 {
		t.Errorf("the line after a torn tail parses on its own: %d, want 1", n)
	}
	if n := governanceLines(t, state, "memory/procedural/a.md") + governanceLines(t, state, "memory/procedural/b.md"); n != 2 {
		t.Errorf("earlier lines untouched: %d, want 2", n)
	}
}
