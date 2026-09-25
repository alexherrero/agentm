package dreaming

import (
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"
)

// agentm-vault plan 09, task 4: the mocs job over the projects space.

var projectsNow = time.Date(2026, 9, 13, 9, 0, 0, 0, time.UTC)

func trackerNote(title, project, task, status, importance, updated, closed, state string) string {
	var b strings.Builder
	b.WriteString("---\nkind: tracker\ntitle: " + title + "\nproject: " + project + "\n")
	if task != "" {
		b.WriteString("task: " + task + "\n")
	}
	b.WriteString("status: " + status + "\n")
	if importance != "" {
		b.WriteString("importance: " + importance + "\n")
	}
	b.WriteString("opened: 2026-09-01\nupdated: " + updated + "\nclosed:")
	if closed != "" {
		b.WriteString(" " + closed)
	}
	b.WriteString("\n---\n\n## Objective\n\nShip it.\n\n## State\n\n" + state + "\n\n## Next\n\nKeep going.\n\n## Outcome\n")
	return b.String()
}

// projectsFixture is the nested layout with two projects, a retired one and an
// empty folder: `demo` has a charter, a project tracker, tasks in both layouts,
// records and research; `other` has only a charter, saved with CRLF endings.
func projectsFixture(t *testing.T) (root, vault string) {
	t.Helper()
	root, vault = rootMapVault(t)
	writeAt(t, vault, "projects/index.md", "---\ntitle: Projects\nkind: reference\n---\n\n# Projects\n\nOne tree per project.\n")
	writeAt(t, vault, "projects/demo/_index.md",
		"---\nkind: project-index\ncreated: 2026-08-01\nupdated: 2026-08-02\n---\n\n# Demo\n\n**What:** A demonstration project.\n")
	writeAt(t, vault, "projects/demo/tracker.md",
		trackerNote("Demo", "demo", "", "active", "", "2026-09-11", "", "Building the maps.\nA second line."))
	writeAt(t, vault, "projects/demo/tasks/001-build/tracker.md",
		trackerNote("Build", "demo", "001-build", "active", "7", "2026-09-12", "", "Half built."))
	writeAt(t, vault, "projects/demo/tasks/002-plan/tracker.md",
		trackerNote("Plan", "demo", "002-plan", "queued", "9", "2026-09-09", "", "Not started."))
	writeAt(t, vault, "projects/demo/tasks/000-start/tracker.md",
		trackerNote("Start", "demo", "000-start", "done", "5", "2026-09-10", "2026-09-10", "Started."))
	// A returned copy of the retired per-project state directory, with a flat
	// pair's tracker in it: no longer a place a task is read from (agentm-vault
	// plan 15), so the maps never list it.
	writeAt(t, vault, "projects/demo/_harness/tracker-flat.md",
		trackerNote("Flat", "demo", "flat", "parked", "", "2026-09-08", "", "Parked on purpose."))
	writeAt(t, vault, "projects/demo/_harness/PLAN-flat.md", "# Plan: flat\n")
	// `updated` is the night's enrichment stamp; the map dates a record by `created`.
	writeAt(t, vault, "projects/demo/decisions/a-ruling.md",
		"---\ntype: reference\ncreated: 2026-09-05\nupdated: 2026-09-13\n---\n\n# A ruling\n")
	writeAt(t, vault, "projects/demo/designs/the-design.md", "---\ntitle: The design\ncreated: 2026-09-03\n---\n\nBody.\n")
	writeAt(t, vault, "projects/demo/research/bundle-a/one.md", "# One\n")
	writeAt(t, vault, "projects/demo/research/bundle-a/notes/two.md", "# Two\n")
	writeAt(t, vault, "projects/demo/research/loose.md", "# Loose\n")
	writeAt(t, vault, "projects/other/_index.md",
		"---\r\nkind: project-index\r\ncreated: 2026-07-01\r\n---\r\n\r\n# Other\r\n\r\n**What:** Another project.\r\n")
	writeAt(t, vault, "projects/_archive/old/_index.md", "# Old\n")
	if err := os.MkdirAll(filepath.Join(vault, "projects", "empty"), 0o755); err != nil {
		t.Fatal(err)
	}
	return root, vault
}

