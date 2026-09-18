package dreaming

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// The three free jobs the axis added, and the facet that reports them.

func writeFile(t *testing.T, root, rel, body string) string {
	t.Helper()
	p := filepath.Join(root, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return rel
}

// ── retain ───────────────────────────────────────────────────────────────────

func TestRetentionRemovesItsOwnPaperAndNothingElse(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)
	contract := axisContract(t)

	old := writeFile(t, root, "diagnostics/digests/20260101-digest-daily.md", "a day\n")
	writeFile(t, root, "diagnostics/digests/20260901-digest-daily.md", "a recent day\n")
	// A weekly digest of the same age is kept: 260 days is past 90 and inside
	// 365, which is the whole point of a per-kind line.
	writeFile(t, root, "diagnostics/digests/20260101-digest-weekly.md", "a week\n")
	oldMorning := writeFile(t, root, "diagnostics/morning/2026-01-01.md", "a morning\n")
	// The record of what moved and what was forgotten. Nothing prunes it.
	writeFile(t, root, "diagnostics/migrations/purge/20200101T000000Z/manifest.json", "{}\n")
	writeFile(t, root, "diagnostics/migrations/20200101-something.md", "a migration record\n")
	// A memory is not the night's paper.
	writeFile(t, root, "memory/semantic/a-fact.md", "---\nkind: note\n---\n\nbody\n")

	plan, err := PlanRetain(root, contract, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	var removed []string
	for _, r := range plan.Removed {
		removed = append(removed, r.Rel)
	}
	want := []string{old, oldMorning}
	if strings.Join(removed, ",") != strings.Join(want, ",") {
		t.Errorf("removed %v, want %v", removed, want)
	}
	for _, in := range plan.Intents {
		if !in.Delete {
			t.Errorf("%s: retention produced something that is not a deletion", in.Rel)
		}
	}
}

func TestRetentionKeepsAFileWithNoDateInItsName(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)
	contract := axisContract(t)
	writeFile(t, root, "diagnostics/morning/latest_morning_note.md", "the current one\n")
	writeFile(t, root, "diagnostics/lint/report.md", "no date at all\n")

	plan, err := PlanRetain(root, contract, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Removed) != 0 {
		t.Errorf("removed %d file(s) whose age is not knowable from the name", len(plan.Removed))
	}
	if plan.Undated != 2 {
		t.Errorf("undated = %d, want the two counted rather than guessed at", plan.Undated)
	}
}

func TestRetentionWithNoContractRemovesNothing(t *testing.T) {
	root := t.TempDir()
	writeFile(t, root, "diagnostics/digests/20200101-digest-daily.md", "ancient\n")
	plan, err := PlanRetain(root, nil, time.Now(), 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Removed) != 0 {
		t.Error("a missing contract deleted something; the safe direction is to keep")
	}
}

// ── sequence ─────────────────────────────────────────────────────────────────

func TestSequenceNumbersByTheCreatedDate(t *testing.T) {
	vault := t.TempDir()
	root := filepath.Join(vault, "agent")
	if err := os.MkdirAll(root, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(vault, ".obsidian"), 0o755); err != nil {
		t.Fatal(err)
	}
	dir := filepath.Join(vault, "projects", "agentm", "decisions")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	write := func(name, created string) {
		body := "---\nkind: note\n"
		if created != "" {
			body += "created: " + created + "\n"
		}
		body += "---\n\nbody\n"
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	// The names are deliberately in the opposite order to the dates: a
	// directory listing is alphabetical, so a fixture whose two orders agree
	// cannot tell the two apart, and this test would pass against a job that
	// numbered by name.
	write("007-already-numbered.md", "2026-01-01")
	write("alpha.md", "2026-06-01")
	write("zulu.md", "2026-03-01")
	write("mike-undated.md", "")

	plan, err := PlanSequence(root, ProjectsRoot(root), time.Now(), 0)
	if err != nil {
		t.Fatal(err)
	}
	if plan.Already != 1 {
		t.Errorf("already numbered = %d, want the one", plan.Already)
	}
	var got []string
	for _, r := range plan.Numbered {
		got = append(got, filepath.Base(r.To))
	}
	want := []string{"008-zulu.md", "009-alpha.md", "010-mike-undated.md"}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("numbered %v, want %v — by the created date, continuing from the "+
			"highest already taken, with the undated file last", got, want)
	}
	for _, in := range plan.Intents {
		if in.To == "" {
			t.Errorf("%s: numbering produced an edit rather than a rename", in.Rel)
		}
		if string(in.Before) != string(in.After) {
			t.Errorf("%s: the rename changed the file's bytes", in.Rel)
		}
	}
}

