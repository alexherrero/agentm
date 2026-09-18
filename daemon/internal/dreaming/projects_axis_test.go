package dreaming

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// `projects/` ranks by how much the work is being worked, and what has finished
// moves out of the way.

func projectVault(t *testing.T) (root, space string) {
	t.Helper()
	vault := t.TempDir()
	root = filepath.Join(vault, "agent")
	if err := os.MkdirAll(root, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(vault, ".obsidian"), 0o755); err != nil {
		t.Fatal(err)
	}
	space = filepath.Join(vault, "projects")
	if err := os.MkdirAll(space, 0o755); err != nil {
		t.Fatal(err)
	}
	return root, space
}

func writeProjectFile(t *testing.T, path string, fm map[string]string, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	var b strings.Builder
	b.WriteString("---\n")
	for _, k := range []string{"kind", "title", "status", "opened", "updated", "closed",
		"created", "task", "superseded_by"} {
		if v, ok := fm[k]; ok {
			b.WriteString(k + ": " + v + "\n")
		}
	}
	b.WriteString("---\n\n" + body + "\n")
	if err := os.WriteFile(path, []byte(b.String()), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestTheActivityBandsReadTheProjectsOwnEvidence(t *testing.T) {
	root, space := projectVault(t)
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)

	// Worked this week.
	writeProjectFile(t, filepath.Join(space, "live", "tasks", "001-a", "tracker.md"),
		map[string]string{"kind": "tracker", "status": "active", "updated": "2026-09-15"}, "state")
	// Worked two months ago.
	writeProjectFile(t, filepath.Join(space, "quiet", "decisions", "a.md"),
		map[string]string{"kind": "note", "updated": "2026-07-20"}, "a ruling")
	// Worked six months ago.
	writeProjectFile(t, filepath.Join(space, "quieter", "decisions", "a.md"),
		map[string]string{"kind": "note", "updated": "2026-03-01"}, "a ruling")
	// Years ago.
	writeProjectFile(t, filepath.Join(space, "cold", "decisions", "a.md"),
		map[string]string{"kind": "note", "updated": "2023-01-01"}, "a ruling")

	plan, err := PlanProjects(root, nil, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]float64{"live": 1.0, "quiet": 0.7, "quieter": 0.5, "cold": 0.3}
	got := map[string]float64{}
	for _, r := range plan.Activity {
		got[r.Slug] = r.Activity
	}
	for slug, w := range want {
		if got[slug] != w {
			t.Errorf("%s reads %v, want %v", slug, got[slug], w)
		}
	}
	for _, r := range plan.Activity {
		if r.Slug == "live" && r.LastWorked != "2026-09-15" {
			t.Errorf("last worked = %q, want the tracker's own date", r.LastWorked)
		}
		if r.Slug == "live" && len(r.Signals) == 0 {
			t.Error("the reading names no signal; a number the operator disagrees " +
				"with has to be arguable")
		}
	}
}

func TestATrackerGainsTheTwoFields(t *testing.T) {
	root, space := projectVault(t)
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)
	tracker := filepath.Join(space, "agentm", "tracker.md")
	writeProjectFile(t, tracker,
		map[string]string{"kind": "tracker", "title": "agentm", "status": "active",
			"updated": "2026-09-17"}, "## Objective\n\nthe work")

	plan, err := PlanProjects(root, nil, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	if plan.TrackersWritten != 1 || len(plan.Intents) != 1 {
		t.Fatalf("wrote %d tracker(s) with %d intent(s), want one of each",
			plan.TrackersWritten, len(plan.Intents))
	}
	after := string(plan.Intents[0].After)
	if !strings.Contains(after, "activity: 1.0") {
		t.Errorf("the tracker did not gain `activity:`:\n%s", after)
	}
	if !strings.Contains(after, "last_worked: 2026-09-17") {
		t.Errorf("the tracker did not gain `last_worked:`:\n%s", after)
	}
	// Everything else byte-identical: a tracker is the operator's living head.
	if !strings.Contains(after, "## Objective\n\nthe work") ||
		!strings.Contains(after, "title: agentm") {
		t.Error("the edit disturbed the rest of the tracker")
	}
}

// No project carries a tracker today — the operator's ruling of 2026-09-17 puts
// them in their own plan — so the pass ranks and writes nothing, and says so by
// counting zero rather than by failing.
func TestAProjectWithNoTrackerIsStillRead(t *testing.T) {
	root, space := projectVault(t)
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)
	writeProjectFile(t, filepath.Join(space, "agentm", "decisions", "a.md"),
		map[string]string{"kind": "note", "updated": "2026-09-17"}, "a ruling")

	plan, err := PlanProjects(root, nil, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Activity) != 1 || plan.Activity[0].Activity != 1.0 {
		t.Errorf("activity = %+v, want the project read anyway", plan.Activity)
	}
	if plan.TrackersWritten != 0 || len(plan.Intents) != 0 {
		t.Error("something was written where no tracker exists")
	}
}

