package dreaming

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// The closed-task mover (task 177): two weeks after a task's tracker reads
// `done` or `dropped`, its whole directory moves to the project's
// `completed/tasks/`, journaled, with every link into it rewritten.

var taskNow = time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)

// taskRules is the packaged contract: what is walled, and the wait.
func taskRules(t *testing.T) *rules.Rules {
	t.Helper()
	t.Setenv("AGENTM_STORAGE_RULES", "")
	r, err := rules.Load(t.TempDir()) // no rules file: the packaged default
	if err != nil {
		t.Fatal(err)
	}
	return r
}

// closedTask writes a task directory with a tracker, a plan and a progress log.
func closedTask(t *testing.T, space, project, task, status, closed string) string {
	t.Helper()
	dir := filepath.Join(space, project, "tasks", task)
	fm := map[string]string{"kind": "tracker", "title": task, "status": status, "task": task}
	if closed != "" {
		fm["closed"] = closed
	}
	writeProjectFile(t, filepath.Join(dir, "tracker.md"), fm, "## Outcome\n\nIt shipped.")
	writeAt(t, dir, "plan.md", "# Plan\n\nSee [the roadmap](../../roadmap.md) and [tracker](tracker.md).\n")
	writeAt(t, dir, "progress.md", "log\n")
	return dir
}

func movedFolders(p TaskMovePlan) []string {
	var out []string
	for _, f := range p.Folders {
		out = append(out, f.Task)
	}
	return out
}

func fileMoves(p TaskMovePlan) int {
	n := 0
	for _, f := range p.folders {
		n += len(f)
	}
	return n
}

func TestAClosedTaskMovesFourteenDaysAfterItClosed(t *testing.T) {
	root, space := projectVault(t)
	closedTask(t, space, "agentm", "001-fourteen-days", "done", "2026-09-04")
	closedTask(t, space, "agentm", "002-thirteen-days", "done", "2026-09-05")
	plan, err := PlanTaskMoves(root, taskRules(t), taskNow, 0)
	if err != nil {
		t.Fatal(err)
	}
	if got := movedFolders(plan); len(got) != 1 || got[0] != "001-fourteen-days" {
		t.Fatalf("planned %v, want the task closed fourteen days ago and not the one closed thirteen", got)
	}
	f := plan.Folders[0]
	if f.From != "projects/agentm/tasks/001-fourteen-days" ||
		f.To != "projects/agentm/completed/tasks/001-fourteen-days" || f.Files != 3 {
		t.Errorf("the folder reads %+v", f)
	}
	if plan.Mode != "report" {
		t.Errorf("a plan is a report until it is applied; mode %q", plan.Mode)
	}
}

func TestDroppedCountsAsClosedAndAnOpenOrUndatedTaskNeverMoves(t *testing.T) {
	root, space := projectVault(t)
	closedTask(t, space, "agentm", "001-dropped", "dropped", "2026-08-01")
	closedTask(t, space, "agentm", "002-active", "active", "2026-08-01")
	closedTask(t, space, "agentm", "003-parked", "parked", "2026-08-01")
	closedTask(t, space, "agentm", "004-no-date", "done", "")
	plan, _ := PlanTaskMoves(root, taskRules(t), taskNow, 0)
	if got := movedFolders(plan); len(got) != 1 || got[0] != "001-dropped" {
		t.Fatalf("planned %v, want only the dropped task", got)
	}
}

func TestWithoutAContractTheMoverReadsNothing(t *testing.T) {
	// The contract says what is walled. Without it every wall would be open.
	root, space := projectVault(t)
	closedTask(t, space, "agentm", "001-done", "done", "2026-08-01")
	plan, err := PlanTaskMoves(root, nil, taskNow, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Folders) != 0 || !strings.Contains(plan.Skipped, "contract") {
		t.Errorf("planned %v with no contract; skipped %q", movedFolders(plan), plan.Skipped)
	}
}