func TestSequenceLeavesASettledFolderAlone(t *testing.T) {
	vault := t.TempDir()
	root := filepath.Join(vault, "agent")
	os.MkdirAll(root, 0o755)
	os.MkdirAll(filepath.Join(vault, ".obsidian"), 0o755)
	dir := filepath.Join(vault, "projects", "agentm", "research")
	os.MkdirAll(dir, 0o755)
	for _, n := range []string{"001-a.md", "002-b.md"} {
		os.WriteFile(filepath.Join(dir, n), []byte("---\nkind: note\n---\n\nbody\n"), 0o644)
	}
	plan, err := PlanSequence(root, ProjectsRoot(root), time.Now(), 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Numbered) != 0 {
		t.Errorf("numbered %d in a folder that is already numbered", len(plan.Numbered))
	}
}

// ── reconcile ────────────────────────────────────────────────────────────────

func TestReconcileFollowsAMoveByItsBody(t *testing.T) {
	known := map[string]string{"memory/semantic/a-fact.md": "abc123"}
	onDisk := map[string]string{"archive/memory/semantic/a-fact.md": "abc123"}
	plan := PlanReconcile(known, onDisk, nil, time.Now())
	if len(plan.Repaired) != 1 ||
		plan.Repaired[0].From != "memory/semantic/a-fact.md" ||
		plan.Repaired[0].To != "archive/memory/semantic/a-fact.md" {
		t.Errorf("repaired = %+v, want the one pairing", plan.Repaired)
	}
}

func TestReconcileRefusesToGuessBetweenTwins(t *testing.T) {
	// Two files carrying the same body is a copy, not a move, and pairing it
	// would pick one at random.
	known := map[string]string{"memory/semantic/a-fact.md": "abc123"}
	onDisk := map[string]string{
		"memory/semantic/one.md": "abc123",
		"memory/semantic/two.md": "abc123",
	}
	plan := PlanReconcile(known, onDisk, nil, time.Now())
	if len(plan.Repaired) != 0 {
		t.Errorf("repaired = %+v, want nothing: the body is at two paths", plan.Repaired)
	}
	if plan.Vanished != 1 {
		t.Errorf("vanished = %d, want the one counted", plan.Vanished)
	}
}

func TestReconcileLeavesADeletedNoteAlone(t *testing.T) {
	known := map[string]string{"memory/semantic/gone.md": "abc123"}
	plan := PlanReconcile(known, map[string]string{"memory/semantic/other.md": "zzz"}, nil, time.Now())
	if len(plan.Repaired) != 0 {
		t.Errorf("repaired = %+v, want nothing: the note was deleted, not moved", plan.Repaired)
	}
	if plan.Vanished != 1 {
		t.Errorf("vanished = %d, want 1", plan.Vanished)
	}
}

// ── the facet ────────────────────────────────────────────────────────────────

func TestTheFacetNamesEveryActAndTheManifest(t *testing.T) {
	vault := t.TempDir()
	root := filepath.Join(vault, "agent")
	os.MkdirAll(root, 0o755)
	os.MkdirAll(filepath.Join(vault, ".obsidian"), 0o755)
	os.MkdirAll(filepath.Join(vault, "calendar"), 0o755)
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)

	rep := &Report{
		Plan: LifecyclePlan{
			Demoted:  []Move{{"memory/semantic/sank.md", 400}},
			Archived: []Move{{"memory/semantic/cold.md", 2000}},
			Deleted:  []Move{{"archive/memory/semantic/ancient.md", 3000}},
			Returned: []Move{{"memory/semantic/back.md", 0}},
			Touched:  []Move{{"memory/semantic/edited.md", 0}},
		},
		Retain: RetainPlan{Removed: []RetainRow{
			{Rel: "diagnostics/digests/20260101-digest-daily.md", What: "daily digest", Days: 260, Keep: 90}}},
		Sequence: SequencePlan{Numbered: []SequenceRow{
			{From: "../projects/agentm/decisions/a.md", To: "../projects/agentm/decisions/008-a.md"}}},
		Reconcile:         ReconcilePlan{Repaired: []ReconcileRow{{From: "memory/semantic/x.md", To: "archive/memory/semantic/x.md"}}},
		SkippedByHandMove: []string{"memory/semantic/moved-under-us.md"},
		DeletionManifest:  "/state/purge/20260918T090000Z/manifest.json",
	}

	plan := PlanDreamingFacet(root, axisContract(t), rep, now)
	if plan.Skipped != "" {
		t.Fatalf("skipped: %s", plan.Skipped)
	}
	if plan.Rel != "../calendar/2026/2026-09-18-dreaming.md" {
		t.Errorf("facet at %q, want the day's file in the register's year", plan.Rel)
	}
	if len(plan.Intents) != 1 {
		t.Fatalf("%d intents, want the one that writes the facet", len(plan.Intents))
	}
	body := string(plan.Intents[0].After)
	for _, want := range []string{
		"facet: dreaming",
		"## Sank", "sank",
		"## Archived", "archive/memory/semantic/cold.md",
		"## Deleted", "manifest.json",
		"## Removed by retention", "daily digest",
		"## Numbered", "008-a.md",
		"## Skipped, and repaired by reconcile", "moved-under-us",
		"## Followed a move you made",
		"## Left alone", "edited",
	} {
		if !strings.Contains(body, want) {
			t.Errorf("the facet does not name %q", want)
		}
	}
}

