package dreaming

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
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
	plan, err := PlanProjectTrackers(root, projectsNow, nil)
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

// Task 177: a task the night has moved to `completed/tasks/` still counts, so
// the tracker's "M done" line is the same the night after the move as before
// it, and the project's map links the task where it now sits.
func TestAMovedTaskStillCountsAndIsLinkedWhereItSits(t *testing.T) {
	root, vault := trackersFixture(t)
	before, err := PlanProjectTrackers(root, projectsNow, nil)
	if err != nil {
		t.Fatal(err)
	}
	from := filepath.Join(vault, "projects", "demo", "tasks", "001-start")
	to := filepath.Join(vault, "projects", "demo", "completed", "tasks", "001-start")
	if err := os.MkdirAll(filepath.Dir(to), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(from, to); err != nil {
		t.Fatal(err)
	}
	after, err := PlanProjectTrackers(root, projectsNow, nil)
	if err != nil {
		t.Fatal(err)
	}
	was, is := plannedText(before, "../projects/demo/tracker.md"), plannedText(after, "../projects/demo/tracker.md")
	if was != is || !strings.Contains(is, "2 open · 2 done") {
		t.Errorf("the move changed the project tracker:\n--- before\n%s\n--- after\n%s", was, is)
	}
	maps, err := PlanProjectMaps(root, projectsNow)
	if err != nil {
		t.Fatal(err)
	}
	demo := plannedText(maps, "../projects/demo/moc-demo.md")
	if !strings.Contains(demo, "[[projects/demo/completed/tasks/001-start/tracker|Start]]") {
		t.Errorf("the map does not link the moved task where it sits:\n%s", demo)
	}
	if strings.Contains(demo, "[[projects/demo/tasks/001-start/") {
		t.Errorf("the map still links the moved task's old path:\n%s", demo)
	}
}

func TestAProjectTrackerOverUnchangedTasksWritesNothing(t *testing.T) {
	root, _ := trackersFixture(t)
	plan, _ := PlanProjectTrackers(root, projectsNow, nil)
	for _, in := range plan.Intents {
		writeAt(t, root, in.Rel, string(in.After))
	}
	again, _ := PlanProjectTrackers(root, projectsNow.AddDate(0, 0, 5), nil)
	if len(again.Intents) != 0 {
		t.Errorf("unchanged task trackers rewrote a project tracker: %+v", again.Intents)
	}
}

// The projects job writes `activity` and `last_worked` into the tracker after
// this job runs; carrying them keeps an unchanged night from rewriting the file
// and the two jobs from undoing each other.
func TestTheProjectsJobsActivityFieldsAreCarried(t *testing.T) {
	root, _ := trackersFixture(t)
	plan, _ := PlanProjectTrackers(root, projectsNow, nil)
	for _, in := range plan.Intents {
		text := string(in.After)
		text = strings.Replace(text, "\n---\n", "\nactivity: 0.7\nlast_worked: 2026-09-12\n---\n", 1)
		writeAt(t, root, in.Rel, text)
	}
	again, _ := PlanProjectTrackers(root, projectsNow.AddDate(0, 0, 1), nil)
	if len(again.Intents) != 0 {
		t.Errorf("carried fields were dropped, so the tracker was rewritten: %s", again.Intents[0].After)
	}
	if got := setActivityFields(mustRead(t, root, "../projects/demo/tracker.md"),
		ActivityReading{Activity: 0.7, LastWorked: "2026-09-12"}); got != mustRead(t, root, "../projects/demo/tracker.md") {
		t.Errorf("the projects job would still edit the carried page:\n%s", got)
	}
}

func mustRead(t *testing.T, root, rel string) string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
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
	plan, _ := PlanProjectTrackers(root, projectsNow, nil)
	for _, in := range plan.Intents {
		writeAt(t, root, in.Rel, string(in.After))
		out, err := exec.Command(py, script, "check", filepath.Join(root, filepath.FromSlash(in.Rel))).CombinedOutput()
		if err != nil {
			t.Errorf("tracker.py check refuses %s: %v\n%s\n%s", in.Rel, err, out, in.After)
		}
	}
}

// One write a night for a project's tracker (task 190 step 4). The night
// rendered the tracker carrying `activity` and `last_worked` from the page as
// it stood, then the projects job edited those two lines later the same
// night: two commits of one file. The write-quality audit of 2026-10-07 found
// each project tracker written twice on 10-05 and 10-06. The reading is now
// taken first and rendered in, so the projects job finds its values in place.
func TestANightWritesAProjectTrackerOnceWithItsActivity(t *testing.T) {
	root, vault := trackersFixture(t)
	cfg := &config.Config{VaultPath: vault, MemoryRoot: "agent", EngineStateDir: filepath.Join(t.TempDir(), "state")}
	night := func(now time.Time) Report {
		t.Helper()
		rep, err := Run(cfg, Options{Apply: true, Force: true, Now: now})
		if err != nil {
			t.Fatal(err)
		}
		return rep
	}

	rep := night(projectsNow)
	if rep.Projects.TrackersWritten != 0 {
		t.Errorf("the projects job wrote %d tracker(s) after the render; the render should carry its reading",
			rep.Projects.TrackersWritten)
	}
	written := 0
	for _, p := range rep.Mocs.Pages {
		if p.Rel == "../projects/demo/tracker.md" && p.Changed {
			written++
		}
	}
	if written != 1 {
		t.Errorf("demo's tracker was rendered %d time(s) tonight, want 1", written)
	}
	text := mustRead(t, root, "../projects/demo/tracker.md")
	var reading ActivityReading
	for _, a := range rep.Projects.Activity {
		if a.Slug == "demo" {
			reading = a
		}
	}
	if reading.LastWorked != "2026-09-12" {
		t.Fatalf("demo's reading is %+v, want last worked 2026-09-12 (its newest task)", reading)
	}
	if want := fmt.Sprintf("\nactivity: %.1f\nlast_worked: 2026-09-12\n---\n", reading.Activity); !strings.Contains(text, want) {
		t.Errorf("the tracker does not carry tonight's reading %q:\n%s", want, text)
	}

	// The next night, nothing moved: neither job writes it.
	rep = night(projectsNow.AddDate(0, 0, 1))
	for _, p := range rep.Mocs.Pages {
		if p.Rel == "../projects/demo/tracker.md" && p.Changed {
			t.Errorf("an unchanged project rewrote its tracker")
		}
	}
	if rep.Projects.TrackersWritten != 0 {
		t.Errorf("an unchanged night's projects job wrote %d tracker(s)", rep.Projects.TrackersWritten)
	}
}
