package mcpsrv

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/note"
)

// exclusionServer is a server over a vault where `clean` excludes `port`, and
// the index holds one note of `port`'s and one neutral note.
func exclusionServer(t *testing.T) (*Server, string) {
	t.Helper()
	vault := t.TempDir()
	vault, _ = filepath.EvalSymlinks(vault)
	for slug, body := range map[string]string{
		"clean": "slug: clean\nrecall_excludes:\n  - port\n",
		"port":  "slug: port\n",
	} {
		dir := filepath.Join(vault, "projects", slug)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "project.yaml"), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	x, err := index.Open(filepath.Join(t.TempDir(), "index.db"), vault, "", false)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { x.Close() })
	for _, rel := range []string{"projects/port/tracker.md", "memory/semantic/keeper.md"} {
		n := note.Note{Rel: rel, Title: rel, Body: "floodplain zoning",
			Captured: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), CapturedSource: "mtime"}
		if err := x.Upsert(n, 1, 17); err != nil {
			t.Fatal(err)
		}
	}
	return &Server{cfg: &config.Config{VaultPath: vault}, idx: x}, vault
}

func searchPaths(t *testing.T, s *Server, args map[string]any) (paths []string, noteText string, err error) {
	t.Helper()
	raw, _ := json.Marshal(args)
	res, err := s.toolSearch(raw)
	if err != nil {
		return nil, "", err
	}
	blob, _ := json.Marshal(res.(map[string]any)["structuredContent"])
	var out index.SearchOutcome
	if err := json.Unmarshal(blob, &out); err != nil {
		t.Fatal(err)
	}
	for _, r := range out.Results {
		paths = append(paths, r.Path)
	}
	return paths, out.Note, nil
}

func TestMemorySearchHoldsASessionToItsRule(t *testing.T) {
	s, vault := exclusionServer(t)
	clean := filepath.Join(vault, "projects", "clean")
	port := filepath.Join(vault, "projects", "port")

	paths, _, err := searchPaths(t, s, map[string]any{"query": "floodplain zoning", "cwd": clean})
	if err != nil || len(paths) != 1 || paths[0] != "memory/semantic/keeper.md" {
		t.Errorf("the excluded session got %v, %v", paths, err)
	}
	paths, _, err = searchPaths(t, s, map[string]any{"query": "floodplain zoning", "cwd": port})
	if err != nil || len(paths) != 2 {
		t.Errorf("the excluded project's own session got %v, %v", paths, err)
	}
}

// The server cannot see who is calling, so a caller that does not say is held
// to every rule, and is told why.
func TestMemorySearchWithNoFolderIsHeldToEveryRule(t *testing.T) {
	s, _ := exclusionServer(t)
	for name, args := range map[string]map[string]any{
		"none":     {"query": "floodplain zoning"},
		"relative": {"query": "floodplain zoning", "cwd": "."},
	} {
		paths, noteText, err := searchPaths(t, s, args)
		if err != nil || len(paths) != 1 || paths[0] != "memory/semantic/keeper.md" {
			t.Errorf("%s: got %v, %v", name, paths, err)
		}
		if !strings.Contains(noteText, "cwd") {
			t.Errorf("%s: nothing told the caller to pass cwd: %q", name, noteText)
		}
	}
}

func TestMemorySearchRefusesWhenTheRuleCannotBeRead(t *testing.T) {
	s, vault := exclusionServer(t)
	clean := filepath.Join(vault, "projects", "clean")
	bad := []byte("slug: clean\nrecall_excludes: port\n")
	if err := os.WriteFile(filepath.Join(clean, "project.yaml"), bad, 0o644); err != nil {
		t.Fatal(err)
	}
	for name, args := range map[string]map[string]any{
		"the session": {"query": "floodplain zoning", "cwd": clean},
		"no folder":   {"query": "floodplain zoning"},
	} {
		if paths, _, err := searchPaths(t, s, args); err == nil {
			t.Errorf("%s: served %v from a rule that cannot be read", name, paths)
		}
	}
}

func TestMemorySearchAsksForTheFolder(t *testing.T) {
	for _, spec := range toolSpecs(nil) {
		if spec["name"] != "memory_search" {
			continue
		}
		schema := spec["inputSchema"].(map[string]any)
		if _, ok := schema["properties"].(map[string]any)["cwd"]; !ok {
			t.Fatal("memory_search does not advertise cwd")
		}
		required := schema["required"].([]string)
		for _, r := range required {
			if r == "cwd" {
				return
			}
		}
		t.Fatalf("cwd is not required: %v", required)
	}
	t.Fatal("memory_search is not in the tool list")
}

// A project's recall_cwd_match keeps its notes to sessions whose folder
// contains a fragment it names. Over MCP the folder is what `cwd` says, and a
// caller that names none is in no folder.
func TestMemorySearchServesAFolderRuledProjectOnlyInsideItsFolder(t *testing.T) {
	s, vault := exclusionServer(t)
	secret := filepath.Join(vault, "projects", "secret")
	if err := os.MkdirAll(secret, 0o755); err != nil {
		t.Fatal(err)
	}
	rule := []byte("slug: secret\nrecall_cwd_match:\n  - /secret-mono/\n")
	if err := os.WriteFile(filepath.Join(secret, "project.yaml"), rule, 0o644); err != nil {
		t.Fatal(err)
	}
	n := note.Note{Rel: "projects/secret/charter.md", Title: "charter", Body: "floodplain zoning",
		Captured: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), CapturedSource: "mtime"}
	if err := s.idx.Upsert(n, 1, 17); err != nil {
		t.Fatal(err)
	}
	inside := filepath.Join(t.TempDir(), "secret-mono", "web")
	if err := os.MkdirAll(inside, 0o755); err != nil {
		t.Fatal(err)
	}
	has := func(paths []string) bool {
		for _, p := range paths {
			if p == "projects/secret/charter.md" {
				return true
			}
		}
		return false
	}
	for name, tc := range map[string]struct {
		args map[string]any
		want bool
	}{
		"inside the folder":    {map[string]any{"query": "floodplain zoning", "cwd": inside}, true},
		"another folder":       {map[string]any{"query": "floodplain zoning", "cwd": t.TempDir()}, false},
		"its own vault folder": {map[string]any{"query": "floodplain zoning", "cwd": secret}, false},
		"no folder":            {map[string]any{"query": "floodplain zoning"}, false},
		"a relative folder":    {map[string]any{"query": "floodplain zoning", "cwd": "secret-mono"}, false},
	} {
		paths, _, err := searchPaths(t, s, tc.args)
		if err != nil {
			t.Errorf("%s: %v", name, err)
			continue
		}
		if has(paths) != tc.want {
			t.Errorf("%s: served the folder-ruled note = %v, want %v (%v)", name, has(paths), tc.want, paths)
		}
		// An unmarked project's note is served wherever its own walls allow.
		if name == "another folder" && len(paths) != 2 {
			t.Errorf("%s: the folder rule took other notes too: %v", name, paths)
		}
	}
}