func TestTheCapCountsFoldersOldestFirst(t *testing.T) {
	root, space := projectVault(t)
	closedTask(t, space, "agentm", "003-newest", "done", "2026-08-20")
	closedTask(t, space, "agentm", "001-oldest", "done", "2026-07-01")
	closedTask(t, space, "blog", "002-middle", "done", "2026-08-01")
	plan, _ := PlanTaskMoves(root, taskRules(t), taskNow, 2)
	if got := strings.Join(movedFolders(plan), ","); got != "001-oldest,002-middle" {
		t.Errorf("planned %s, want the two that closed first", got)
	}
	if plan.Capped != 1 {
		t.Errorf("capped %d, want the third waiting for another night", plan.Capped)
	}
	// Three files each, whole: a folder is never split by the cap.
	if n := fileMoves(plan); n != 6 {
		t.Errorf("%d file moves for two folders of three files", n)
	}
}

func TestAFolderAPersonalNoteLinksIntoIsHeldAndListedAndTakesNoSlot(t *testing.T) {
	root, space := projectVault(t)
	vault := filepath.Dir(root)
	closedTask(t, space, "agentm", "001-linked", "done", "2026-07-01")
	closedTask(t, space, "agentm", "002-free", "done", "2026-08-01")
	writeAt(t, vault, "personal/journal.md", "I read [[projects/agentm/tasks/001-linked/plan]] today.\n")
	writeAt(t, vault, "standards/notes.md", "Unrelated to any task.\n")

	plan, _ := PlanTaskMoves(root, taskRules(t), taskNow, 1)
	if got := movedFolders(plan); len(got) != 1 || got[0] != "002-free" {
		t.Fatalf("planned %v, want the unlinked folder in the one slot", got)
	}
	if len(plan.Held) != 1 || plan.Held[0].From != "projects/agentm/tasks/001-linked" ||
		len(plan.Held[0].Notes) != 1 || plan.Held[0].Notes[0] != "personal/journal.md" {
		t.Fatalf("held %+v, want the linked folder and the note that links into it", plan.Held)
	}
	for _, in := range plan.Intents {
		if strings.Contains(in.Rel, "personal/") || strings.Contains(in.Rel, "standards/") {
			t.Errorf("an intent touches %s", in.Rel)
		}
	}
}

func TestTheLinksIntoAndOutOfAMovedFolderAreRewritten(t *testing.T) {
	root, space := projectVault(t)
	vault := filepath.Dir(root)
	closedTask(t, space, "agentm", "001-done", "done", "2026-08-01")
	writeAt(t, space, "agentm/roadmap.md", "# Roadmap\n")
	writeAt(t, vault, "agent/memory/semantic/a-card.md",
		"---\ntitle: a\n---\n\nFrom [[projects/agentm/tasks/001-done/plan|the plan]].\n")
	writeAt(t, space, "agentm/docs/followups.md", "- [x] [done](../tasks/001-done/tracker.md)\n")

	plan, _ := PlanTaskMoves(root, taskRules(t), taskNow, 0)
	after := map[string]string{}
	for _, in := range plan.Intents {
		after[in.Rel] = string(in.After)
	}
	for _, f := range plan.folders {
		for _, in := range f {
			after[in.To] = string(in.After)
		}
	}
	for rel, want := range map[string]string{
		"memory/semantic/a-card.md":                           "[[projects/agentm/completed/tasks/001-done/plan|the plan]]",
		"../projects/agentm/docs/followups.md":                "[done](../completed/tasks/001-done/tracker.md)",
		"../projects/agentm/completed/tasks/001-done/plan.md": "[the roadmap](../../../roadmap.md) and [tracker](tracker.md)",
	} {
		if !strings.Contains(after[rel], want) {
			t.Errorf("%s does not read %q after the move:\n%s", rel, want, after[rel])
		}
	}
	if plan.Links != 3 || len(plan.Edited) != 2 {
		t.Errorf("rewrote %d link(s) in %d note(s), want 3 in the two notes and the plan", plan.Links, len(plan.Edited))
	}
}

