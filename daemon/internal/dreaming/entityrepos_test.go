package dreaming

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Task 186: the bars the vault growth audit of 2026-10-03 asked for. A task's
// own notes count once toward a page's bar, and a renamed repository's old
// name folds into its current page. (Its third, that a number or version the
// repository's clone never had gets no page, went with the issue and release
// pages in task 202.)

func pageIDs(t *testing.T, notes map[string]string) map[string]EntityPage {
	t.Helper()
	root, x := entityVault(t, notes)
	plan, err := PlanEntities(root, filepath.Dir(root), x, PeopleOptions{}, nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	got := map[string]EntityPage{}
	for _, p := range plan.Pages {
		got[p.ID] = p
	}
	return got
}

// A progress log restates its plan's status line, so the two are one source:
// a repository only one task names gets no page, and one a second task names
// does — a completed task counting like an open one.
func TestATaskFolderCountsOnceTowardThePagesBar(t *testing.T) {
	got := pageIDs(t, map[string]string{
		"projects/agentm/project.yaml":                      "slug: agentm\nrepositories:\n  - alexherrero/agentm\n",
		"projects/agentm/tasks/003-c/plan.md":               "Built on github.com/alexherrero/solo.\n",
		"projects/agentm/tasks/003-c/progress.md":           "Step 4 used github.com/alexherrero/solo.\n",
		"projects/agentm/tasks/003-c/tracker.md":            "Waiting on github.com/alexherrero/solo.\n",
		"projects/agentm/tasks/004-d/plan.md":               "Paired with github.com/alexherrero/pair.\n",
		"projects/agentm/completed/tasks/001-a/progress.md": "See github.com/alexherrero/pair.\n",
	})
	if p, ok := got["repo:alexherrero/solo"]; ok {
		t.Errorf("one task's plan, progress and tracker cleared the bar: %+v", p)
	}
	if p := got["repo:alexherrero/pair"]; p.Mentions != 2 {
		t.Errorf("pair is named by two tasks, one of them completed: %+v, want a page with 2 mentions", p)
	}
}

// GitHub redirects a renamed repository, so a note that names the old one
// names the same repository: one page, the old name among its aliases.
func TestAFormerNameFoldsIntoTheCurrentRepositorysPage(t *testing.T) {
	root, x := entityVault(t, map[string]string{
		"projects/agentm/project.yaml": "slug: agentm\nrepositories:\n  - alexherrero/agentm\nformer_names:\n  - alexherrero/agentic-harness\n",
		"agent/memory/semantic/old.md": "---\ntitle: Old\ntype: reference\n---\n\nThe harness lived at github.com/alexherrero/agentic-harness, " +
			"released at https://github.com/alexherrero/agentic-harness/releases/tag/v2.0.0.\n",
		"agent/memory/semantic/new.md": "---\ntitle: New\ntype: reference\n---\n\nNow github.com/alexherrero/agentm, still " +
			"https://github.com/alexherrero/agentm/releases/tag/v2.0.0.\n",
		"agent/memory/semantic/notes.md": "---\ntitle: Notes\ntype: reference\n---\n\nSee " +
			"https://github.com/alexherrero/agentm/releases/tag/v2.0.0 for the notes.\n",
	})
	plan, err := PlanEntities(root, filepath.Dir(root), x, PeopleOptions{}, nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	got := map[string]EntityPage{}
	for _, p := range plan.Pages {
		got[p.ID] = p
	}
	// The release address names the repository too, so the repository counts
	// three notes; the release itself gets no page (task 202).
	if p := got["repo:alexherrero/agentm"]; p.Mentions != 3 {
		t.Errorf("repo:alexherrero/agentm: %+v, want one page counting the old name's note and the new ones' (3)", p)
	}
	if len(got) != 1 {
		t.Errorf("pages %v, want the repository's alone", keys(got))
	}
	for id := range got {
		if strings.Contains(id, "agentic-harness") {
			t.Errorf("the old name kept a page of its own: %s", id)
		}
	}
	var page string
	for _, in := range plan.Intents {
		if in.Rel == "memory/entities/repos/alexherrero-agentm.md" {
			page = string(in.After)
		}
	}
	if !strings.Contains(page, "aliases: [agentm, agentic-harness, alexherrero/agentic-harness]\n") {
		t.Errorf("the repository page does not answer to its old name:\n%s", page)
	}
}

func keys(m map[string]EntityPage) []string {
	var out []string
	for k := range m {
		out = append(out, k)
	}
	return out
}

// Issues and releases get no page however many origins name them, and a
// repository keeps its bar of two (task 202; the bar is task 190's). A task
// folder is one origin however many of its notes name the repository; a note
// outside a task folder is its own.
func TestIssuesAndReleasesHaveNoPagesAndRepositoriesNeedTwoOrigins(t *testing.T) {
	got := pageIDs(t, map[string]string{
		"projects/agentm/project.yaml":            "slug: agentm\nrepositories:\n  - alexherrero/agentm\n",
		"projects/agentm/tasks/001-a/plan.md":     "PR #700. Shipped v1.2.0. Also #800. See github.com/alexherrero/agentm.\n",
		"projects/agentm/tasks/001-a/progress.md": "#700 again, and #810. github.com/alexherrero/once is new.\n",
		"projects/agentm/tasks/002-b/plan.md":     "Follows #700. Shipped v1.2.0. And #810.\n",
		"projects/agentm/research/notes.md":       "#800 and #810 both matter.\n",
		"projects/agentm/tasks/003-c/plan.md":     "Shipped v1.2.0 with #800 once more.\n",
		"projects/other/brief.md":                 "Unrelated, but github.com/alexherrero/agentm is its home.\n",
	})
	for id := range got {
		if strings.HasPrefix(id, "issue:") || strings.HasPrefix(id, "release:") {
			t.Errorf("%s has a page; numbers are never paged", id)
		}
	}
	if _, ok := got["repo:alexherrero/agentm"]; !ok {
		t.Errorf("a repository two origins name lost its page; got %v", keys(got))
	}
	if _, ok := got["repo:alexherrero/once"]; ok {
		t.Errorf("a repository one origin names has a page")
	}
}

// The index can still name a task's notes where they were for a moment after
// the mover took the folder to `completed/tasks/` the same night. The page
// links where the note sits, and the folder is still one origin.
func TestAPageLinksAMovedTasksNoteWhereItSitsBeforeTheIndexCatchesUp(t *testing.T) {
	notes := map[string]string{
		"projects/agentm/project.yaml":            "slug: agentm\nrepositories:\n  - alexherrero/agentm\n",
		"projects/agentm/tasks/001-a/plan.md":     "Fixed with github.com/alexherrero/moved.\n",
		"projects/agentm/tasks/001-a/progress.md": "github.com/alexherrero/moved merged.\n",
		"projects/agentm/tasks/002-b/plan.md":     "Builds on github.com/alexherrero/moved.\n",
	}
	root, x := entityVault(t, notes)
	vault := filepath.Dir(root)
	from := filepath.Join(vault, "projects", "agentm", "tasks", "001-a")
	to := filepath.Join(vault, "projects", "agentm", "completed", "tasks", "001-a")
	if err := os.MkdirAll(filepath.Dir(to), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(from, to); err != nil {
		t.Fatal(err)
	}
	plan, err := PlanEntities(root, vault, x, PeopleOptions{}, nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	var page string
	for _, in := range plan.Intents {
		if strings.Contains(in.Rel, "repos/alexherrero-moved") {
			page = string(in.After)
		}
	}
	if page == "" {
		t.Fatalf("moved has two origins and no page planned: %+v", plan.Pages)
	}
	if !strings.Contains(page, "projects/agentm/completed/tasks/001-a/plan") ||
		strings.Contains(page, "projects/agentm/tasks/001-a/") {
		t.Errorf("the page does not link the moved task's notes where they sit:\n%s", page)
	}
}
