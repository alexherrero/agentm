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

// A vault's path is data: a bracket in it must not read as a pattern and lose
// every rule without a word.
func TestAVaultPathWithAGlobCharacterStillHasItsRules(t *testing.T) {
	root, _ := filepath.EvalSymlinks(t.TempDir())
	vault := filepath.Join(root, "drive[1]", "vault")
	for slug, body := range map[string]string{"clean": "slug: clean\nrecall_excludes:\n  - port\n", "port": "slug: port\n"} {
		dir := filepath.Join(vault, "projects", slug)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "project.yaml"), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	got, err := Exclusions(vault, filepath.Join(vault, "projects", "clean"))
	if err != nil || !reflect.DeepEqual(got, []string{"port"}) {
		t.Fatalf("got %v, %v", got, err)
	}
	if got, err := EveryExclusion(vault); err != nil || !reflect.DeepEqual(got, []string{"port"}) {
		t.Fatalf("every: got %v, %v", got, err)
	}
}

func linkWorktree(t *testing.T, slot, gitdir string) {
	t.Helper()
	if err := os.MkdirAll(slot, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(slot, ".git"), []byte("gitdir: "+gitdir+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestAWorktreeOfABareRepositoryKnowsItsProject(t *testing.T) {
	vault, _ := exclusionVault(t)
	root := filepath.Dir(vault)
	bare := filepath.Join(root, "code", "bare.git")
	body := "slug: clean\ncode_paths:\n  - " + bare + "\nrecall_excludes:\n  - port\n"
	if err := os.WriteFile(filepath.Join(vault, "projects", "clean", "project.yaml"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	slot := filepath.Join(root, "wts", "a")
	linkWorktree(t, slot, filepath.Join(bare, "worktrees", "a"))
	if got, err := Exclusions(vault, slot); err != nil || !reflect.DeepEqual(got, []string{"port"}) {
		t.Fatalf("got %v, %v", got, err)
	}
}

// A worktree's own folder being claimed must not hide the main checkout's claim.
func TestEveryClaimOnAFolderCounts(t *testing.T) {
	vault, code := exclusionVault(t)
	root := filepath.Dir(vault)
	wts := filepath.Join(root, "wts")
	umbrella := "slug: umbrella\ncode_paths:\n  - " + wts + "\n"
	if err := os.MkdirAll(filepath.Join(vault, "projects", "umbrella"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(vault, "projects", "umbrella", "project.yaml"), []byte(umbrella), 0o644); err != nil {
		t.Fatal(err)
	}
	slot := filepath.Join(wts, "a")
	linkWorktree(t, slot, filepath.Join(code, ".git", "worktrees", "a"))
	if got, err := Exclusions(vault, slot); err != nil || !reflect.DeepEqual(got, []string{"port"}) {
		t.Fatalf("a worktree in another project's folder: got %v, %v", got, err)
	}
	// Two projects listing one folder: the earlier name must not win it.
	first := "slug: aaa\ncode_paths:\n  - " + code + "\n"
	if err := os.MkdirAll(filepath.Join(vault, "projects", "aaa"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(vault, "projects", "aaa", "project.yaml"), []byte(first), 0o644); err != nil {
		t.Fatal(err)
	}
	if got, err := Exclusions(vault, code); err != nil || !reflect.DeepEqual(got, []string{"port"}) {
		t.Fatalf("a folder two projects list: got %v, %v", got, err)
	}
}

// A project under a broader claim is not walled out of its own notes.
func TestAClaimingProjectIsNeverExcludedFromItself(t *testing.T) {
	vault, code := exclusionVault(t)
	nested := filepath.Join(code, "vendor", "port")
	if err := os.MkdirAll(nested, 0o755); err != nil {
		t.Fatal(err)
	}
	body := "slug: port\ncode_paths:\n  - " + nested + "\n"
	if err := os.WriteFile(filepath.Join(vault, "projects", "port", "project.yaml"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	if got, err := Exclusions(vault, nested); err != nil || got != nil {
		t.Fatalf("got %v, %v", got, err)
	}
}

func TestAFolderSpelledInAnotherCaseIsTheSameFolder(t *testing.T) {
	vault, code := exclusionVault(t)
	if got, err := Exclusions(vault, strings.ToUpper(code)); err != nil || !reflect.DeepEqual(got, []string{"port"}) {
		t.Fatalf("the clone: got %v, %v", got, err)
	}
	dir := filepath.Join(vault, "PROJECTS", "Clean")
	if got, err := Exclusions(vault, dir); err != nil || !reflect.DeepEqual(got, []string{"port"}) {
		t.Fatalf("the vault folder: got %v, %v", got, err)
	}
}

func TestARuleTheReaderCannotTakeIsNeverAnEmptyRule(t *testing.T) {
	for name, rule := range map[string]string{
		"a list on the next line": "recall_excludes:\n  [port]\n",
		"a quoted key":            "'recall_excludes': [port]\n",
		"a bare key":              "recall_excludes:\n",
		"a scalar on the next":    "recall_excludes:\n  port\n",
		"a trailing slash":        "recall_excludes:\n  - port/\n",
		"a path":                  "recall_excludes:\n  - completed/port\n",
		"a climbing path":         "recall_excludes:\n  - ../projects/port\n",
		"an empty entry":          "recall_excludes:\n  - \"\"\n",
	} {
		t.Run(name, func(t *testing.T) {
			vault, code := exclusionVault(t)
			body := "slug: clean\ncode_paths:\n  - " + code + "\n" + rule
			if err := os.WriteFile(filepath.Join(vault, "projects", "clean", "project.yaml"), []byte(body), 0o644); err != nil {
				t.Fatal(err)
			}
			if got, err := Exclusions(vault, code); err == nil {
				t.Fatalf("no error, slugs %q", got)
			}
		})
	}
}

func TestAnExplicitlyEmptyRuleIsARule(t *testing.T) {
	vault, code := exclusionVault(t)
	body := "slug: clean\ncode_paths:\n  - " + code + "\nrecall_excludes: []\n# recall_excludes, in a comment\n"
	if err := os.WriteFile(filepath.Join(vault, "projects", "clean", "project.yaml"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	if got, err := Exclusions(vault, code); err != nil || len(got) != 0 {
		t.Fatalf("got %v, %v", got, err)
	}
}

func TestAProjectFolderThatWillNotOpenIsNotAClearSession(t *testing.T) {
	if os.Getuid() == 0 {
		t.Skip("root reads everything")
	}
	vault, code := exclusionVault(t)
	dir := filepath.Join(vault, "projects", "clean")
	if err := os.Chmod(dir, 0); err != nil {
		t.Skip("this filesystem has no such permission")
	}
	t.Cleanup(func() { os.Chmod(dir, 0o755) })
	if got, err := Exclusions(vault, code); err == nil {
		t.Fatalf("no error, slugs %v", got)
	}
}