func TestAFolderTheMoverCannotCarryIsHeldAndSaysWhy(t *testing.T) {
	root, space := projectVault(t)
	vault := filepath.Dir(root)
	r := taskRules(t)

	// A file too large for the journal's line.
	big := closedTask(t, space, "agentm", "001-big", "done", "2026-08-01")
	if err := os.WriteFile(filepath.Join(big, "recording.mov"), nil, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Truncate(filepath.Join(big, "recording.mov"), MaxTaskFileBytes+1); err != nil {
		t.Fatal(err)
	}
	// A walled area inside a task: never read, so never moved.
	walled := closedTask(t, space, "agentm", "002-walled", "done", "2026-08-01")
	writeAt(t, walled, "secrets/key.md", "not for any model\n")
	r.RecallExemptAreas = append(r.RecallExemptAreas, "projects/agentm/tasks/002-walled/secrets")
	// A file that cannot be read holds its folder and not the night. Mode 0
	// only blocks a read where the platform honours it, which Windows does not.
	unreadable := runtime.GOOS != "windows"
	if unreadable {
		locked := closedTask(t, space, "agentm", "003-locked", "done", "2026-08-01")
		if err := os.Chmod(filepath.Join(locked, "progress.md"), 0); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = os.Chmod(filepath.Join(locked, "progress.md"), 0o644) })
	}
	closedTask(t, space, "agentm", "004-fine", "done", "2026-08-01")

	plan, err := PlanTaskMoves(root, r, taskNow, 0)
	if err != nil {
		t.Fatalf("one folder's trouble failed the plan: %v", err)
	}
	if got := movedFolders(plan); len(got) != 1 || got[0] != "004-fine" {
		t.Errorf("planned %v, want only the folder with nothing wrong", got)
	}
	reasons := map[string]string{}
	for _, h := range plan.Held {
		reasons[filepath.Base(h.From)] = h.Reason
	}
	wants := map[string]string{"001-big": "too large", "002-walled": "walled"}
	if unreadable {
		wants["003-locked"] = "could not be read"
	}
	for task, want := range wants {
		if !strings.Contains(reasons[task], want) {
			t.Errorf("%s held for %q, want a reason saying %q", task, reasons[task], want)
		}
	}
	for _, f := range plan.folders {
		for _, in := range f {
			if strings.Contains(string(in.Before), "not for any model") {
				t.Error("the walled note was read into an intent")
			}
		}
	}
	_ = vault
}

func TestAHiddenFolderMovesWithItsTaskAndTheTrackerMovesLast(t *testing.T) {
	root, space := projectVault(t)
	dir := closedTask(t, space, "agentm", "001-done", "done", "2026-08-01")
	writeAt(t, dir, ".assets/diagram.png", "png")
	plan, _ := PlanTaskMoves(root, taskRules(t), taskNow, 0)
	if len(plan.folders) != 1 {
		t.Fatalf("planned %v", movedFolders(plan))
	}
	f := plan.folders[0]
	var rels []string
	for _, in := range f {
		rels = append(rels, in.Rel)
	}
	if len(f) != 4 || !strings.HasSuffix(f[len(f)-1].Rel, "/tracker.md") ||
		!strings.Contains(strings.Join(rels, " "), ".assets/diagram.png") {
		t.Errorf("moves %v; want the hidden folder's file with them and the tracker last", rels)
	}
}

