package dreaming

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
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
	writeRaw(t, root, "memory/procedural/settled-copy.md", "---\ntitle: settled\nkind: workflow\nstatus: active\nlifecycle: superseded\nsuperseded_by: memory/procedural/copy-canon.md\n---\n\n"+body)
	writeRaw(t, root, "memory/semantic/_always-load/curated.md", "---\ntitle: curated\nstatus: active\nlifecycle: pinned\n---\n\n"+body)
	writeRaw(t, root, "memory/semantic/lonely.md", "---\ntitle: lonely\nstatus: active\n---\n\nNothing else says this.\n")
	plan, err := PlanCopies(root, 0)
	if err != nil {
		t.Fatal(err)
	}
	if plan.Considered != 4 {
		t.Errorf("considered %d active notes, want 4 (the superseded ones — by status or by lifecycle — and the curated one excluded)", plan.Considered)
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
		// The contract's shape: the loser names its successor on the axis;
		// `status` untouched; never a loser-side `supersedes:`.
		if !strings.Contains(after, "lifecycle: superseded\n") || !strings.Contains(after, "superseded_by: memory/procedural/copy-canon.md\n") ||
			strings.Contains(after, "status: superseded") || strings.Contains(after, "supersedes:") {
			t.Errorf("%s: after = %q", in.Rel, after)
		}
		if in.Meta["to"] != "superseded" || in.Meta["from"] != "active" {
			t.Errorf("%s: the intent says where the axis moved: %v", in.Rel, in.Meta)
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

// trace is a session trace in the shape episodic_trace.py writes: the five
// sections, the ones with nothing in them left out.
func trace(session, captured, recalled, candidates string) string {
	var b strings.Builder
	b.WriteString("---\ntitle: " + session + "\nkind: session-trace\nstatus: active\nsession: " +
		session + "\n---\n\n## Asked\n\nwork on the vault\n\n## Outcome\n\nDone.\n\n")
	if captured != "" {
		b.WriteString("## Captured\n\n" + captured + "\n\n")
	}
	if recalled != "" {
		b.WriteString("## Recalled\n\n" + recalled + "\n\n")
	}
	if candidates != "" {
		b.WriteString("## Candidates\n\n" + candidates + "\n")
	}
	return b.String()
}

// The five fixture traces (agentm-vault plan 04, task 4). Every one recalls
// `zorbulax`, a test token that reached the recall index; three capture the
// same card; three say the same thing in passing, in three spellings; two say
// something else.
func writeFixtureTraces(t *testing.T, root string) {
	t.Helper()
	recalled := "- [[zorbulax]]\n- [[talk]]\n- [[progress]]\n- [[roadmap]]"
	say := []string{
		"- preference (×2) — “Always run the full battery before a commit.”",
		"- preference — “always run the full battery before a commit”",
		"- correction (×3) — “Always run the FULL battery, before a commit!”",
	}
	for i := 1; i <= 5; i++ {
		captured, candidates := "", ""
		if i <= 3 {
			captured = "- [[keep-git-out-of-drive]]"
			candidates = say[i-1]
		} else {
			candidates = "- fix — “the daemon restarts on the old binary”"
		}
		writeRaw(t, root, fmt.Sprintf("memory/episodic/2026-09-0%d-session-%d.md", i, i),
			trace(fmt.Sprintf("s%d", i), captured, recalled, candidates))
	}
	writeRaw(t, root, "memory/semantic/keep-git-out-of-drive.md",
		"---\ntitle: Keep git out of Drive\ntype: preference\nstatus: active\n---\n\nbody\n")
}

func TestPromoteReadsCapturedAndCandidatesNeverRecalled(t *testing.T) {
	root := t.TempDir()
	writeFixtureTraces(t, root)
	plan, err := PlanPromote(root, nil, time.Date(2026, 9, 12, 2, 30, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if plan.Sources != 5 {
		t.Errorf("read %d traces, want the five", plan.Sources)
	}
	// Nothing for `zorbulax`, or for anything else only `## Recalled` names —
	// five traces carry it, and recurrence over a recall list is a popularity
	// counter over what the hook happened to inject.
	for _, in := range plan.Intents {
		if strings.Contains(string(in.After), "zorbulax") || strings.Contains(in.Rel, "zorbulax") {
			t.Errorf("promote wrote from the recall list: %s", in.Rel)
		}
	}
	for _, p := range append(append([]Promotion{}, plan.Promotions...), plan.Existing...) {
		if strings.Contains(p.Target, "zorbulax") || p.Target == "talk" || p.Target == "roadmap" {
			t.Errorf("a recall-list target recurred: %+v", p)
		}
	}
	// Nothing crystallized, ever.
	for _, in := range plan.Intents {
		if strings.Contains(in.Rel, "crystallized") {
			t.Errorf("promote wrote into crystallized/: %s", in.Rel)
		}
	}
	// One candidate: the thing said in three sessions, in three spellings.
	if len(plan.Promotions) != 1 {
		t.Fatalf("promotions = %+v, want the one recurring candidate", plan.Promotions)
	}
	got := plan.Promotions[0]
	if got.Rel != "memory/semantic/candidate-always-run-the-full-battery-before-a-commit.md" ||
		len(got.Sources) != 3 {
		t.Errorf("the candidate = %+v", got)
	}
	// The card three sessions captured already exists: reported, not written.
	if len(plan.Existing) != 1 || plan.Existing[0].Target != "keep-git-out-of-drive" {
		t.Errorf("existing = %+v, want the captured card reported", plan.Existing)
	}
}

// The candidate is unjudged, carries no why, and names the traces it came from.
func TestAPromotedCandidateIsUnfiledWithNoWhyAndItsSources(t *testing.T) {
	root := t.TempDir()
	writeFixtureTraces(t, root)
	plan, err := PlanPromote(root, nil, time.Date(2026, 9, 12, 2, 30, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	content := string(plan.Intents[0].After)
	fm, body := ParseFrontmatter(content)
	if fm["status"] != "unfiled" || fm["kind"] != "" {
		t.Errorf("a candidate is unfiled and not a record: %+v", fm)
	}
	if _, has := fm["why"]; has {
		t.Error("promote wrote a why; only a writer that knows writes one")
	}
	for i := 1; i <= 3; i++ {
		if !strings.Contains(fm["derived_from"], fmt.Sprintf("2026-09-0%d-session-%d", i, i)) {
			t.Errorf("derived_from does not name trace %d: %q", i, fm["derived_from"])
		}
	}
	if !strings.Contains(body, "## Evidence") || !strings.Contains(body, "Always run the full battery") {
		t.Errorf("the candidate does not quote what was said:\n%s", body)
	}
	if plan.Intents[0].Before != nil {
		t.Error("a promotion creates: Before must be nil")
	}

	// A candidate written before is never overwritten.
	writeRaw(t, root, plan.Promotions[0].Rel, "---\ntitle: edited\nstatus: active\n---\n\nmine\n")
	again, _ := PlanPromote(root, nil, time.Now())
	if len(again.Promotions) != 0 || len(again.Existing) != 2 {
		t.Errorf("an existing candidate: promotions=%v existing=%v", again.Promotions, again.Existing)
	}
}

// Two sessions are not a recurrence; three are, and a line inside a fence or a
// section that is not Captured or Candidates never counts.
func TestPromoteNeedsThreeDistinctSessions(t *testing.T) {
	root := t.TempDir()
	line := "- preference — “keep the plan on disk”"
	writeRaw(t, root, "memory/episodic/a.md", trace("a", "", "", line))
	writeRaw(t, root, "memory/episodic/b.md", trace("b", "", "", line))
	writeRaw(t, root, "memory/episodic/c.md",
		trace("c", "", "", "```\n"+line+"\n```")+"\n## Outcome\n\n"+line+"\n")
	writeRaw(t, root, "memory/semantic/not-a-trace.md",
		"---\ntitle: x\nstatus: active\n---\n\n## Candidates\n\n"+line+"\n")
	plan, err := PlanPromote(root, nil, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Promotions) != 0 {
		t.Errorf("two sessions promoted a candidate: %+v", plan.Promotions)
	}
	writeRaw(t, root, "memory/episodic/d.md", trace("d", "", "", line))
	plan, _ = PlanPromote(root, nil, time.Now())
	if len(plan.Promotions) != 1 || len(plan.Promotions[0].Sources) != 3 {
		t.Errorf("three sessions did not promote it: %+v", plan.Promotions)
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

// A collapse is a move along the axis like any other, and the lifecycle
// journal records it through the intent's Meta — the same line the policy's
// sinks write, written once.
func TestACopiesIntentLandsInTheLifecycleJournal(t *testing.T) {
	root := t.TempDir()
	state := t.TempDir()
	j, _ := OpenJournal(state)
	now := time.Now().UTC()
	before := []byte("---\ntitle: c\nkind: workflow\nstatus: active\n---\n\nbody\n")
	after := []byte("---\ntitle: c\nkind: workflow\nstatus: active\nlifecycle: superseded\nsuperseded_by: memory/procedural/canon.md\n---\n\nbody\n")
	writeRaw(t, root, "memory/procedural/c.md", string(before))
	if err := j.Append(Entry{Kind: KindRunStart, RunID: "r", TS: now, Mode: "apply"}); err != nil {
		t.Fatal(err)
	}
	in := Intent{Job: JobCopies, Rel: "memory/procedural/c.md", Before: before, After: after, Summary: "collapse",
		Meta: map[string]string{"from": "active", "to": "superseded", "reason": "content-identical copy of memory/procedural/canon.md"}}
	if kind, err := j.Commit(root, "r", "r-1", in, now); kind != KindApplied || err != nil {
		t.Fatalf("commit: %s %v", kind, err)
	}
	if n := governanceLines(t, state, "memory/procedural/c.md"); n != 1 {
		t.Fatalf("governance lines = %d, want 1", n)
	}
}

// The scope is a ruling, not an inherited default (2026-09-06). Measured on
// the live corpus that day, the only content-identical families outside the
// memory classes were the two `latest_*` scorecard mirrors, whose whole job
// is to be byte-identical to the file they point at. A job that collapsed
// those would break the pointer on its first applying pass.
func TestTheCopiesJobOwnsTheMemoryClassesOnly(t *testing.T) {
	root := t.TempDir()
	same := "---\nkind: telemetry\nstatus: active\n---\n\nThe same spend line, twice.\n"
	writeRaw(t, root, "diagnostics/health/2026-09-03-health-scorecard.md", same)
	writeRaw(t, root, "diagnostics/health/latest_health_scorecard.md", same)

	plan, err := PlanCopies(root, 0)
	if err != nil {
		t.Fatalf("PlanCopies: %v", err)
	}
	if len(plan.Families) != 0 {
		t.Errorf("a mirror outside the memory classes was proposed for collapse: %+v", plan.Families)
	}
	if plan.Considered != 0 {
		t.Errorf("Considered = %d; nothing outside the memory classes is fingerprinted", plan.Considered)
	}
	if plan.Population != CopiesPopulation {
		t.Errorf("the report names its population %q, want %q", plan.Population, CopiesPopulation)
	}

	// The same two bytes inside the memory classes are a family, so the test
	// above is about the scope and not about the fingerprinting being broken.
	memory := "---\nkind: reference\nstatus: active\nlifecycle: active\n---\n\nThe same wording, twice.\n"
	writeRaw(t, root, "memory/semantic/a.md", memory)
	writeRaw(t, root, "memory/semantic/b.md", memory)
	plan, err = PlanCopies(root, 0)
	if err != nil {
		t.Fatalf("PlanCopies: %v", err)
	}
	if len(plan.Families) != 1 {
		t.Fatalf("the same pair inside the memory classes gave %d families", len(plan.Families))
	}
}
