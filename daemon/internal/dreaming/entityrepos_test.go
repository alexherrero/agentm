package dreaming

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// Task 186: the bars the vault growth audit of 2026-10-03 asked for. A task's
// own notes count once toward a page's bar; a number or version the
// repository's clone never had gets no page; a renamed repository's old name
// folds into its current page.

// gitRepo makes a clone with the given commit subjects, oldest first, and tags.
// It reads no global or system git config, so a machine's hooks never run.
func gitRepo(t *testing.T, subjects []string, tags []string) string {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git is not installed")
	}
	dir := filepath.Join(t.TempDir(), "clone")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	global := filepath.Join(t.TempDir(), "gitconfig")
	if err := os.WriteFile(global, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	run := func(args ...string) {
		t.Helper()
		cmd := exec.Command("git", append([]string{"-C", dir}, args...)...)
		cmd.Env = append(os.Environ(), "GIT_CONFIG_GLOBAL="+global, "GIT_CONFIG_NOSYSTEM=1",
			"GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@example.com",
			"GIT_COMMITTER_NAME=t", "GIT_COMMITTER_EMAIL=t@example.com")
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v\n%s", args, err, out)
		}
	}
	run("init", "-q")
	for _, s := range subjects {
		run("commit", "-q", "--allow-empty", "-m", s)
	}
	for _, tag := range tags {
		run("tag", tag)
	}
	return dir
}

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
// a number only one task names gets no page, and one a second task names does.
func TestATaskFolderCountsOnceTowardThePagesBar(t *testing.T) {
	got := pageIDs(t, map[string]string{
		"projects/agentm/project.yaml":                      "slug: agentm\nrepositories:\n  - alexherrero/agentm\n",
		"projects/agentm/tasks/003-c/plan.md":               "Shipped in PR #553.\n",
		"projects/agentm/tasks/003-c/progress.md":           "Completed step 4 (PR #553).\n",
		"projects/agentm/tasks/003-c/tracker.md":            "Step 4 is PR #553.\n",
		"projects/agentm/tasks/004-d/plan.md":               "Reopened #600.\n",
		"projects/agentm/completed/tasks/001-a/progress.md": "See #600.\n",
	})
	if p, ok := got["issue:alexherrero/agentm#553"]; ok {
		t.Errorf("one task's plan, progress and tracker cleared the bar of two: %+v", p)
	}
	if p := got["issue:alexherrero/agentm#600"]; p.Mentions != 2 {
		t.Errorf("#600 is named by two tasks, one of them completed: %+v, want a page with 2 mentions", p)
	}
}

// The clone decides: an issue number above every `#N` its commits cite is no
// issue of it, and a version that is not one of its tags is no release.
func TestTheClonesHistoryDecidesWhichNumbersAndVersionsGetPages(t *testing.T) {
	clone := gitRepo(t, []string{"first", "Add the extractor (#12)", "fix: closes #40"}, []string{"v1.0.0"})
	home := gitRepo(t, []string{"initial import", "add the pest notes"}, nil)
	got := pageIDs(t, map[string]string{
		"projects/agentm/project.yaml":        "slug: agentm\nrepositories:\n  - alexherrero/agentm\ncode_paths:\n  - " + clone + "\n",
		"projects/house/project.yaml":         "slug: house\nrepositories:\n  - alexherrero/home-tools\ncode_paths:\n  - " + home + "\n",
		"projects/blog/project.yaml":          "slug: blog\nrepositories:\n  - alexherrero/blog\n",
		"projects/agentm/tasks/001-a/plan.md": "Fixed in #40, and #41 is next.\nShipped v1.0.0 and v2.0.0.\n",
		"projects/agentm/tasks/002-b/plan.md": "After #40 came #41. Since v1.0.0 and v2.0.0.\n",
		"projects/house/research/house-a.md":  "Pest supplemental #28272 and MLS #41138876.\n",
		"projects/house/research/house-b.md":  "Pest #28272 again, MLS #41138876 removed.\n",
		"projects/blog/docs/brand.md":         "Ticket #12 for the palette.\n",
		"projects/blog/parts/brand.md":        "Ticket #12 again.\n",
	})
	for id, want := range map[string]bool{
		"issue:alexherrero/agentm#40":           true,  // its commits cite #40
		"issue:alexherrero/agentm#41":           false, // nothing it merged reaches #41
		"release:alexherrero/agentm@v1.0.0":     true,  // a tag
		"release:alexherrero/agentm@v2.0.0":     false, // never tagged
		"issue:alexherrero/home-tools#28272":    false, // a repository that cites no number has no issue pages
		"issue:alexherrero/home-tools#41138876": false,
		"issue:alexherrero/blog#12":             true, // no clone on this machine: the old reading stands
	} {
		if _, ok := got[id]; ok != want {
			t.Errorf("%s: page %v, want %v (pages %v)", id, ok, want, keys(got))
		}
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
	})
	plan, err := PlanEntities(root, filepath.Dir(root), x, PeopleOptions{}, nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	got := map[string]EntityPage{}
	for _, p := range plan.Pages {
		got[p.ID] = p
	}
	for _, id := range []string{"repo:alexherrero/agentm", "release:alexherrero/agentm@v2.0.0"} {
		if got[id].Mentions != 2 {
			t.Errorf("%s: %+v, want one page counting the old name's note and the new one's", id, got[id])
		}
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
