package dreaming

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// The axis, end to end on a fixture corpus: what moves, what does not, and what
// is written before a file goes.
//
// The three acts this covers were not on the axis before. Archive was a
// candidate list the job never acted on and deletion was not on the axis at
// all — "a purge is an operator act with a manifest, never a policy outcome".
// Session 5 reversed both, on the reasoning that a stage nobody confirms is a
// stage that never happens, and bounded the reversal by naming the folders the
// machinery may touch and requiring a manifest and a journal line before every
// deletion. These are the tests of that boundary.

// axisContract is the shipped contract's lines, so the fixture runs on the
// numbers the operator set rather than on this package's defaults.
func axisContract(t *testing.T) *rules.Rules {
	t.Helper()
	r, err := rules.Load("")
	if err != nil {
		t.Fatalf("the shipped contract does not parse: %v", err)
	}
	return r
}

func relsOf(ms []Move) []string {
	out := make([]string, 0, len(ms))
	for _, m := range ms {
		out = append(out, m.Rel)
	}
	return out
}

func TestTheAxisMovesExactlyWhatIsPastEachLine(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)
	contract := axisContract(t)

	// Past the sink line, and not.
	sinks := writeNote(t, root, "memory/semantic/silent.md", "active", 400, now, "")
	writeNote(t, root, "memory/semantic/recent.md", "active", 100, now, "")
	// A rule decays and never sinks: one that has not come up in a year is
	// still the rule.
	writeNote(t, root, "memory/semantic/a-preference.md", "active", 900, now, "type: preference\n")
	writeNote(t, root, "memory/procedural/a-convention.md", "active", 900, now, "type: convention\n")
	// Past the archive line: moved now, not merely named.
	archives := writeNote(t, root, "memory/semantic/cold.md", "dormant", 2000, now, "")
	// A trace runs the shorter line the contract's overrides set: dormant at
	// 90 where a card sinks at 365.
	trace := writeNote(t, root, "memory/episodic/a-trace.md", "active", 120, now, "")
	writeNote(t, root, "memory/episodic/a-fresh-trace.md", "active", 40, now, "")
	// The classes the machinery does not move, however old.
	writeNote(t, root, "memory/crystallized/a-lesson.md", "active", 4000, now, "")
	writeNote(t, root, "memory/entities/someone.md", "active", 4000, now, "")
	writeNote(t, root, "memory/mocs/a-map.md", "active", 4000, now, "")
	// Pinned is the one word that exempts.
	writeNote(t, root, "memory/semantic/pinned.md", "pinned", 4000, now, "")

	plan, err := PlanLifecycle(root, "", contract, now, 0)
	if err != nil {
		t.Fatal(err)
	}

	if got := relsOf(plan.Demoted); strings.Join(got, ",") != trace+","+sinks {
		t.Errorf("demoted = %v, want the silent card and the silent trace only", got)
	}
	if got := relsOf(plan.Archived); strings.Join(got, ",") != archives {
		t.Errorf("archived = %v, want the dormant note past the archive line", got)
	}
	if len(plan.Deleted) != 0 {
		t.Errorf("deleted = %v, want nothing: no note here is in the archive", relsOf(plan.Deleted))
	}

	// The list is not the act. Naming a note archived while moving nothing is
	// the arrangement this landing replaced, so the intent is asserted too:
	// where the file goes, and that it is stamped on the way.
	var moved bool
	for _, in := range plan.Intents {
		if in.Rel != archives {
			continue
		}
		moved = true
		if in.To != "archive/"+archives {
			t.Errorf("the archive intent moves to %q, want the mirrored archive path", in.To)
		}
		if !strings.Contains(string(in.After), "lifecycle: archived") {
			t.Error("the note is moved without being stamped archived")
		}
	}
	if !moved {
		t.Error("the note past the archive line was named and not moved")
	}
}

