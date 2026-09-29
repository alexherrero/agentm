package capture

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A capture is labelled with the project whose project.yaml lists the folder
// its session runs in (agentm-vault § Projects and tasks, amended 2026-09-28).
func TestACaptureIsLabelledWithItsSessionsProject(t *testing.T) {
	cp := newHarness(t)
	code := t.TempDir()
	for _, slug := range []string{"agentm", "crickets"} {
		if err := os.MkdirAll(filepath.Join(code, slug), 0o755); err != nil {
			t.Fatal(err)
		}
		dir := filepath.Join(cp.cfg.VaultPath, "projects", slug)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		yaml := "slug: " + slug + "\ncode_paths:\n  - " + filepath.Join(code, slug) + "\n"
		if err := os.WriteFile(filepath.Join(dir, "project.yaml"), []byte(yaml), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	label := func(req Request) string {
		t.Helper()
		res, err := cp.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		for _, line := range strings.Split(readNote(t, cp, res.Path), "\n") {
			if strings.HasPrefix(line, "project: ") {
				return strings.Trim(strings.TrimPrefix(line, "project: "), `"`)
			}
		}
		return ""
	}
	for name, c := range map[string]struct {
		req  Request
		want string
	}{
		"agentm checkout":        {Request{Text: "A fix in agentm.", Type: "fix", Cwd: filepath.Join(code, "agentm")}, "agentm"},
		"crickets checkout":      {Request{Text: "A fix in crickets.", Type: "fix", Cwd: filepath.Join(code, "crickets")}, "crickets"},
		"unregistered folder":    {Request{Text: "A fix elsewhere.", Type: "fix", Cwd: code}, ""},
		"no folder":              {Request{Text: "A fix from nowhere.", Type: "fix"}, ""},
		"a convention":           {Request{Text: "Squash-merge every plan.", Type: "convention", Cwd: filepath.Join(code, "agentm")}, ""},
		"a preference":           {Request{Text: "Tabs, please.", Type: "preference", Cwd: filepath.Join(code, "agentm")}, ""},
		"the caller's word wins": {Request{Text: "A crickets fix, named.", Type: "fix", Project: "crickets", Cwd: filepath.Join(code, "agentm")}, "crickets"},
		"a named convention":     {Request{Text: "An agentm-only rule.", Type: "convention", Project: "agentm"}, "agentm"},
	} {
		if got := label(c.req); got != c.want {
			t.Errorf("%s: project %q, want %q", name, got, c.want)
		}
	}
}