func plannedText(plan MocsPlan, rel string) string {
	for _, in := range plan.Intents {
		if in.Rel == rel {
			return string(in.After)
		}
	}
	return ""
}

func plannedRels(plan MocsPlan) []string {
	var rels []string
	for _, p := range plan.Pages {
		rels = append(rels, p.Rel)
	}
	sort.Strings(rels)
	return rels
}

func mustContain(t *testing.T, page, text string, parts ...string) {
	t.Helper()
	for _, part := range parts {
		if !strings.Contains(text, part) {
			t.Errorf("%s lacks %q:\n%s", page, part, text)
		}
	}
}

func TestTheProjectsSpaceGetsAMapPerProjectAMapOfTasksAndAMapOfProjects(t *testing.T) {
	root, _ := projectsFixture(t)
	plan, err := PlanProjectMaps(root, projectsNow)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"../projects/demo/moc-demo.md", "../projects/moc-projects.md", "../projects/moc-tasks.md",
		"../projects/other/moc-other.md"}
	if got := plannedRels(plan); !reflect.DeepEqual(got, want) {
		t.Fatalf("planned %v, want %v", got, want)
	}

	demo := plannedText(plan, "../projects/demo/moc-demo.md")
	mustContain(t, "moc-demo", demo,
		"kind: moc\n", "slug: moc-demo\n", "generated_by: agentmdream\n",
		// The newest input is a task's update; the ruling's `updated` stamp is not an input.
		"updated: 2026-09-12\n",
		"# Demo\n\n[[moc-projects]] · [[projects/demo/_index|charter]] · [[projects/demo/tracker|tracker]]\n\nA demonstration project.\n",
		"## Tasks\n\n"+
			"- [[projects/demo/tasks/002-plan/tracker|Plan]] · queued · importance 9\n"+
			"- [[projects/demo/tasks/001-build/tracker|Build]] · active · importance 7\n\n"+
			"> [!done]- Closed 2026-09-10\n"+
			"> - [[projects/demo/tasks/000-start/tracker|Start]] · done · importance 5\n",
		"## Decisions\n\n- [[projects/demo/decisions/a-ruling|A ruling]] · 2026-09-05\n",
		"## Designs\n\n- [[projects/demo/designs/the-design|The design]] · 2026-09-03\n",
		"## Research\n\n- `bundle-a/` — 2 notes\n- 1 loose note in `research/`\n")
	if strings.Contains(demo, "PLAN-flat") || strings.Contains(demo, "tracker-flat") {
		t.Errorf("a flat pair in a returned retired directory was listed as a task:\n%s", demo)
	}

	tasks := plannedText(plan, "../projects/moc-tasks.md")
	mustContain(t, "moc-tasks", tasks,
		"slug: moc-tasks\n", "members: 3\n",
		"[[moc-root]] · [[moc-projects]]\n\n3 tasks across 1 project: in flight first",
		"## In flight\n\n- [[projects/demo/tasks/002-plan/tracker|Plan]] · demo · queued · importance 9\n",
		"## Closed\n\n> [!done]- Closed 2026-09-10\n> - [[projects/demo/tasks/000-start/tracker|Start]] · demo · done · importance 5\n")

	projects := plannedText(plan, "../projects/moc-projects.md")
	mustContain(t, "moc-projects", projects,
		"slug: moc-projects\n", "members: 3\n",
		"# projects\n\n[[moc-root]] · [[moc-tasks]]\n\nOne tree per project.",
		"through the promotion door", "lands all of it as one revertible commit.",
		"[[../index|index]] says who writes where.",
		"## Projects\n\n"+
			"- [[projects/demo/moc-demo|demo]] — A demonstration project. · 2 tasks in flight\n"+
			"- empty\n"+
			"- [[projects/other/moc-other|other]] — Another project.\n")
	if strings.Contains(projects, "_archive") || strings.Contains(projects, "\n- old") {
		t.Errorf("a retired project was listed:\n%s", projects)
	}
}

