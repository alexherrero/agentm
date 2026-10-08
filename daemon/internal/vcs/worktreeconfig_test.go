package vcs

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// On 2026-10-02 a worktree made inside the vault left
// `extensions.worktreeConfig = true` in the vault repository's config. go-git
// v5.19.2 knows no such extension and refuses to open the repository, so after
// its restart on 2026-10-03 the daemon came up with git degraded: no commits, no
// undo, and the corpus-write gate refusing (#859). These tests build the live
// layout, a git directory outside the tree behind a `.git` pointer file, set the
// key with CLI git, and ask the daemon to work on it anyway.

// newKeyedRepo is a repository laid out like the vault, with one committed
// note at personal/kept.md and the given extra config set by CLI git. Returns
// the worktree root and the git directory.
func newKeyedRepo(t *testing.T, configs ...[2]string) (string, string) {
	t.Helper()
	isolateGit(t)
	base := t.TempDir()
	dir := filepath.Join(base, "vault")
	gitDir := filepath.Join(base, "vault-git", "vault.git")
	if err := os.MkdirAll(filepath.Dir(gitDir), 0o755); err != nil {
		t.Fatal(err)
	}
	mustWrite(t, filepath.Join(dir, "personal", "kept.md"), note)
	gitCLI(t, base, "init", "--initial-branch=main", "--separate-git-dir="+gitDir, dir)
	gitCLI(t, dir, "add", ".")
	gitCLI(t, dir, "commit", "-m", "initial")
	for _, kv := range configs {
		gitCLI(t, dir, "config", kv[0], kv[1])
	}
	return dir, gitDir
}

func TestWorktreeConfig_TheDaemonCommitsAndRevertsOnARepoCarryingTheKey(t *testing.T) {
	for _, tc := range []struct {
		name    string
		configs [][2]string
	}{
		{"format 0", [][2]string{{"extensions.worktreeConfig", "true"}}},
		// git itself raises the format to 1 when it enables the extension.
		{"format 1", [][2]string{{"core.repositoryformatversion", "1"}, {"extensions.worktreeConfig", "true"}}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			dir, gitDir := newKeyedRepo(t, tc.configs...)

			repo := Open(dir)
			if !repo.Available() {
				t.Fatalf("the daemon refused a repository carrying extensions.worktreeConfig: %s", repo.Status())
			}

			// A commit, and the revert point the corpus-write gate asks for.
			mustWrite(t, filepath.Join(dir, "personal", "kept.md"), "changed by the daemon\n")
			sha, err := repo.Commit(OriginLocal, []string{"personal/kept.md"})
			if err != nil || sha == "" {
				t.Fatalf("Commit = %q, %v", sha, err)
			}
			head, err := repo.Head()
			if err != nil || head != sha {
				t.Fatalf("Head = %q, %v; want the commit just made, %q", head, err, sha)
			}

			// Undo: CLI git reverts the daemon's commit, and the daemon reads the
			// result as clean.
			gitCLI(t, dir, "revert", "--no-edit", sha)
			if got := mustRead(t, filepath.Join(dir, "personal", "kept.md")); got != note {
				t.Fatalf("after the revert the note reads %q, want the original", got)
			}
			if dirty, err := repo.Dirty(); err != nil || len(dirty) != 0 {
				t.Fatalf("after the revert Dirty = %v, %v; want clean", dirty, err)
			}

			// The key is still on disk: the daemon hides it from its library and
			// never removes it.
			if got := strings.TrimSpace(gitCLI(t, dir, "config", "--get", "extensions.worktreeConfig")); got != "true" {
				t.Fatalf("extensions.worktreeConfig on disk = %q, want it left as the operator's", got)
			}
			if _, err := os.Stat(filepath.Join(gitDir, "config")); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestWorktreeConfig_AnyOtherUnknownExtensionStillRefuses(t *testing.T) {
	// Only the one key whose meaning the daemon doesn't depend on is hidden. An
	// extension that changes what the object store holds stays a refusal.
	dir, _ := newKeyedRepo(t, [2]string{"core.repositoryformatversion", "1"}, [2]string{"extensions.objectFormat", "sha256"})
	if repo := Open(dir); repo.Available() {
		t.Fatal("the daemon opened a repository with an extension it cannot honor")
	}
}

func TestWorktreeConfig_AConfigWrittenThroughTheLibraryKeepsTheKey(t *testing.T) {
	dir, _ := newKeyedRepo(t, [2]string{"extensions.worktreeConfig", "true"})
	repo := Open(dir)
	if !repo.Available() {
		t.Fatalf("Open: %s", repo.Status())
	}
	cfg, err := repo.repo.Storer.Config()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Raw.Section("extensions").HasOption("worktreeConfig") {
		t.Fatal("the library was shown extensions.worktreeConfig")
	}
	cfg.Raw.Section("user").SetOption("name", "someone")
	if err := repo.repo.Storer.SetConfig(cfg); err != nil {
		t.Fatal(err)
	}
	if got := strings.TrimSpace(gitCLI(t, dir, "config", "--get", "extensions.worktreeConfig")); got != "true" {
		t.Fatalf("a config written back through go-git dropped the key: %q", got)
	}
}
