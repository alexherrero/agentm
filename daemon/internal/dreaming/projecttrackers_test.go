package dreaming

import (
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// The project trackers (task 176 step 6): generated nightly from the task
// trackers, in the tracker's one schema.

func trackersFixture(t *testing.T) (root, vault string) {
	t.Helper()
	root, vault = rootMapVault(t)
	writeAt(t, vault, "projects/demo/charter.md",
		"---\nkind: project-index\ncreated: 2026-08-01\n---\n\n# Demo\n\n**What:** A demonstration project.\n")
	writeAt(t, vault, "projects/demo/project.yaml",
		"slug: demo\ntitle: \"Demo, the project\"\nstatus: active\nrepositories: []\ncode_paths: []\nsensitivity: personal-financial\n")
	writeAt(t, vault, "projects/demo/tasks/001-start/tracker.md",
		trackerNote("Start", "demo", "001-start", "done", "", "2026-09-05", "2026-09-05", "Started."))
	writeAt(t, vault, "projects/demo/tasks/002-plan/tracker.md",
		trackerNote("Plan", "demo", "002-plan", "queued", "", "2026-09-09", "", "Not started."))
	writeAt(t, vault, "projects/demo/tasks/003-build/tracker.md",
		strings.Replace(trackerNote("Build", "demo", "003-build", "active", "", "2026-09-12", "", "Half built."),
			"closed:\n", "closed:\ndesign: wiki/designs/demo-design.md\n", 1))
	writeAt(t, vault, "projects/demo/tasks/004-ship/tracker.md",
		trackerNote("Ship", "demo", "004-ship", "done", "", "2026-09-10", "2026-09-10", "Shipped."))
	writeAt(t, vault, "projects/bare/charter.md", "# Bare\n\n**What:** Nothing in flight.\n")
	return root, vault
}

func TestAProjectTrackerIsTheChecklistOfItsTasks(t *testing.T) {
	root, _ := trackersFixture(t)
	plan, err := PlanProjectTrackers(root, projectsNow)
	if err != nil {
		t.Fatal(err)
	}
	text := plannedText(plan, "../projects/demo/tracker.md")
	if text == "" {
		t.Fatalf("no tracker planned for demo: %+v", plan.Pages)
	}
	for _, want := range []string{
		"kind: tracker\ntitle: \"Demo, the project\"\nproject: demo\nstatus: active\nopened: 2026-08-01\nupdated: 2026-09-12\nclosed:\nsensitivity: personal-financial\n---",
		"A demonstration project.",
		"2 open · 2 done",
		"- [ ] 003-build — active · demo-design\n- [ ] 002-plan — queued\n- [x] done: 2 — the latest 004-ship, closed 2026-09-10",
		"## Next\n\n003-build (active)",
	} {
		if !strings.Contains(text, want) {
			t.Errorf("demo's tracker lacks %q:\n%s", want, text)
		}
	}
	// No task field: a project's own tracker names none.
	if strings.Contains(text, "\ntask:") {
		t.Errorf("the project tracker names a task:\n%s", text)
	}
	bare := plannedText(plan, "../projects/bare/tracker.md")
	if !strings.Contains(bare, "0 open · 0 done") || !strings.Contains(bare, "No task is in flight.") ||
		!strings.Contains(bare, "status: active") || !strings.Contains(bare, "title: Bare") {
		t.Errorf("a project with no tasks still gets a tracker:\n%s", bare)
	}
}

func TestAProjectTrackerOverUnchangedTasksWritesNothing(t *testing.T) {
	root, _ := trackersFixture(t)
	plan, _ := PlanProjectTrackers(root, projectsNow)
	for _, in := range plan.Intents {
		writeAt(t, root, in.Rel, string(in.After))
	}
	again, _ := PlanProjectTrackers(root, projectsNow.AddDate(0, 0, 5))
	if len(again.Intents) != 0 {
		t.Errorf("unchanged task trackers rewrote a project tracker: %+v", again.Intents)
	}
}

// The generated file keeps the tracker's one schema: scripts/tracker.py reads
// it and finds nothing to refuse. Skipped where python3 or the repo's scripts
// are out of reach.
func TestAGeneratedTrackerPassesTheTrackerSchema(t *testing.T) {
	py, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 is not on PATH")
	}
	_, here, _, _ := runtime.Caller(0)
	script := filepath.Join(filepath.Dir(here), "..", "..", "..", "scripts", "tracker.py")
	root, _ := trackersFixture(t)
	plan, _ := PlanProjectTrackers(root, projectsNow)
	for _, in := range plan.Intents {
		writeAt(t, root, in.Rel, string(in.After))
		out, err := exec.Command(py, script, "check", filepath.Join(root, filepath.FromSlash(in.Rel))).CombinedOutput()
		if err != nil {
			t.Errorf("tracker.py check refuses %s: %v\n%s\n%s", in.Rel, err, out, in.After)
		}
	}
}
