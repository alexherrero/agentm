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