func TestATraceRunsTheShorterLine(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)
	contract := axisContract(t)

	// 120 days: past `episodic`'s 90 and nowhere near a card's 365.
	writeNote(t, root, "memory/episodic/a-trace.md", "active", 120, now, "")
	writeNote(t, root, "memory/semantic/a-card.md", "active", 120, now, "")

	plan, err := PlanLifecycle(root, "", contract, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	if got := relsOf(plan.Demoted); strings.Join(got, ",") != "memory/episodic/a-trace.md" {
		t.Errorf("demoted = %v; the class override is not being read", got)
	}

	// And with no contract, both run the shared line and neither sinks.
	bare, err := PlanLifecycle(root, "", nil, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(bare.Demoted) != 0 {
		t.Errorf("with no contract, demoted = %v, want nothing at 120 days", relsOf(bare.Demoted))
	}
}

func TestADeletionNeedsBothClocks(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)
	contract := axisContract(t)

	// Silent for eight years and archived seven years ago: due.
	due := writeNote(t, root, "archive/memory/semantic/ancient.md", "archived", 3000, now,
		"lifecycle_since: 2019-01-01\n")
	// Silent for eight years and archived this morning — by hand, or by last
	// night's pass. Not due: the wait is from the archive move, not from the
	// silence, or a note archived today would be deleted tonight.
	writeNote(t, root, "archive/memory/semantic/just-archived.md", "archived", 3000, now,
		"lifecycle_since: "+now.Format("2006-01-02")+"\n")
	// In the archive and not yet silent long enough.
	writeNote(t, root, "archive/memory/semantic/young.md", "archived", 2000, now,
		"lifecycle_since: 2020-01-01\n")

	plan, err := PlanLifecycle(root, "", contract, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	if got := relsOf(plan.Deleted); strings.Join(got, ",") != due {
		t.Errorf("deleted = %v, want the one note past both clocks", got)
	}
}

func TestAnArchivedNoteMovedBackReturnsToActive(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)
	contract := axisContract(t)

	// The file is in its class directory and still carries the stamp. Where the
	// file is wins: the operator put it back, and nothing they move is moved
	// back by the night.
	back := writeNote(t, root, "memory/semantic/returned.md", "archived", 4000, now, "")
	writeNote(t, root, "archive/memory/semantic/still-away.md", "archived", 4000, now,
		"lifecycle_since: "+now.Format("2006-01-02")+"\n")

	plan, err := PlanLifecycle(root, "", contract, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	if got := relsOf(plan.Returned); strings.Join(got, ",") != back {
		t.Errorf("returned = %v, want the note sitting in its class", got)
	}
	for _, in := range plan.Intents {
		if in.Rel == back && !strings.Contains(string(in.After), "lifecycle: active") {
			t.Error("the returned note was not stamped active")
		}
	}
}

func TestTheCapHoldsEachActSeparately(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)
	contract := axisContract(t)

	for _, name := range []string{"a", "b", "c"} {
		writeNote(t, root, "memory/semantic/silent-"+name+".md", "active", 400, now, "")
		writeNote(t, root, "memory/semantic/cold-"+name+".md", "dormant", 2000, now, "")
	}

	plan, err := PlanLifecycle(root, "", contract, now, 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Demoted) != 2 {
		t.Errorf("demoted %d, want the cap's 2", len(plan.Demoted))
	}
	if len(plan.Archived) != 2 {
		t.Errorf("archived %d, want the cap's 2", len(plan.Archived))
	}
	if plan.Capped != 2 {
		t.Errorf("capped %d, want the one held back from each act", plan.Capped)
	}
}

func TestTheContractsCapIsRead(t *testing.T) {
	contract := axisContract(t)
	if got := DemotionCap(contract, 0); got != 25 {
		t.Errorf("cap = %d, want the contract's 25", got)
	}
	if got := DemotionCap(contract, 3); got != 3 {
		t.Errorf("cap = %d, want the flag to win for a supervised run", got)
	}
	if got := DemotionCap(nil, 0); got != DefaultDemotionCap {
		t.Errorf("cap = %d, want the package default with no contract", got)
	}
}

// The rule that replaced "no policy outcome ever deletes a memory".
func TestTheManifestIsWrittenBeforeTheFileGoes(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)
	contract := axisContract(t)
	rel := writeNote(t, root, "archive/memory/semantic/ancient.md", "archived", 3000, now,
		"lifecycle_since: 2019-01-01\n")

	plan, err := PlanLifecycle(root, "", contract, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	rows := DeletionRows(root, plan.Intents, plan.Deleted)
	if len(rows) != 1 {
		t.Fatalf("%d manifest rows, want one per deletion", len(rows))
	}
	if rows[0].Rel != rel || rows[0].SHA256 == "" || rows[0].Title == "" {
		t.Errorf("row %+v does not name the note well enough to find it again", rows[0])
	}

	path, err := WriteDeletionManifest(root, "run-1", rows, now)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(filepath.ToSlash(path), PurgeManifestDir) {
		t.Errorf("manifest at %s, want it beside the other purge manifests", path)
	}
	blob, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("the manifest is not on disk: %v", err)
	}
	var back map[string]any
	if err := json.Unmarshal(blob, &back); err != nil {
		t.Fatalf("the manifest is not readable: %v", err)
	}
	if back["count"].(float64) != 1 {
		t.Errorf("manifest counts %v, want 1", back["count"])
	}
	// And the file it names is still there — the manifest comes first.
	if _, err := os.Stat(filepath.Join(root, filepath.FromSlash(rel))); err != nil {
		t.Error("the note was deleted before its manifest was written")
	}
}

