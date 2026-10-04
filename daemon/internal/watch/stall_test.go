package watch

import (
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/vcs"
)

// On 2026-10-04 every commit cycle failed for eleven hours and the only trace
// was a warning in the log every five minutes. The run of failures is now
// counted, so health can page on it. These pin what counts as a failed cycle
// and what ends the run.

func TestCommitStall_ACleanWorktreeIsNotAFailure(t *testing.T) {
	w, _ := newGitWatcher(t)
	w.commitDirty()
	w.commitDirty()
	if st := w.CommitStall(); st.Failures != 0 || !st.FirstAt.IsZero() {
		t.Fatalf("a clean worktree counted as a failed commit cycle: %+v", st)
	}
}

func TestCommitStall_CountsFromTheFirstFailureAndResetsOnACommit(t *testing.T) {
	w, dir := newGitWatcher(t)
	clock := time.Date(2026, 10, 4, 0, 32, 14, 0, time.UTC)
	w.now = func() time.Time { return clock }

	// HEAD's tree goes missing for every git client, which is what the daemon
	// saw on 2026-10-04 even though the objects were there for the CLI.
	tree := gitOut(t, dir, "rev-parse", "HEAD^{tree}")
	obj := filepath.Join(dir, ".git", "objects", tree[:2], tree[2:])
	saved, err := os.ReadFile(obj)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(obj, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(obj); err != nil {
		t.Fatal(err)
	}

	w.commitDirty()
	first := w.CommitStall()
	if first.Failures != 1 || !first.FirstAt.Equal(clock) ||
		!strings.Contains(first.LastError, "object not found") {
		t.Fatalf("after one failed cycle: %+v", first)
	}

	clock = clock.Add(5 * time.Minute)
	w.commitDirty()
	if st := w.CommitStall(); st.Failures != 2 || !st.FirstAt.Equal(first.FirstAt) {
		t.Fatalf("the second failure restarted the run instead of extending it: %+v", st)
	}

	if err := os.WriteFile(obj, saved, 0o444); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "note.md"), []byte("edited\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	w.commitDirty()
	if st := w.CommitStall(); st.Failures != 0 || !st.FirstAt.IsZero() || st.LastError != "" {
		t.Fatalf("a successful commit did not end the run: %+v", st)
	}
	if got := gitOut(t, dir, "rev-list", "--count", "HEAD"); got != "2" {
		t.Fatalf("the edit was not committed: %s commits", got)
	}
}

func TestCommitStall_NoRepositoryIsNotAStall(t *testing.T) {
	dir := t.TempDir()
	quiet := slog.New(slog.NewTextHandler(io.Discard, nil))
	repo := vcs.Open(dir)
	repo.SetLogger(quiet)
	w := New(&config.Config{VaultPath: dir}, nil, repo, quiet)
	w.commitDirty()
	if st := w.CommitStall(); st.Failures != 0 {
		t.Fatalf("a vault with no repository counted as a commit stall: %+v", st)
	}
}

// ---------------------------------------------------------------------------

// newGitWatcher is a watcher over a real repository with one committed note,
// with every git process kept away from the machine's own config.
func newGitWatcher(t *testing.T) (*Watcher, string) {
	t.Helper()
	global := filepath.Join(t.TempDir(), "gitconfig")
	if err := os.WriteFile(global, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GIT_CONFIG_GLOBAL", global)
	t.Setenv("GIT_CONFIG_NOSYSTEM", "1")
	t.Setenv("GIT_AUTHOR_NAME", "test")
	t.Setenv("GIT_AUTHOR_EMAIL", "t@example.com")
	t.Setenv("GIT_COMMITTER_NAME", "test")
	t.Setenv("GIT_COMMITTER_EMAIL", "t@example.com")

	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "note.md"), []byte("a note\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	gitOut(t, dir, "init", "--initial-branch=main")
	gitOut(t, dir, "add", ".")
	gitOut(t, dir, "commit", "-m", "initial")

	quiet := slog.New(slog.NewTextHandler(io.Discard, nil))
	repo := vcs.Open(dir)
	if !repo.Available() {
		t.Fatalf("repository not available: %s", repo.Status())
	}
	repo.SetLogger(quiet)
	return New(&config.Config{VaultPath: dir}, nil, repo, quiet), dir
}

func gitOut(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return strings.TrimSpace(string(out))
}