func TestASecondPassOverTheWrittenMapsWritesNothing(t *testing.T) {
	root, _ := projectsFixture(t)
	first, err := PlanProjectMaps(root, projectsNow)
	if err != nil {
		t.Fatal(err)
	}
	for _, in := range first.Intents {
		writeAt(t, root, in.Rel, string(in.After))
	}
	second, err := PlanProjectMaps(root, projectsNow.Add(48*time.Hour))
	if err != nil {
		t.Fatal(err)
	}
	if len(second.Intents) != 0 {
		t.Errorf("a second pass over unchanged projects planned %d write(s)", len(second.Intents))
	}
	if !reflect.DeepEqual(plannedRels(second), plannedRels(first)) {
		t.Errorf("the second pass reported %v, want %v", plannedRels(second), plannedRels(first))
	}
}

func TestNoProjectsSpaceIsNoMap(t *testing.T) {
	vault := t.TempDir()
	root := filepath.Join(vault, "agent")
	for _, d := range []string{".obsidian", "agent/memory/mocs"} {
		if err := os.MkdirAll(filepath.Join(vault, d), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	plan, err := PlanProjectMaps(root, projectsNow)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Pages) != 0 || len(plan.Intents) != 0 {
		t.Errorf("a vault with no projects space planned %v", plannedRels(plan))
	}
}

func TestATasksMapWaitsForATask(t *testing.T) {
	root, vault := rootMapVault(t)
	writeAt(t, vault, "projects/other/_index.md", "# Other\n\n**What:** Another project.\n")
	plan, err := PlanProjectMaps(root, projectsNow)
	if err != nil {
		t.Fatal(err)
	}
	if want := []string{"../projects/moc-projects.md", "../projects/other/moc-other.md"}; !reflect.DeepEqual(plannedRels(plan), want) {
		t.Errorf("planned %v, want %v", plannedRels(plan), want)
	}
	projects := plannedText(plan, "../projects/moc-projects.md")
	mustContain(t, "moc-projects", projects, "# projects\n\n[[moc-root]]\n\nOne tree per project.")
	if strings.Contains(projects, "moc-tasks") {
		t.Errorf("the projects map links a tasks map no pass writes:\n%s", projects)
	}
}

func TestAFlatVaultNamesItsProjectMapsWithoutClimbing(t *testing.T) {
	vault := t.TempDir()
	writeAt(t, vault, "projects/demo/_index.md", "# Demo\n\n**What:** A demonstration project.\n")
	plan, err := PlanProjectMaps(vault, projectsNow)
	if err != nil {
		t.Fatal(err)
	}
	if want := []string{"projects/demo/moc-demo.md", "projects/moc-projects.md"}; !reflect.DeepEqual(plannedRels(plan), want) {
		t.Errorf("planned %v, want %v", plannedRels(plan), want)
	}
}

func TestTonightsRootMapListsTheProjectMapsPlannedTonight(t *testing.T) {
	root, _ := projectsFixture(t)
	plan, err := PlanProjectMaps(root, projectsNow)
	if err != nil {
		t.Fatal(err)
	}
	var planned []string
	for _, p := range plan.Pages {
		planned = append(planned, p.Rel)
	}
	rootPlan, err := PlanRootMap(root, planned, projectsNow)
	if err != nil {
		t.Fatal(err)
	}
	text := rootMapText(rootPlan)
	if !strings.HasSuffix(text, "## Projects\n\n- [[moc-projects]]\n- [[moc-tasks]]\n") {
		t.Errorf("the root map does not list tonight's project maps:\n%s", text)
	}
	if strings.Contains(text, "moc-demo") {
		t.Errorf("the root map lists a project's own map, which moc-projects links:\n%s", text)
	}
}
