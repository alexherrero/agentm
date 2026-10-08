package vcs

import (
	"bytes"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// Task 189 step 4: the daemon names a git worktree in the vault on its status
// surface the same day, and removes the record of one that is already gone.
// Each sign is seeded in a repository laid out like the vault and must appear,
// then clear once it is removed. The empty `.claude/worktrees` the live vault
// has carried since 2026-08-16 must not count.

func checkedSigns(t *testing.T, repo *Repo) []string {
	t.Helper()
	signs := repo.CheckWorktrees()
	if got := repo.WorktreeSigns(); strings.Join(got, "|") != strings.Join(signs, "|") {
		t.Fatalf("WorktreeSigns = %v, want the last check's %v", got, signs)
	}
	return signs
}

func assertSign(t *testing.T, signs []string, fragment string) {
	t.Helper()
	for _, s := range signs {
		if strings.Contains(s, fragment) {
			return
		}
	}
	t.Fatalf("no sign mentions %q: %v", fragment, signs)
}

func openKeyed(t *testing.T) (*Repo, string, string) {
	t.Helper()
	dir, gitDir := newKeyedRepo(t)
	repo := Open(dir)
	if !repo.Available() {
		t.Fatalf("Open: %s", repo.Status())
	}
	return repo, dir, gitDir
}

func TestWorktreeSigns_ACleanVaultAndTheEmptyRootFolderHaveNone(t *testing.T) {
	repo, dir, _ := openKeyed(t)
	if signs := checkedSigns(t, repo); len(signs) != 0 {
		t.Fatalf("a clean vault reported %v", signs)
	}
	if err := os.MkdirAll(filepath.Join(dir, ".claude", "worktrees"), 0o755); err != nil {
		t.Fatal(err)
	}
	if signs := checkedSigns(t, repo); len(signs) != 0 {
		t.Fatalf("the empty .claude/worktrees at the vault root counted: %v", signs)
	}
}

func TestWorktreeSigns_ARegisteredWorktreeIsNamedThenClears(t *testing.T) {
	repo, dir, _ := openKeyed(t)
	outside := filepath.Join(t.TempDir(), "elsewhere")
	gitCLI(t, dir, "worktree", "add", "-q", "--detach", outside)
	assertSign(t, checkedSigns(t, repo), "git lists a worktree at")
	gitCLI(t, dir, "worktree", "remove", "--force", outside)
	if signs := checkedSigns(t, repo); len(signs) != 0 {
		t.Fatalf("after the worktree went, still %v", signs)
	}
}

// backdate sets every file in a worktree's record to four months ago, past
// WorktreePruneExpire. git measures a gone worktree's age from its record's
// index file.
func backdate(t *testing.T, gitDir, name string) {
	t.Helper()
	old := time.Now().Add(-120 * 24 * time.Hour)
	dir := filepath.Join(gitDir, "worktrees", name)
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if err := os.Chtimes(filepath.Join(dir, e.Name()), old, old); err != nil {
			t.Fatal(err)
		}
	}
}

func TestWorktreeSigns_TheRecordOfAWorktreeGonePastTheExpiryIsPruned(t *testing.T) {
	repo, dir, gitDir := openKeyed(t)
	var logged bytes.Buffer
	repo.SetLogger(slog.New(slog.NewTextHandler(&logged, nil)))
	gone := filepath.Join(t.TempDir(), "gone")
	gitCLI(t, dir, "worktree", "add", "-q", "--detach", gone)
	if err := os.RemoveAll(gone); err != nil {
		t.Fatal(err)
	}
	backdate(t, gitDir, "gone")
	if signs := checkedSigns(t, repo); len(signs) != 0 {
		t.Fatalf("after the prune, still %v", signs)
	}
	if entries, _ := os.ReadDir(filepath.Join(gitDir, "worktrees")); len(entries) != 0 {
		t.Fatalf("the record of the gone worktree is still there: %d entries", len(entries))
	}
	if !strings.Contains(logged.String(), "pruned the records of worktrees that are gone") {
		t.Fatalf("the prune was not logged:\n%s", logged.String())
	}
}

func TestWorktreeSigns_ARecentlyMissingWorktreeKeepsItsRecordAndItsHead(t *testing.T) {
	// A worktree on a volume that isn't mounted looks gone. Its record holds its
	// HEAD and index, so pruning it early would lose a detached commit.
	repo, dir, _ := openKeyed(t)
	wt := filepath.Join(t.TempDir(), "usb")
	gitCLI(t, dir, "worktree", "add", "-q", "--detach", wt)
	mustWrite(t, filepath.Join(wt, "personal", "kept.md"), "work on the stick\n")
	gitCLI(t, wt, "commit", "-qam", "detached work")
	head := strings.TrimSpace(gitCLI(t, wt, "rev-parse", "HEAD"))
	away := wt + "-unmounted"
	if err := os.Rename(wt, away); err != nil {
		t.Fatal(err)
	}
	assertSign(t, checkedSigns(t, repo), "whose directory is gone")
	if err := os.Rename(away, wt); err != nil {
		t.Fatal(err)
	}
	if got := strings.TrimSpace(gitCLI(t, wt, "rev-parse", "HEAD")); got != head {
		t.Fatalf("the remounted worktree's HEAD is %q, want %q", got, head)
	}
}