// On a filesystem that folds case — macOS's, the vault's own — a tracker saved
// as `Tracker.md` is still the tracker, and it still moves last. Where case is
// kept, `Tracker.md` is simply not a tracker and the task is not a candidate.
func TestACapitalisedTrackerStillMovesLastWhereCaseFolds(t *testing.T) {
	root, space := projectVault(t)
	dir := filepath.Join(space, "agentm", "tasks", "001-done")
	writeProjectFile(t, filepath.Join(dir, "Tracker.md"),
		map[string]string{"kind": "tracker", "status": "done", "task": "001-done", "closed": "2026-08-01"}, "## Outcome\n\nx")
	if _, err := os.Stat(filepath.Join(dir, "tracker.md")); err != nil {
		t.Skip("this filesystem keeps case, so Tracker.md is not a tracker here")
	}
	writeAt(t, dir, "plan.md", "# plan\n")
	plan, _ := PlanTaskMoves(root, taskRules(t), taskNow, 0)
	if len(plan.folders) != 1 {
		t.Fatalf("planned %v", movedFolders(plan))
	}
	f := plan.folders[0]
	if len(f) != 2 || !strings.HasSuffix(f[1].Rel, "Tracker.md") {
		t.Errorf("the capitalised tracker did not move last: %v, %v", f[0].Rel, f[len(f)-1].Rel)
	}
}

func moverConfig(t *testing.T, root string) *config.Config {
	t.Helper()
	t.Setenv("AGENTM_STORAGE_RULES", "")
	vault := filepath.Dir(root)
	return &config.Config{VaultPath: vault, MemoryRoot: "agent",
		EngineStateDir: filepath.Join(t.TempDir(), "state"), Rules: rules.NewHolder(vault, taskNow)}
}

func readSidecar(t *testing.T, dir, name string) map[string]map[string]any {
	t.Helper()
	var s struct {
		Entries map[string]map[string]any `json:"entries"`
	}
	raw, _ := os.ReadFile(filepath.Join(dir, name))
	if err := json.Unmarshal(raw, &s); err != nil {
		t.Fatal(err)
	}
	return s.Entries
}

func TestAppliedTheFolderMovesWholeTheEmptiedFolderGoesAndTheSidecarsFollow(t *testing.T) {
	root, space := projectVault(t)
	src := closedTask(t, space, "agentm", "001-done", "done", "2026-08-01")
	writeAt(t, src, ".DS_Store", "litter")
	cfg := moverConfig(t, root)
	writeAt(t, cfg.EngineStateDir, ".heat.json",
		`{"entries": {"projects/agentm/tasks/001-done/plan.md": {"hits": 3, "last_hit": "2026-09-01"}}, "version": 2}`)
	writeAt(t, cfg.EngineStateDir, ".lifecycle.json",
		`{"entries": {"projects/agentm/tasks/001-done/tracker.md": {"last_access": "2026-09-01", "fingerprint": "stale"}}, "version": 2}`)

	plan, rep, err := MoveTasks(cfg, MoveTasksOptions{Now: taskNow, Apply: true})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Applied != 3 || rep.Skipped != 0 || plan.Mode != "apply" {
		t.Fatalf("applied %d, skipped %d, mode %s", rep.Applied, rep.Skipped, plan.Mode)
	}
	dst := filepath.Join(space, "agentm", "completed", "tasks", "001-done")
	for _, f := range []string{"tracker.md", "plan.md", "progress.md"} {
		if _, err := os.Stat(filepath.Join(dst, f)); err != nil {
			t.Errorf("%s did not arrive: %v", f, err)
		}
	}
	if _, err := os.Stat(src); !os.IsNotExist(err) {
		t.Errorf("the emptied folder is still there: %v", err)
	}
	heat := readSidecar(t, cfg.EngineStateDir, ".heat.json")
	if e := heat["projects/agentm/completed/tasks/001-done/plan.md"]; e == nil || e["hits"] != 3.0 {
		t.Errorf("the heat entry did not follow the plan: %v", heat)
	}
	tracker, _ := os.ReadFile(filepath.Join(dst, "tracker.md"))
	life := readSidecar(t, cfg.EngineStateDir, ".lifecycle.json")
	if e := life["projects/agentm/completed/tasks/001-done/tracker.md"]; e == nil ||
		e["fingerprint"] != BodyFingerprint(string(tracker)) {
		t.Errorf("the lifecycle entry did not follow with a fresh fingerprint: %v", life)
	}
}