func TestTheForwardListsNameWhatIsComing(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)
	contract := axisContract(t)

	// Ten days short of the sink line, and ten short of the archive line.
	sinking := writeNote(t, root, "memory/semantic/nearly-dormant.md", "active", 355, now, "")
	archiving := writeNote(t, root, "memory/semantic/nearly-archived.md", "dormant", 1815, now, "")
	// Well short of either: not coming within thirty days.
	writeNote(t, root, "memory/semantic/fine.md", "active", 100, now, "")

	plan, err := PlanLifecycle(root, "", contract, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	if got := relsOf(plan.SinkingSoon); strings.Join(got, ",") != sinking {
		t.Errorf("sinking within 30 days = %v, want the one note", got)
	}
	if got := relsOf(plan.ArchivingSoon); strings.Join(got, ",") != archiving {
		t.Errorf("archiving within 30 days = %v, want the one note", got)
	}
}

// The operator's own edit of `lifecycle`, made in Obsidian rather than through
// a lane that journals.
func TestAHandEditIsATouchAndTheNightLeavesItAlone(t *testing.T) {
	root := t.TempDir()
	engine := t.TempDir()
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)
	contract := axisContract(t)

	// The night sank it last month; the operator has since put it back to
	// `active` by hand, and the file is four hundred days silent — so without
	// this rule tonight's pass would sink it straight back.
	rel := writeNote(t, root, "memory/semantic/argued-with.md", "active", 400, now, "")
	if err := AppendLifecycleJournal(engine, rel, "active", "dormant",
		"silent 380 days", "run-0", now.AddDate(0, -1, 0)); err != nil {
		t.Fatal(err)
	}

	plan, err := PlanLifecycle(root, engine, contract, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	if got := relsOf(plan.Touched); strings.Join(got, ",") != rel {
		t.Errorf("touched = %v, want the note the operator edited", got)
	}
	if len(plan.Demoted) != 0 {
		t.Errorf("demoted = %v; the night re-sank a note the operator had just "+
			"put back", relsOf(plan.Demoted))
	}
}

// And a note the night itself moved last time is not mistaken for one the
// operator touched.
func TestTheNightsOwnLastMoveIsNotAHandEdit(t *testing.T) {
	root := t.TempDir()
	engine := t.TempDir()
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)
	contract := axisContract(t)

	rel := writeNote(t, root, "memory/semantic/sank.md", "dormant", 2000, now, "")
	if err := AppendLifecycleJournal(engine, rel, "active", "dormant",
		"silent 380 days", "run-0", now.AddDate(0, -1, 0)); err != nil {
		t.Fatal(err)
	}

	plan, err := PlanLifecycle(root, engine, contract, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Touched) != 0 {
		t.Errorf("touched = %v, want nothing: the journal and the file agree",
			relsOf(plan.Touched))
	}
	if got := relsOf(plan.Archived); strings.Join(got, ",") != rel {
		t.Errorf("archived = %v, want the pass to carry on with its own note", got)
	}
}

// planAndApply is one night of the lifecycle pass, applied through a journal
// whose governance lines land in engine.
func planAndApply(t *testing.T, root, engine string, now time.Time) LifecyclePlan {
	t.Helper()
	plan, err := PlanLifecycle(root, engine, axisContract(t), now, 0)
	if err != nil {
		t.Fatal(err)
	}
	journal, err := OpenJournal(engine)
	if err != nil {
		t.Fatal(err)
	}
	if err := applyAll(journal, root, "run-"+now.Format("20060102"), plan.Intents, now, 0, &Report{}); err != nil {
		t.Fatal(err)
	}
	return plan
}