func TestWorktreeSigns_ALockedRecordIsNotReportedPruned(t *testing.T) {
	repo, dir, gitDir := openKeyed(t)
	var logged bytes.Buffer
	repo.SetLogger(slog.New(slog.NewTextHandler(&logged, nil)))
	wt := filepath.Join(t.TempDir(), "locked")
	gitCLI(t, dir, "worktree", "add", "-q", "--detach", wt)
	gitCLI(t, dir, "worktree", "lock", "--reason", "on a stick", wt)
	if err := os.RemoveAll(wt); err != nil {
		t.Fatal(err)
	}
	backdate(t, gitDir, "locked")
	assertSign(t, checkedSigns(t, repo), "whose directory is gone")
	if strings.Contains(logged.String(), "pruned the records") {
		t.Fatalf("a locked record git kept was logged as pruned:\n%s", logged.String())
	}
}

func TestWorktreeSigns_ARelativeGitdirIsReadFromItsRecord(t *testing.T) {
	repo, dir, gitDir := openKeyed(t)
	wt := filepath.Join(t.TempDir(), "rel")
	gitCLI(t, dir, "worktree", "add", "-q", "--detach", wt)
	rec := filepath.Join(gitDir, "worktrees", "rel")
	rel, err := filepath.Rel(rec, filepath.Join(wt, ".git"))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(rec, "gitdir"), []byte(filepath.ToSlash(rel)+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	signs := checkedSigns(t, repo)
	assertSign(t, signs, "git lists a worktree at")
	for _, s := range signs {
		if strings.Contains(s, "gone") {
			t.Fatalf("a live worktree with a relative gitdir read as gone: %v", signs)
		}
	}
}

func TestWorktreeSigns_AFolderUnderTheVaultIsNamedThenClears(t *testing.T) {
	// The 2026-10-02 shape: a project folder's own `.claude/worktrees/`.
	repo, dir, _ := openKeyed(t)
	wt := filepath.Join(dir, "projects", "pixelton", ".claude", "worktrees", "laughing-bassi-06d858")
	if err := os.MkdirAll(wt, 0o755); err != nil {
		t.Fatal(err)
	}
	assertSign(t, checkedSigns(t, repo), "projects/pixelton/.claude/worktrees/ holds a worktree")
	if err := os.RemoveAll(wt); err != nil {
		t.Fatal(err)
	}
	if signs := checkedSigns(t, repo); len(signs) != 0 {
		t.Fatalf("after the folder emptied, still %v", signs)
	}
}

func TestWorktreeSigns_TheConfigKeyIsNamedWhileTheDaemonKeepsWorking(t *testing.T) {
	repo, dir, _ := openKeyed(t)
	gitCLI(t, dir, "config", "extensions.worktreeConfig", "true")
	// Reopened, the way a restart reads it: step 2's tolerance and step 4's
	// report together.
	repo = Open(dir)
	if !repo.Available() {
		t.Fatalf("Open: %s", repo.Status())
	}
	assertSign(t, checkedSigns(t, repo), "extensions.worktreeConfig=true")
	gitCLI(t, dir, "config", "--unset", "extensions.worktreeConfig")
	if signs := checkedSigns(t, repo); len(signs) != 0 {
		t.Fatalf("after the key was unset, still %v", signs)
	}
}

func TestWorktreeSigns_ASymlinkedVaultRootIsWalked(t *testing.T) {
	_, dir, _ := openKeyed(t)
	if err := os.MkdirAll(filepath.Join(dir, "projects", "pixelton", ".claude", "worktrees", "x"), 0o755); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(t.TempDir(), "vault-link")
	if err := os.Symlink(dir, link); err != nil {
		t.Skipf("no symlinks here: %v", err)
	}
	repo := Open(link)
	if !repo.Available() {
		t.Fatalf("Open: %s", repo.Status())
	}
	assertSign(t, checkedSigns(t, repo), "projects/pixelton/.claude/worktrees/ holds a worktree")
}

func TestWorktreeSigns_AFinderFileInTheEmptyFolderIsNotAWorktree(t *testing.T) {
	repo, dir, _ := openKeyed(t)
	mustWrite(t, filepath.Join(dir, ".claude", "worktrees", ".DS_Store"), "finder\n")
	if signs := checkedSigns(t, repo); len(signs) != 0 {
		t.Fatalf("a .DS_Store counted as a worktree: %v", signs)
	}
}