// A file changed between the plan and the move: its move is refused, so the
// tracker must not move either, and the next pass finishes the folder.
func TestAChangedFileStopsTheFolderShortOfItsTrackerAndTheNextPassFinishesIt(t *testing.T) {
	root, space := projectVault(t)
	src := closedTask(t, space, "agentm", "001-done", "done", "2026-08-01")
	cfg := moverConfig(t, root)
	contract, _ := cfg.Rules.Get()
	plan, err := PlanTaskMoves(root, contract, taskNow, 0)
	if err != nil {
		t.Fatal(err)
	}
	writeAt(t, src, "plan.md", "# Plan, edited after the night planned\n")
	journal, _ := OpenJournal(cfg.EngineStateDir)
	var rep Report
	if err := ApplyTaskMoves(journal, root, cfg.EngineStateDir, "r1", &plan, contract, taskNow, 0, &rep); err != nil {
		t.Fatal(err)
	}
	if len(plan.Stopped) != 1 || rep.Skipped != 1 {
		t.Fatalf("stopped %+v, skipped %d", plan.Stopped, rep.Skipped)
	}
	if _, err := os.Stat(filepath.Join(src, "tracker.md")); err != nil {
		t.Fatalf("the tracker moved although the folder was not whole: %v", err)
	}

	if _, _, err := MoveTasks(cfg, MoveTasksOptions{Now: taskNow, Apply: true}); err != nil {
		t.Fatal(err)
	}
	dst := filepath.Join(space, "agentm", "completed", "tasks", "001-done")
	got, _ := os.ReadFile(filepath.Join(dst, "plan.md"))
	if !strings.Contains(string(got), "edited after the night planned") {
		t.Errorf("the edited plan did not follow: %q", got)
	}
	if _, err := os.Stat(src); !os.IsNotExist(err) {
		t.Errorf("the folder is still split after the next pass: %v", err)
	}
}

// A crash mid-folder: one file moved, one journaled and not yet made, the rest
// — the tracker among them — where they were, and no link edited. The next run
// settles the pending move through the journal, finishes the folder, and the
// repair from disk rewrites the links and re-keys the sidecars the crashed
// pass never reached.
func TestAPassThatCrashedMidFolderIsFinishedWholeAndItsLinksRepaired(t *testing.T) {
	root, space := projectVault(t)
	vault := filepath.Dir(root)
	src := closedTask(t, space, "agentm", "001-done", "done", "2026-08-01")
	writeAt(t, vault, "agent/memory/semantic/a-card.md", "From [[projects/agentm/tasks/001-done/plan]].\n")
	cfg := moverConfig(t, root)
	writeAt(t, cfg.EngineStateDir, ".heat.json",
		`{"entries": {"projects/agentm/tasks/001-done/plan.md": {"hits": 1}}, "version": 2}`)
	contract, _ := cfg.Rules.Get()
	plan, err := PlanTaskMoves(root, contract, taskNow, 0)
	if err != nil {
		t.Fatal(err)
	}
	f := plan.folders[0]
	if len(f) != 3 || !strings.HasSuffix(f[2].Rel, "tracker.md") {
		t.Fatalf("the tracker must move last: %v", f)
	}
	journal, _ := OpenJournal(cfg.EngineStateDir)
	if err := journal.Append(Entry{Kind: KindRunStart, RunID: "crashed", TS: taskNow, Mode: "apply"}); err != nil {
		t.Fatal(err)
	}
	if _, err := journal.Commit(root, "crashed", "crashed-0001", f[0], taskNow); err != nil {
		t.Fatal(err)
	}
	in := f[1] // journaled, never made: the crash
	if err := journal.Append(Entry{Kind: KindIntent, RunID: "crashed", TS: taskNow, ID: "crashed-0002",
		Job: in.Job, Rel: in.Rel, To: in.To, BeforeHash: Hash(in.Before), AfterHash: Hash(in.After),
		After: base64.StdEncoding.EncodeToString(in.After), Summary: in.Summary, Meta: in.Meta}); err != nil {
		t.Fatal(err)
	}

	_, rep, err := MoveTasks(cfg, MoveTasksOptions{Now: taskNow, Apply: true})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Resumed != 1 {
		t.Errorf("resumed %d, want the one pending intent", rep.Resumed)
	}
	dst := filepath.Join(space, "agentm", "completed", "tasks", "001-done")
	for _, name := range []string{"tracker.md", "plan.md", "progress.md"} {
		if _, err := os.Stat(filepath.Join(dst, name)); err != nil {
			t.Errorf("%s is not in the completed folder: %v", name, err)
		}
	}
	if _, err := os.Stat(src); !os.IsNotExist(err) {
		t.Errorf("the task is split: the old folder is still there (%v)", err)
	}
	card, _ := os.ReadFile(filepath.Join(vault, "agent", "memory", "semantic", "a-card.md"))
	if !strings.Contains(string(card), "[[projects/agentm/completed/tasks/001-done/plan|") {
		t.Errorf("the link into the moved plan was not repaired:\n%s", card)
	}
	if _, ok := readSidecar(t, cfg.EngineStateDir, ".heat.json")["projects/agentm/completed/tasks/001-done/plan.md"]; !ok {
		t.Error("the heat entry of the file the crashed pass moved was not re-keyed")
	}
}