func TestFinishedRecordsMoveAndLivingOnesDoNot(t *testing.T) {
	root, space := projectVault(t)
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)

	// A closed task, and a research bundle that names it.
	writeProjectFile(t, filepath.Join(space, "agentm", "tasks", "001-done", "tracker.md"),
		map[string]string{"kind": "tracker", "status": "done", "task": "001-done",
			"closed": "2026-09-01"}, "## Outcome\n\nit shipped")
	writeProjectFile(t, filepath.Join(space, "agentm", "research", "a-bundle.md"),
		map[string]string{"kind": "note", "task": "001-done"}, "what it found")
	// A decision a later one supersedes.
	writeProjectFile(t, filepath.Join(space, "agentm", "decisions", "old.md"),
		map[string]string{"kind": "note", "superseded_by": "[[new]]"}, "the old ruling")
	// The living ones.
	writeProjectFile(t, filepath.Join(space, "agentm", "decisions", "standing.md"),
		map[string]string{"kind": "note"}, "still true")
	writeProjectFile(t, filepath.Join(space, "agentm", "charter.md"),
		map[string]string{"kind": "note"}, "why we do this")
	// An open task's bundle stays.
	writeProjectFile(t, filepath.Join(space, "agentm", "tasks", "002-open", "tracker.md"),
		map[string]string{"kind": "tracker", "status": "active", "task": "002-open"}, "state")
	writeProjectFile(t, filepath.Join(space, "agentm", "research", "b-bundle.md"),
		map[string]string{"kind": "note", "task": "002-open"}, "in flight")

	closed := ClosedTasks(root)
	if !closed["001-done"] || closed["002-open"] {
		t.Fatalf("closed tasks = %v, want the done one only", closed)
	}
	plan, err := PlanCompleted(root, closed, nil, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	var moved []string
	for _, m := range plan.Moved {
		moved = append(moved, m.From+" -> "+m.To)
	}
	if len(moved) != 2 {
		t.Fatalf("moved %v, want the closed task's bundle and the superseded decision", moved)
	}
	joined := strings.Join(moved, "\n")
	if !strings.Contains(joined, "completed/research/a-bundle.md") {
		t.Error("the closed task's bundle did not move")
	}
	if !strings.Contains(joined, "completed/decisions/old.md") {
		t.Error("the superseded decision did not move")
	}
	if strings.Contains(joined, "standing.md") || strings.Contains(joined, "charter.md") ||
		strings.Contains(joined, "b-bundle.md") {
		t.Error("a living document moved")
	}
	// A task directory never moves at all.
	if strings.Contains(joined, "tasks/") {
		t.Error("a task moved; the sequence of task directories is the history of " +
			"the project as the operator reads it")
	}
}

func TestAProjectMovesOnlyWithItsOutcomeWritten(t *testing.T) {
	root, space := projectVault(t)
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)

	writeProjectFile(t, filepath.Join(space, "finished", "tracker.md"),
		map[string]string{"kind": "tracker", "status": "done"},
		"## Objective\n\nthe work\n\n## Outcome\n\nit shipped")
	// `done` with an empty Outcome is a tracker mid-edit, and moving the project
	// out from under the session writing it is the one thing this must not do.
	writeProjectFile(t, filepath.Join(space, "mid-edit", "tracker.md"),
		map[string]string{"kind": "tracker", "status": "done"},
		"## Objective\n\nthe work\n\n## Outcome\n")
	writeProjectFile(t, filepath.Join(space, "live", "tracker.md"),
		map[string]string{"kind": "tracker", "status": "active"}, "## Outcome\n")

	done := DoneProjects(root)
	if strings.Join(done, ",") != "finished" {
		t.Fatalf("done projects = %v, want the one with an Outcome", done)
	}
	plan, err := PlanCompleted(root, nil, done, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Moved) != 1 || !strings.HasSuffix(plan.Moved[0].To, "projects/completed/finished") {
		t.Errorf("moved = %+v, want the finished project into projects/completed/", plan.Moved)
	}
}

func TestTheReadingsRoundTripForTheRanker(t *testing.T) {
	dir := t.TempDir()
	readings := []ActivityReading{
		{Slug: "agentm", Activity: 1.0, LastWorked: "2026-09-18"},
		{Slug: "blog", Activity: 0.3, LastWorked: "2023-01-01"},
	}
	if err := WriteActivity(dir, readings, time.Now()); err != nil {
		t.Fatal(err)
	}
	back := ReadActivity(dir)
	if back["agentm"] != 1.0 || back["blog"] != 0.3 {
		t.Errorf("read back %v, want the readings as written", back)
	}
}
