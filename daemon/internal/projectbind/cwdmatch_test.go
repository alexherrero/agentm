package projectbind

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

// folderVault is a vault where `secret` keeps its notes to folders containing
// `/secret-mono/`, `plain` has no rule, and `clean` excludes `plain`. It returns
// the vault and the root the code folders sit under.
func folderVault(t *testing.T, secretRule string) (vault, root string) {
	t.Helper()
	root = t.TempDir()
	root, _ = filepath.EvalSymlinks(root)
	vault = filepath.Join(root, "Vault")
	for slug, body := range map[string]string{
		"secret": "slug: secret\ncode_paths:\n  - " + filepath.Join(root, "code", "secret-mono") + "\n" + secretRule,
		"plain":  "slug: plain\ncode_paths:\n  - " + filepath.Join(root, "code", "plain") + "\n",
		"clean":  "slug: clean\ncode_paths:\n  - " + filepath.Join(root, "code", "clean") + "\nrecall_excludes:\n  - plain\n",
	} {
		dir := filepath.Join(vault, "projects", slug)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "project.yaml"), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	for _, d := range []string{"secret-mono/sub", "secret-mono-old", "plain", "clean", "Elsewhere/Secret-Mono"} {
		if err := os.MkdirAll(filepath.Join(root, "code", d), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	return vault, root
}

const listRule = "recall_cwd_match:\n  - /secret-mono/\n"

func TestAFolderRuleServesItsNotesOnlyInsideTheFolder(t *testing.T) {
	vault, root := folderVault(t, listRule)
	code := filepath.Join(root, "code")
	// A linked worktree outside the clone is the clone's by its .git file.
	slot := filepath.Join(root, "slots", "w1")
	if err := os.MkdirAll(slot, 0o755); err != nil {
		t.Fatal(err)
	}
	gitfile := "gitdir: " + filepath.Join(code, "secret-mono", ".git", "worktrees", "w1") + "\n"
	if err := os.WriteFile(filepath.Join(slot, ".git"), []byte(gitfile), 0o644); err != nil {
		t.Fatal(err)
	}
	for name, tc := range map[string]struct {
		dir  string
		want []string
	}{
		"the folder itself":            {filepath.Join(code, "secret-mono"), nil},
		"under it":                     {filepath.Join(code, "secret-mono", "sub"), nil},
		"another case":                 {filepath.Join(code, "Elsewhere", "Secret-Mono"), nil},
		"its worktree elsewhere":       {slot, nil},
		"a name that only starts so":   {filepath.Join(code, "secret-mono-old"), []string{"secret"}},
		"an unmarked project":          {filepath.Join(code, "plain"), []string{"secret"}},
		"its own folder in the vault":  {filepath.Join(vault, "projects", "secret"), []string{"secret"}},
		"nobody's folder":              {root, []string{"secret"}},
		"a folder that does not exist": {filepath.Join(root, "gone"), []string{"secret"}},
		"a project with its own rule":  {filepath.Join(code, "clean"), []string{"plain", "secret"}},
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

// A caller that names no folder is in no folder a project names.
func TestAFolderRuleWallsACallerWithNoFolder(t *testing.T) {
	vault, _ := folderVault(t, listRule)
	for name, get := range map[string]func() ([]string, error){
		"EveryExclusion":  func() ([]string, error) { return EveryExclusion(vault) },
		"a blank folder":  func() ([]string, error) { return Exclusions(vault, "") },
		"a spaces folder": func() ([]string, error) { return Exclusions(vault, "  ") },
	} {
		got, err := get()
		if err != nil || !reflect.DeepEqual(got, []string{"plain", "secret"}) {
			t.Errorf("%s: got %v, %v", name, got, err)
		}
	}
}

// A rule that cannot be trusted keeps the project's notes from every session,
// its own folder included, and stops nobody else's recall.
func TestAFolderRuleThatCannotBeReadWallsTheProjectEverywhere(t *testing.T) {
	for name, rule := range map[string]string{
		"a scalar":     "recall_cwd_match: /secret-mono/\n",
		"a bare slash": "recall_cwd_match:\n  - /\n",
		"a mapping":    "recall_cwd_match:\n  path: /secret-mono/\n",
	} {
		t.Run(name, func(t *testing.T) {
			vault, root := folderVault(t, rule)
			for _, dir := range []string{filepath.Join(root, "code", "secret-mono"), filepath.Join(root, "code", "plain")} {
				got, err := Exclusions(vault, dir)
				if err != nil || !reflect.DeepEqual(got, []string{"secret"}) {
					t.Errorf("%s: got %v, %v", dir, got, err)
				}
			}
		})
	}
}

func TestAnEmptyFolderRuleIsNoRule(t *testing.T) {
	vault, root := folderVault(t, "recall_cwd_match: []\n")
	if got, err := Exclusions(vault, filepath.Join(root, "code", "plain")); err != nil || got != nil {
		t.Fatalf("got %v, %v", got, err)
	}
}

// The wall covers the notes the recall_excludes wall covers: the project's
// folders, and a note elsewhere stamped with it.
func TestAWalledProjectsNotesAreThoseItsSlugNames(t *testing.T) {
	vault, root := folderVault(t, listRule)
	got, _ := Exclusions(vault, filepath.Join(root, "code", "plain"))
	for _, n := range []struct {
		rel, label string
		hidden     bool
	}{
		{"projects/secret/charter.md", "", true},
		{"agent/projects/completed/secret/old.md", "", true},
		{"memory/semantic/card.md", "secret", true},
		{"memory/semantic/card.md", "plain", false},
		{"projects/plain/charter.md", "", false},
	} {
		if NoteExcluded(n.rel, n.label, got) != n.hidden {
			t.Errorf("%s (project %q): hidden should be %v", n.rel, n.label, n.hidden)
		}
	}
}

// Found by review: the line reader keeps a YAML comment as part of the slug,
// which check-project-yaml accepts, so the wall named a project nobody has and
// the real one's notes were served everywhere. The folder's name is walled too.
func TestACommentAfterTheSlugStillWallsTheProject(t *testing.T) {
	vault, root := folderVault(t, listRule)
	body := "slug: secret  # a client's work\nrecall_cwd_match:\n  - /secret-mono/\n"
	if err := os.WriteFile(filepath.Join(vault, "projects", "secret", "project.yaml"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	got, err := Exclusions(vault, filepath.Join(root, "code", "plain"))
	if err != nil {
		t.Fatal(err)
	}
	if !NoteExcluded("projects/secret/charter.md", "", got) || !NoteExcluded("memory/a.md", "secret", got) {
		t.Fatalf("the project's notes are served outside its folder: %v", got)
	}
	if every, _ := EveryExclusion(vault); !NoteExcluded("projects/secret/charter.md", "", every) {
		t.Fatalf("a caller with no folder is served them: %v", every)
	}
}

// A path that only passes through the folder on its way elsewhere is outside.
func TestAPathThatLeavesTheFolderIsOutside(t *testing.T) {
	vault, root := folderVault(t, listRule)
	dir := filepath.Join(root, "code", "secret-mono") + string(filepath.Separator) + ".." + string(filepath.Separator) + "plain"
	if got, err := Exclusions(vault, dir); err != nil || !reflect.DeepEqual(got, []string{"secret"}) {
		t.Fatalf("got %v, %v", got, err)
	}
}

// A projects folder that will not open may hold any folder rule, so no
// session can be called clear of one, claimed or not.
func TestAProjectsFolderThatWillNotOpenIsAnError(t *testing.T) {
	vault, root := folderVault(t, listRule)
	completed := filepath.Join(vault, "projects", "completed")
	if err := os.MkdirAll(completed, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(completed, 0); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.Chmod(completed, 0o755) })
	if _, err := os.ReadDir(completed); err == nil {
		t.Skip("this user opens every folder")
	}
	for _, dir := range []string{filepath.Join(root, "code", "clean"), filepath.Join(root, "code", "plain")} {
		if got, err := Exclusions(vault, dir); err == nil {
			t.Errorf("%s: got %v and no error", dir, got)
		}
	}
}
