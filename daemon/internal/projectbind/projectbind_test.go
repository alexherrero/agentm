package projectbind

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeYAML(t *testing.T, vault, slug, body string) {
	t.Helper()
	dir := filepath.Join(vault, "projects", slug)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "project.yaml"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func fixture(t *testing.T) (vault, code string) {
	t.Helper()
	root := t.TempDir()
	vault, code = filepath.Join(root, "vault"), filepath.Join(root, "code")
	for _, d := range []string{"agentm/.claude/worktrees/slot", "agentm/vendor/tool", "crickets", "downloads"} {
		if err := os.MkdirAll(filepath.Join(code, d), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	writeYAML(t, vault, "agentm", "slug: agentm\ntitle: AgentM\ncode_paths:\n  - "+filepath.Join(code, "agentm")+"\n")
	writeYAML(t, vault, "crickets", "slug: crickets\r\ncode_paths:\r\n  - \""+filepath.Join(code, "crickets")+"\"\r\n")
	writeYAML(t, vault, "tool", "slug: tool\ncode_paths: ["+filepath.Join(code, "agentm", "vendor", "tool")+"]\n")
	writeYAML(t, vault, "empty", "slug: empty\ncode_paths: []\n")
	return vault, code
}

func TestAFolderBindsToTheProjectThatListsIt(t *testing.T) {
	vault, code := fixture(t)
	for dir, want := range map[string]string{
		filepath.Join(code, "agentm"):                                 "agentm",
		filepath.Join(code, "agentm", ".claude", "worktrees", "slot"): "agentm",
		filepath.Join(code, "agentm", "vendor", "tool"):               "tool",
		filepath.Join(code, "crickets"):                               "crickets",
		filepath.Join(code, "downloads"):                              "",
		filepath.Join(code, "agentm-sibling"):                         "",
		"":                                                            "",
	} {
		if got := Resolve(vault, dir); got != want {
			t.Errorf("Resolve(%q) = %q, want %q", dir, got, want)
		}
	}
}

func TestAHomeRelativePathIsExpanded(t *testing.T) {
	home, err := os.UserHomeDir()
	if err != nil {
		t.Skip("no home directory")
	}
	vault := t.TempDir()
	writeYAML(t, vault, "homebound", "slug: homebound\ncode_paths:\n  - ~/\n")
	if got := Resolve(vault, home); got != "homebound" {
		t.Errorf("Resolve(home) = %q, want homebound", got)
	}
}

// Task 179: each project's repositories, which qualify a bare `#12`.
func TestRepositoriesMapsEachProjectToWhatItLists(t *testing.T) {
	vault := t.TempDir()
	writeYAML(t, vault, "agentm", "slug: agentm\nrepositories:\n  - AlexHerrero/AgentM\ncode_paths:\n  - ~/x\n")
	writeYAML(t, vault, "two", "slug: two\nrepositories: [a/b, \"c/d\"]\n")
	writeYAML(t, vault, "none", "slug: none\nrepositories: []\n")
	writeYAML(t, vault, "completed/old", "slug: old\nrepositories:\n  - a/old\n")
	got := Repositories(vault)
	want := map[string][]string{"agentm": {"alexherrero/agentm"}, "two": {"a/b", "c/d"}, "none": {}, "old": {"a/old"}}
	if len(got) != len(want) {
		t.Fatalf("Repositories = %v, want %v", got, want)
	}
	for slug, repos := range want {
		if strings.Join(got[slug], ",") != strings.Join(repos, ",") {
			t.Errorf("%s: %v, want %v", slug, got[slug], repos)
		}
	}
	before := Signature(vault)
	writeYAML(t, vault, "none", "slug: none\nrepositories:\n  - a/none\n")
	if Signature(vault) == before {
		t.Error("the signature did not move when a project.yaml changed")
	}
}

func TestANoteBelongsToItsPlaceFirstAndThenItsLabel(t *testing.T) {
	projects := map[string][]string{"agentm": {"alexherrero/agentm"}, "crickets": {"alexherrero/crickets"}, "old": {"a/old"}}
	for _, c := range []struct{ rel, label, want string }{
		{"projects/agentm/tasks/179-x/plan.md", "", "agentm"},
		{"projects/agentm/completed/tasks/100-y/progress.md", "", "agentm"},
		{"projects/completed/old/charter.md", "", "old"},
		{"projects/agentm/decisions/d.md", "crickets", "agentm"},
		{"agent/memory/episodic/t.md", "crickets", "crickets"},
		{"agent/memory/semantic/c.md", "[[projects/agentm/charter|agentm]]", ""},
		{"agent/memory/semantic/c.md", "", ""},
		{"projects/moc-projects.md", "", ""},
	} {
		if got := NoteProject(c.rel, c.label, projects); got != c.want {
			t.Errorf("NoteProject(%q, %q) = %q, want %q", c.rel, c.label, got, c.want)
		}
	}
}

func TestAShortNameTwoRepositoriesShareSaysNeither(t *testing.T) {
	got := ShortNames(map[string][]string{"a": {"alexherrero/agentm"}, "b": {"x/tools", "y/tools"}, "c": {}})
	if got["agentm"] != "alexherrero/agentm" || len(got) != 1 {
		t.Errorf("ShortNames = %v", got)
	}
}

// Task 186: a renamed repository's old name maps to the project's one
// repository, and a name some project still uses is never "former".
func TestFormerNamesMapAnOldNameToTheProjectsRepository(t *testing.T) {
	vault := t.TempDir()
	writeYAML(t, vault, "agentm", "slug: agentm\nrepositories:\n  - alexherrero/agentm\nformer_names:\n  - Alexherrero/Agentic-Harness\n")
	writeYAML(t, vault, "crickets", "slug: crickets\nrepositories: [alexherrero/crickets]\nformer_names: [alexherrero/agent-toolkit, alexherrero/agentm]\n")
	writeYAML(t, vault, "metro", "slug: metro\nrepositories:\n  - o/metro-dev\n  - o/metro\nformer_names:\n  - o/old-metro\n")
	got := FormerNames(vault)
	want := map[string]string{
		"alexherrero/agentic-harness": "alexherrero/agentm",
		"alexherrero/agent-toolkit":   "alexherrero/crickets",
	}
	if len(got) != len(want) {
		t.Fatalf("FormerNames = %v, want %v (no current name, no name from a two-repository project)", got, want)
	}
	for old, now := range want {
		if got[old] != now {
			t.Errorf("FormerNames[%q] = %q, want %q", old, got[old], now)
		}
	}
}

// Task 186: a repository's clone is the project's code path that is a git
// checkout; with several repositories, the folder's name says which.
func TestClonesFindEachRepositorysCheckout(t *testing.T) {
	root := t.TempDir()
	vault, code := filepath.Join(root, "vault"), filepath.Join(root, "code")
	for _, d := range []string{"solo/.git", "metro-dev/.git", "metro", "plain"} {
		if err := os.MkdirAll(filepath.Join(code, d), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	writeYAML(t, vault, "solo", "slug: solo\nrepositories: [o/solo-repo]\ncode_paths:\n  - "+filepath.Join(code, "plain")+"\n  - "+filepath.Join(code, "solo")+"\n")
	writeYAML(t, vault, "metro", "slug: metro\nrepositories:\n  - o/metro-dev\n  - o/metro\ncode_paths:\n  - "+filepath.Join(code, "metro-dev")+"\n  - "+filepath.Join(code, "metro")+"\n")
	writeYAML(t, vault, "gone", "slug: gone\nrepositories: [o/gone]\ncode_paths: ["+filepath.Join(code, "missing")+"]\n")
	got := Clones(vault)
	if got["o/solo-repo"] != realPath(filepath.Join(code, "solo")) {
		t.Errorf("solo: %q, want its one checkout (the path with no .git is skipped)", got["o/solo-repo"])
	}
	if got["o/metro-dev"] != realPath(filepath.Join(code, "metro-dev")) {
		t.Errorf("metro-dev: %q, want the folder named for it", got["o/metro-dev"])
	}
	if _, ok := got["o/metro"]; ok {
		t.Errorf("o/metro has no checkout (its folder is not a git repository): %q", got["o/metro"])
	}
	if _, ok := got["o/gone"]; ok {
		t.Errorf("a missing path is no clone: %v", got)
	}
}

// Task 187: a project's `bare_issue_floor` applies to each repository it lists.
// A file that sets none, or sets something other than a positive whole number,
// gives no floor; a comment after the value is not part of it.
func TestIssueFloorsMapEachListedRepositoryToItsProjectsFloor(t *testing.T) {
	vault := t.TempDir()
	writeYAML(t, vault, "agentm", "slug: agentm\nrepositories:\n  - Alexherrero/Agentm\nbare_issue_floor: 48\n")
	writeYAML(t, vault, "crickets", "slug: crickets\r\nrepositories: [alexherrero/crickets]\r\nbare_issue_floor: \"48\"  # the roadmap's last item, plus one\r\n")
	writeYAML(t, vault, "metro", "slug: metro\nrepositories:\n  - o/metro-dev\n  - o/metro\nbare_issue_floor: 12\n")
	writeYAML(t, vault, "shared", "slug: shared\nrepositories: [o/metro]\nbare_issue_floor: 30\n")
	writeYAML(t, vault, "blog", "slug: blog\nrepositories: [o/blog]\n")
	writeYAML(t, vault, "bad", "slug: bad\nrepositories: [o/bad]\nbare_issue_floor: forty\n")
	writeYAML(t, vault, "zero", "slug: zero\nrepositories: [o/zero]\nbare_issue_floor: 0\n")
	got := IssueFloors(vault)
	want := map[string]int{
		"alexherrero/agentm":   48,
		"alexherrero/crickets": 48,
		"o/metro-dev":          12,
		"o/metro":              30,
	}
	if len(got) != len(want) {
		t.Fatalf("IssueFloors = %v, want %v (no floor for blog, bad or zero)", got, want)
	}
	for repo, floor := range want {
		if got[repo] != floor {
			t.Errorf("IssueFloors[%q] = %d, want %d", repo, got[repo], floor)
		}
	}
}
