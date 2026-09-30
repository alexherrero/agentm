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
