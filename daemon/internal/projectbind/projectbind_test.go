package projectbind

import (
	"os"
	"path/filepath"
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
