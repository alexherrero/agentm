package projectbind

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

// exclusionVault is a vault where `clean` excludes `port`, a project with
// neither, and a code folder of `clean`'s outside the vault.
func exclusionVault(t *testing.T) (vault, code string) {
	t.Helper()
	root := t.TempDir()
	root, _ = filepath.EvalSymlinks(root)
	vault = filepath.Join(root, "Vault")
	code = filepath.Join(root, "code", "clean")
	write := func(rel, body string) {
		t.Helper()
		p := filepath.Join(vault, rel)
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write("projects/clean/project.yaml", "slug: clean\ncode_paths:\n  - "+code+"\nrecall_excludes:\n  - port\n")
	write("projects/port/project.yaml", "slug: port\ncode_paths:\n  - "+filepath.Join(root, "code", "port")+"\n")
	write("projects/other/project.yaml", "slug: other\ncode_paths:\n  - "+filepath.Join(root, "code", "other")+"\n")
	for _, d := range []string{code, filepath.Join(root, "code", "port"), filepath.Join(root, "code", "other")} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	return vault, code
}

func TestExclusionsFollowTheSessionsFolder(t *testing.T) {
	vault, code := exclusionVault(t)
	root := filepath.Dir(vault)
	// A linked worktree outside the clone says whose it is only in its .git file.
	outside := filepath.Join(root, "elsewhere", "slot")
	if err := os.MkdirAll(filepath.Join(outside, "sub"), 0o755); err != nil {
		t.Fatal(err)
	}
	gitfile := "gitdir: " + filepath.Join(code, ".git", "worktrees", "slot") + "\n"
	if err := os.WriteFile(filepath.Join(outside, ".git"), []byte(gitfile), 0o644); err != nil {
		t.Fatal(err)
	}
	// A vault worktree holds a copy of the project's folder.
	vaultSlot := filepath.Join(vault, ".claude", "worktrees", "slot")
	if err := os.MkdirAll(filepath.Join(vaultSlot, "projects", "clean"), 0o755); err != nil {
		t.Fatal(err)
	}
	gitfile = "gitdir: " + filepath.Join(vault, ".git", "worktrees", "slot") + "\n"
	if err := os.WriteFile(filepath.Join(vaultSlot, ".git"), []byte(gitfile), 0o644); err != nil {
		t.Fatal(err)
	}
	for name, tc := range map[string]struct {
		dir  string
		want []string
	}{
		"the clone":                  {code, []string{"port"}},
		"a worktree under the clone": {filepath.Join(code, ".claude", "worktrees", "slot"), []string{"port"}},
		"a worktree outside it":      {filepath.Join(outside, "sub"), []string{"port"}},
		"its vault folder":           {filepath.Join(vault, "projects", "clean"), []string{"port"}},
		"under its vault folder":     {filepath.Join(vault, "projects", "clean", "tasks", "x"), []string{"port"}},
		"its folder, vault worktree": {filepath.Join(vaultSlot, "projects", "clean"), []string{"port"}},
		"the excluded project":       {filepath.Join(vault, "projects", "port"), nil},
		"another project":            {filepath.Join(root, "code", "other"), nil},
		"nobody's folder":            {root, nil},
	} {
		got, err := Exclusions(vault, tc.dir)
		if err != nil {
			t.Errorf("%s: %v", name, err)
		}
		if !reflect.DeepEqual(got, tc.want) {
			t.Errorf("%s: got %v, want %v", name, got, tc.want)
		}
	}
}

func TestAnUnreadableRuleIsAnErrorNotAnEmptyList(t *testing.T) {
	for name, rule := range map[string]string{
		"a scalar":        "recall_excludes: port\n",
		"a misspelt slug": "recall_excludes:\n  - prot\n",
	} {
		t.Run(name, func(t *testing.T) {
			vault, code := exclusionVault(t)
			body := "slug: clean\ncode_paths:\n  - " + code + "\n" + rule
			if err := os.WriteFile(filepath.Join(vault, "projects", "clean", "project.yaml"), []byte(body), 0o644); err != nil {
				t.Fatal(err)
			}
			if _, err := Exclusions(vault, code); err == nil {
				t.Fatalf("the clone got no error for %q", rule)
			}
			if _, err := Exclusions(vault, filepath.Join(vault, "projects", "clean")); err == nil {
				t.Fatalf("the vault folder got no error for %q", rule)
			}
			if _, err := EveryExclusion(vault); err == nil {
				t.Fatalf("a caller naming no folder got no error for %q", rule)
			}
			// Another project's session is not held to a rule it was never under.
			if got, err := Exclusions(vault, filepath.Join(vault, "projects", "other")); err != nil || got != nil {
				t.Fatalf("another project got %v, %v", got, err)
			}
		})
	}
}

// A project.yaml that will not read may be the one claiming this folder, so a
// folder nothing else claims cannot be called clear.
func TestAFileThatWillNotReadLeavesAnUnclaimedFolderUnknown(t *testing.T) {
	vault, code := exclusionVault(t)
	path := filepath.Join(vault, "projects", "clean", "project.yaml")
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(path, 0o755); err != nil { // reads fail whoever runs the test
		t.Fatal(err)
	}
	_, err := Exclusions(vault, code)
	if err == nil || !strings.Contains(err.Error(), "clean") {
		t.Fatalf("the session of the unreadable project got %v", err)
	}
	if _, err := Exclusions(vault, filepath.Join(vault, "projects", "clean")); err == nil {
		t.Fatalf("a session in its vault folder got no error")
	}
	if got, err := Exclusions(vault, filepath.Join(vault, "projects", "other")); err != nil || got != nil {
		t.Fatalf("a project that reads got %v, %v", got, err)
	}
}

func TestEveryExclusionIsTheUnion(t *testing.T) {
	vault, _ := exclusionVault(t)
	got, err := EveryExclusion(vault)
	if err != nil || !reflect.DeepEqual(got, []string{"port"}) {
		t.Fatalf("got %v, %v", got, err)
	}
}

func TestNoteExcluded(t *testing.T) {
	slugs := []string{"port"}
	for path, want := range map[string]bool{
		"projects/port/tracker.md":              true,
		"projects/port/desk/a/b.md":             true,
		"projects/Port/charter.md":              true,
		"projects/completed/port/charter.md":    true,
		"../projects/port/tracker.md":           true,
		"agent/projects/port/x.md":              true,
		"projects/port-two/tracker.md":          false,
		"projects/clean/research/port.md":       false,
		"projects/port.md":                      false,
		"projects/completed/port.md":            false,
		"projects/completed/other/port/note.md": false,
		"memory/semantic/port.md":               false,
	} {
		if got := NoteExcluded(path, "", slugs); got != want {
			t.Errorf("%s: got %v", path, got)
		}
	}
	if !NoteExcluded("memory/semantic/a.md", `"port"`, slugs) {
		t.Errorf("a stamped card was not excluded")
	}
	if NoteExcluded("projects/port/a.md", "port", nil) {
		t.Errorf("an empty rule excluded a note")
	}
}