func TestAMoveCrashedBetweenItsWriteAndItsRemovalIsFinishedOnResume(t *testing.T) {
	root, space := projectVault(t)
	src := closedTask(t, space, "agentm", "001-done", "done", "2026-08-01")
	cfg := moverConfig(t, root)
	contract, _ := cfg.Rules.Get()
	plan, _ := PlanTaskMoves(root, contract, taskNow, 0)
	in := plan.folders[0][0]
	journal, _ := OpenJournal(cfg.EngineStateDir)
	e := Entry{Kind: KindIntent, RunID: "crashed", TS: taskNow, ID: "crashed-0001",
		Job: in.Job, Rel: in.Rel, To: in.To, BeforeHash: Hash(in.Before), AfterHash: Hash(in.After),
		After: base64.StdEncoding.EncodeToString(in.After)}
	if err := journal.Append(Entry{Kind: KindRunStart, RunID: "crashed", TS: taskNow}); err != nil {
		t.Fatal(err)
	}
	if err := journal.Append(e); err != nil {
		t.Fatal(err)
	}
	// The destination written, the source not yet removed.
	writeAt(t, root, in.To, string(in.After))
	kind, err := journal.Resolve(root, e, taskNow)
	if err != nil {
		t.Fatal(err)
	}
	if kind != KindApplied {
		t.Errorf("resolved as %s, want applied", kind)
	}
	if _, err := os.Stat(filepath.Join(root, filepath.FromSlash(in.Rel))); !os.IsNotExist(err) {
		t.Errorf("the source is still there: %v", err)
	}
	_ = src
}

func TestMoveTasksThatDoesNotApplyWritesNothingEvenWithAnIntentPending(t *testing.T) {
	root, space := projectVault(t)
	src := closedTask(t, space, "agentm", "001-done", "done", "2026-08-01")
	cfg := moverConfig(t, root)
	contract, _ := cfg.Rules.Get()
	plan, _ := PlanTaskMoves(root, contract, taskNow, 0)
	in := plan.folders[0][0]
	journal, _ := OpenJournal(cfg.EngineStateDir)
	_ = journal.Append(Entry{Kind: KindRunStart, RunID: "crashed", TS: taskNow})
	_ = journal.Append(Entry{Kind: KindIntent, RunID: "crashed", TS: taskNow, ID: "crashed-0001",
		Job: in.Job, Rel: in.Rel, To: in.To, BeforeHash: Hash(in.Before), AfterHash: Hash(in.After),
		After: base64.StdEncoding.EncodeToString(in.After)})

	dry, _, err := MoveTasks(cfg, MoveTasksOptions{Now: taskNow})
	if err != nil {
		t.Fatal(err)
	}
	if dry.Pending != 1 {
		t.Errorf("pending %d, want the crashed intent reported", dry.Pending)
	}
	if _, err := os.Stat(filepath.Join(src, "plan.md")); err != nil {
		t.Errorf("a run that does not apply moved a file: %v", err)
	}
}