// recallOn records a genuine recall of the note on day, in the sidecar the
// pass reads.
func recallOn(t *testing.T, root, slug string, day time.Time) {
	t.Helper()
	blob, _ := json.Marshal(map[string]any{"version": 1, "entries": map[string]any{
		slug: map[string]any{"last_access": day.Format("2006-01-02")}}})
	if err := os.WriteFile(filepath.Join(root, ".lifecycle.json"), blob, 0o644); err != nil {
		t.Fatal(err)
	}
}

func journalLinesFor(t *testing.T, engine, rel string) []lifecycleLine {
	t.Helper()
	blob, _ := os.ReadFile(filepath.Join(engine, LifecycleJournalName))
	var out []lifecycleLine
	for _, raw := range strings.Split(string(blob), "\n") {
		var line lifecycleLine
		if json.Unmarshal([]byte(raw), &line) == nil && line.Rel == rel {
			out = append(out, line)
		}
	}
	return out
}

// Task 187: the operator's edit of `dormant` back to `active` is journaled as
// theirs, once, with `lifecycle_since` stamped, and the note's quiet time
// counts from that day. It is not sunk the next night, nor on any night within
// the line, and it is sunk once it has been quiet for the full line again.
func TestAHandEditIsJournaledAsTheOperatorsAndTheClockRestartsFromIt(t *testing.T) {
	root, engine := t.TempDir(), t.TempDir()
	edit := time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC)
	rel := writeNote(t, root, "memory/semantic/put-back.md", "active", 400, edit, "")
	if err := AppendLifecycleJournal(engine, rel, "active", "dormant", "silent 370 days",
		"run-0", edit.AddDate(0, -1, 0)); err != nil {
		t.Fatal(err)
	}

	plan := planAndApply(t, root, engine, edit)
	if got := relsOf(plan.Touched); strings.Join(got, ",") != rel {
		t.Fatalf("touched = %v, want the note the operator edited", got)
	}
	lines := journalLinesFor(t, engine, rel)
	if len(lines) != 2 {
		t.Fatalf("journal lines for the note = %+v, want the night's sink and the operator's edit", lines)
	}
	if got := lines[1]; got.Actor != ActorOperator || got.From != "dormant" || got.To != "active" {
		t.Errorf("the edit's line = %+v, want the operator, dormant → active", got)
	}
	raw, _ := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
	if !strings.Contains(string(raw), "lifecycle_since: 2026-10-05\n") {
		t.Errorf("the note is not stamped with the day the edit was found:\n%s", raw)
	}

	for _, after := range []int{1, 100, 364, 365} {
		plan := planAndApply(t, root, engine, edit.AddDate(0, 0, after))
		if len(plan.Demoted) != 0 || len(plan.Touched) != 0 || len(plan.Intents) != 0 {
			t.Errorf("%d days after the edit: demoted %v, touched %v, %d intents; want nothing",
				after, relsOf(plan.Demoted), relsOf(plan.Touched), len(plan.Intents))
		}
	}
	if got := len(journalLinesFor(t, engine, rel)); got != 2 {
		t.Errorf("journal lines after the quiet nights = %d, want still 2: an edit is journaled once", got)
	}
	plan = planAndApply(t, root, engine, edit.AddDate(0, 0, 366))
	if got := relsOf(plan.Demoted); strings.Join(got, ",") != rel {
		t.Errorf("a full line of quiet after the edit: demoted %v, want the note", got)
	}
}

// Task 187: a note the operator sinks by hand — one no writer ever journaled —
// stays `dormant`. A recall from before the edit does not bring it back; a
// genuine recall after it does.
func TestAHandSinkStaysDormantUntilARecallAfterIt(t *testing.T) {
	root, engine := t.TempDir(), t.TempDir()
	edit := time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC)
	rel := writeNote(t, root, "memory/semantic/set-aside.md", "active", 40, edit, "")
	recallOn(t, root, "set-aside", edit.AddDate(0, 0, -10))
	p := filepath.Join(root, filepath.FromSlash(rel))
	raw, _ := os.ReadFile(p)
	if err := os.WriteFile(p, []byte(strings.Replace(string(raw), "lifecycle: active", "lifecycle: dormant", 1)), 0o644); err != nil {
		t.Fatal(err)
	}

	plan := planAndApply(t, root, engine, edit)
	if got := relsOf(plan.Touched); strings.Join(got, ",") != rel {
		t.Fatalf("touched = %v, want the hand sink", got)
	}
	if lines := journalLinesFor(t, engine, rel); len(lines) != 1 || lines[0].Actor != ActorOperator ||
		lines[0].From != "active" || lines[0].To != "dormant" {
		t.Errorf("journal = %+v, want one line: the operator, active → dormant", lines)
	}
	plan = planAndApply(t, root, engine, edit.AddDate(0, 0, 1))
	if len(plan.Revived) != 0 || len(plan.Intents) != 0 {
		t.Errorf("the next night: revived %v, %d intents; a recall from before the edit brought it back",
			relsOf(plan.Revived), len(plan.Intents))
	}
	recallOn(t, root, "set-aside", edit.AddDate(0, 0, 3))
	plan = planAndApply(t, root, engine, edit.AddDate(0, 0, 4))
	if got := relsOf(plan.Revived); strings.Join(got, ",") != rel {
		t.Errorf("after a recall that followed the edit: revived %v, want the note", got)
	}
}