func TestANightThatMovedNothingWritesNoFacet(t *testing.T) {
	vault := t.TempDir()
	root := filepath.Join(vault, "agent")
	os.MkdirAll(root, 0o755)
	os.MkdirAll(filepath.Join(vault, ".obsidian"), 0o755)
	os.MkdirAll(filepath.Join(vault, "calendar"), 0o755)

	plan := PlanDreamingFacet(root, axisContract(t), &Report{}, time.Now())
	if len(plan.Intents) != 0 || plan.Acts != 0 {
		t.Error("a quiet night wrote a facet; a facet file exists only on a day " +
			"that had content for it")
	}
}

// A hand move during the run: skipped, named, and repaired the same night.
//
// This is the whole of "the night follows what you moved" in one test. The
// operator moves a file between the moment the pass reads it and the moment it
// writes; the journal's per-write re-check refuses the intent because the
// target no longer hashes as the plan saw it; the facet says so rather than
// swallowing it; and reconcile pairs the vanished path with the file that now
// carries its body.
func TestAHandMoveDuringTheRunIsSkippedNamedAndRepaired(t *testing.T) {
	vault := t.TempDir()
	root := filepath.Join(vault, "agent")
	os.MkdirAll(filepath.Join(vault, ".obsidian"), 0o755)
	os.MkdirAll(filepath.Join(vault, "calendar"), 0o755)
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)
	contract := axisContract(t)

	rel := writeNote(t, root, "memory/semantic/moved-under-us.md", "active", 400, now, "")
	plan, err := PlanLifecycle(root, "", contract, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Intents) != 1 {
		t.Fatalf("%d intents, want the one demotion the fixture earns", len(plan.Intents))
	}
	body, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
	if err != nil {
		t.Fatal(err)
	}

	// The operator moves it, now, between the plan and the write.
	moved := "memory/procedural/moved-under-us.md"
	os.MkdirAll(filepath.Join(root, "memory", "procedural"), 0o755)
	if err := os.Rename(filepath.Join(root, filepath.FromSlash(rel)),
		filepath.Join(root, filepath.FromSlash(moved))); err != nil {
		t.Fatal(err)
	}

	// Applying the plan now: the target is gone, so the intent is refused.
	journal, err := OpenJournal(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	rep := Report{}
	if err := applyAll(journal, root, "run-1", plan.Intents, now, 0, &rep); err != nil {
		t.Fatal(err)
	}
	if rep.Applied != 0 || rep.Skipped != 1 {
		t.Errorf("applied %d skipped %d, want the moved file refused", rep.Applied, rep.Skipped)
	}
	if strings.Join(rep.SkippedByHandMove, ",") != rel {
		t.Errorf("skipped by hand move = %v, want the file that moved", rep.SkippedByHandMove)
	}
	// It is still where the operator put it, unedited.
	if got, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(moved))); err != nil ||
		string(got) != string(body) {
		t.Error("the pass wrote to a file the operator had moved")
	}

	// Reconcile pairs the two by the body, and the facet names both the skip
	// and the repair.
	//
	// `known` is keyed the way the sidecar keys it — from the vault root — and
	// not the way this line used to key it, from the memory root. Built by hand
	// from the same rel the walk returned, the two sides agreed because one
	// value was used twice, and the pass reported every note in the corpus as
	// moved on the first real vault it saw. `reconcile_base_test.go` holds the
	// bases apart against a sidecar on disk; this keeps using a literal, so the
	// literal has to be the sidecar's spelling.
	vaultRel := func(memRel string) string {
		return vaultRelOf(vaultRootOf(root), filepath.Join(root, filepath.FromSlash(memRel)), memRel)
	}
	known := map[string]string{vaultRel(rel): BodyFingerprint(string(body))}
	onDisk, err := FingerprintsOnDisk(root)
	if err != nil {
		t.Fatal(err)
	}
	rep.Reconcile = PlanReconcile(known, onDisk, nil, now)
	if len(rep.Reconcile.Repaired) != 1 || rep.Reconcile.Repaired[0].To != vaultRel(moved) {
		t.Fatalf("reconcile = %+v, want the pairing to %s", rep.Reconcile.Repaired, vaultRel(moved))
	}

	facet := PlanDreamingFacet(root, contract, &rep, now)
	if len(facet.Intents) != 1 {
		t.Fatalf("%d facet intents, want the one", len(facet.Intents))
	}
	text := string(facet.Intents[0].After)
	if !strings.Contains(text, "moved by hand during the run") {
		t.Error("the facet does not say the file moved under the pass")
	}
	if !strings.Contains(text, moved) {
		t.Error("the facet does not say where reconcile found it")
	}
}