func TestADestinationFileThatExistsHoldsTheFolder(t *testing.T) {
	root, space := projectVault(t)
	closedTask(t, space, "agentm", "001-done", "done", "2026-08-01")
	writeAt(t, space, "agentm/completed/tasks/001-done/plan.md", "a different plan\n")
	plan, _ := PlanTaskMoves(root, taskRules(t), taskNow, 0)
	if len(plan.Folders) != 0 || len(plan.Held) != 1 ||
		!strings.Contains(plan.Held[0].Reason, "never overwrites") {
		t.Errorf("planned %v, held %+v", movedFolders(plan), plan.Held)
	}
}

func TestTheNightReportsTheMovesUntilItsSwitchIsOn(t *testing.T) {
	root, space := projectVault(t)
	src := closedTask(t, space, "agentm", "001-done", "done", "2026-08-01")
	cfg := moverConfig(t, root)
	rep, err := Run(cfg, Options{Now: taskNow, Apply: true, Force: true})
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.Tasks.Folders) != 1 || rep.Tasks.Mode != "report" {
		t.Fatalf("the night's task plan reads %+v", rep.Tasks)
	}
	if _, err := os.Stat(src); err != nil {
		t.Fatalf("the folder moved with the switch off: %v", err)
	}
	cfg.TaskMoverEnabled = true
	rep, err = Run(cfg, Options{Now: taskNow.Add(time.Hour), Apply: true, Force: true})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Tasks.Mode != "apply" {
		t.Errorf("mode %q with the switch on", rep.Tasks.Mode)
	}
	if _, err := os.Stat(src); !os.IsNotExist(err) {
		t.Errorf("the folder did not move with the switch on: %v", err)
	}
}

func TestThePackagedContractWaitsFourteenDays(t *testing.T) {
	if got := TaskCompletedAfterDays(taskRules(t)); got != 14 {
		t.Errorf("the packaged contract's %s reads %v, want 14", TaskCompletedAfterKey, got)
	}
	if got := TaskCompletedAfterDays(nil); got != DefaultTaskCompletedAfterDays {
		t.Errorf("no contract reads %v days", got)
	}
}

// One writer for an entity page (task 190 step 5). The mover repaired an
// entity page's links into a moved folder, and the builder rebuilt the same
// page the next night: the write-quality audit of 2026-10-07 found pages
// written twice for it. The builder renders the pages from the notes, so the
// mover leaves them to it.
func TestTheMoverLeavesEntityPagesToTheBuilder(t *testing.T) {
	root, space := projectVault(t)
	vault := filepath.Dir(root)
	closedTask(t, space, "agentm", "001-done", "done", "2026-08-01")
	writeAt(t, vault, "agent/memory/entities/issues/alexherrero-agentm-900.md",
		"---\ntitle: alexherrero/agentm#900\nkind: entity-profile\n---\n\n- [[projects/agentm/tasks/001-done/plan|001-done plan]]\n")
	writeAt(t, vault, "agent/memory/semantic/a-card.md",
		"---\ntitle: a\n---\n\nFrom [[projects/agentm/tasks/001-done/plan|the plan]].\n")
	plan, _ := PlanTaskMoves(root, taskRules(t), taskNow, 0)
	for _, in := range plan.Intents {
		if strings.Contains(in.Rel, "memory/entities/") {
			t.Errorf("the mover planned an edit of the entity page %s:\n%s", in.Rel, in.After)
		}
	}
	if len(plan.Edited) != 1 || !strings.HasSuffix(plan.Edited[0].Path, "memory/semantic/a-card.md") {
		t.Errorf("edited %+v, want the card alone", plan.Edited)
	}
}