// Task 187, the operator's ruling of 2026-10-04: a dormant note returns only on
// a genuine recall after its `lifecycle_since`. Enrichment's sink, journaled as
// the machinery's, restamps `updated` as it writes; that is no recall.
func TestEnrichmentsSinkIsNotUndoneByItsOwnRewrite(t *testing.T) {
	root, engine := t.TempDir(), t.TempDir()
	now := time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC)
	rel := writeNote(t, root, "memory/semantic/below-the-floor.md", "dormant", 60, now,
		"lifecycle_since: 2026-09-19\nupdated: 2026-10-01\n")
	if err := AppendLifecycleJournal(engine, rel, "active", "dormant",
		"enrichment: a second verdict below the floor", "", time.Date(2026, 9, 19, 9, 0, 0, 0, time.UTC)); err != nil {
		t.Fatal(err)
	}
	recallOn(t, root, "below-the-floor", time.Date(2026, 9, 10, 0, 0, 0, 0, time.UTC))
	plan := planAndApply(t, root, engine, now)
	if len(plan.Revived) != 0 || len(plan.Touched) != 0 {
		t.Errorf("revived %v, touched %v; want it left dormant", relsOf(plan.Revived), relsOf(plan.Touched))
	}
	recallOn(t, root, "below-the-floor", now)
	plan = planAndApply(t, root, engine, now.AddDate(0, 0, 1))
	if got := relsOf(plan.Revived); strings.Join(got, ",") != rel {
		t.Errorf("after a recall: revived %v, want the note", got)
	}
}

// Editing a note to `pinned` exempts it: journaled as the operator's, and never
// moved again, however long it stays quiet.
func TestEditingToPinnedExemptsTheNote(t *testing.T) {
	root, engine := t.TempDir(), t.TempDir()
	now := time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC)
	rel := writeNote(t, root, "memory/semantic/keep.md", "pinned", 3000, now, "")
	if err := AppendLifecycleJournal(engine, rel, "active", "dormant", "silent", "run-0", now.AddDate(-5, 0, 0)); err != nil {
		t.Fatal(err)
	}
	plan := planAndApply(t, root, engine, now)
	if lines := journalLinesFor(t, engine, rel); len(lines) != 2 || lines[1].Actor != ActorOperator || lines[1].To != "pinned" {
		t.Errorf("journal = %+v, want the operator's pin after the night's sink", lines)
	}
	for _, years := range []int{1, 5, 10} {
		plan = planAndApply(t, root, engine, now.AddDate(years, 0, 1))
		if len(plan.Intents) != 0 {
			t.Errorf("%d years on: %d intents for a pinned note", years, len(plan.Intents))
		}
	}
}

// A journal line that ends where no file can be — `purged` — says nothing about
// the file at that path now, which is a new note: it is no hand edit.
func TestAPurgedPathHoldsANewNoteNotAHandEdit(t *testing.T) {
	root, engine := t.TempDir(), t.TempDir()
	now := time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC)
	rel := writeNote(t, root, "memory/semantic/again.md", "active", 10, now, "")
	if err := AppendLifecycleJournal(engine, rel, "active", "purged", "purge manifest", "", now.AddDate(0, -1, 0)); err != nil {
		t.Fatal(err)
	}
	if plan := planAndApply(t, root, engine, now); len(plan.Touched) != 0 || len(plan.Intents) != 0 {
		t.Errorf("touched %v, %d intents; want nothing", relsOf(plan.Touched), len(plan.Intents))
	}
}