// The night builds the entity pages after the mover, so a page lists a task
// moved tonight where it now sits, the same night — even while the index still
// names its notes at `tasks/`.
func TestTheNightBuildsEntityPagesAfterTheMover(t *testing.T) {
	root, space := projectVault(t)
	vault := filepath.Dir(root)
	writeAt(t, space, "agentm/project.yaml", "slug: agentm\nrepositories:\n  - alexherrero/agentm\n")
	dir := closedTask(t, space, "agentm", "001-done", "done", "2026-08-01")
	writeAt(t, dir, "plan.md", "# Plan\n\nFixed in #900.\n")
	writeAt(t, space, "agentm/tasks/002-open/plan.md", "# Plan\n\nBuilds on #900.\n")
	writeAt(t, space, "agentm/research/why.md", "# Why\n\nWhy #900 mattered.\n")
	cfg := moverConfig(t, root)
	cfg.TaskMoverEnabled = true
	cfg.IndexPath = filepath.Join(t.TempDir(), "index.db")
	x, err := index.Open(cfg.IndexPath, vault, "agent", false)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}
	x.Close()

	rep, err := Run(cfg, Options{Now: taskNow, Apply: true, Force: true})
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.Tasks.Folders) != 1 {
		t.Fatalf("the mover planned %+v", rep.Tasks.Folders)
	}
	if _, err := os.Stat(filepath.Join(space, "agentm", "completed", "tasks", "001-done", "plan.md")); err != nil {
		t.Fatalf("the task did not move: %v", err)
	}
	page, err := os.ReadFile(filepath.Join(root, "memory", "entities", "issues", "alexherrero-agentm-900.md"))
	if err != nil {
		t.Fatalf("#900 has three origins and no page: %v (entities %+v)", err, rep.Entities)
	}
	if !strings.Contains(string(page), "projects/agentm/completed/tasks/001-done/plan") ||
		strings.Contains(string(page), "projects/agentm/tasks/001-done/") {
		t.Errorf("the page built tonight does not list the moved task where it sits:\n%s", page)
	}
	for _, e := range rep.Tasks.Edited {
		if strings.Contains(e.Path, "memory/entities/") {
			t.Errorf("the mover edited the entity page %s as well; the builder is its one writer", e.Path)
		}
	}
}

// Found by the adversarial review of task 190 steps 3-7.
//
// linkEdits now skips every note under `<memRel>/memory/entities/` on the
// grounds that "the builder renders [entity pages] from the notes after the
// mover has run". But the builder only renders its own `kind: entity-profile`
// pages: a hand-written note in the same folders is `handWritten` and goes to
// plan.Held, never re-rendered. Its links into a moved task folder are now
// repaired by nobody, so they break the night the task moves.
func TestTheMoverStillRepairsAHandWrittenNoteUnderEntities(t *testing.T) {
	root, space := projectVault(t)
	vault := filepath.Dir(root)
	closedTask(t, space, "agentm", "001-done", "done", "2026-08-01")
	hand := "---\ntitle: Alex\ntype: person\n---\n\nLed [[projects/agentm/tasks/001-done/plan|the plan]].\n"
	writeAt(t, vault, "agent/memory/entities/people/alex.md", hand)

	// The builder would hold this note, not render it.
	if !handWritten([]byte(hand)) {
		t.Fatalf("fixture: the note is not hand-written to the builder")
	}

	plan, err := PlanTaskMoves(root, taskRules(t), taskNow, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Folders) != 1 {
		t.Fatalf("fixture: the mover planned %+v", plan.Folders)
	}
	repaired := false
	for _, in := range plan.Intents {
		if strings.HasSuffix(in.Rel, "memory/entities/people/alex.md") &&
			strings.Contains(string(in.After), "projects/agentm/completed/tasks/001-done/plan") {
			repaired = true
		}
	}
	if !repaired {
		b, _ := os.ReadFile(filepath.Join(vault, "agent", "memory", "entities", "people", "alex.md"))
		t.Errorf("a hand-written note under memory/entities/ links into a task folder the mover is "+
			"moving tonight, the builder holds it (never re-renders it), and the mover no longer "+
			"repairs it: the link breaks.\nnote:\n%s\nedited: %+v", b, plan.Edited)
	}
}
