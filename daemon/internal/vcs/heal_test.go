package vcs

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// Two stalls on 2026-10-04, pinned here as the shapes they took.
//
// The first: git's loose-objects maintenance had packed 6,716 objects into
// `objects/pack/loose-<hash>.pack`, and a later repack pruned their loose
// copies. CLI git reads any `*.pack`, so `git status` and `fsck` stayed clean.
// go-git v5.19.2 lists only files named `pack-*.pack`, so for the daemon those
// objects were gone: `Dirty()` failed with "object not found" every five
// minutes for eleven hours, and a restart changed nothing.
//
// The second: the restart that was meant to recover it killed a daemon while
// it held index.lock. The lock read `held by agentmd (pid 83986)`, that pid was
// dead, and the new daemon skipped every commit as "another git process holds
// index.lock" until the file was removed by hand.

// TestUnlistedPack_DirtyReadsObjectsOnlyInALoosePack is the first stall. HEAD's
// objects live only in a `loose-` pack; the daemon must still read the
// worktree's status.
func TestUnlistedPack_DirtyReadsObjectsOnlyInALoosePack(t *testing.T) {
	dir, gitDir := newCLIRepo(t)
	packIntoLoosePack(t, dir, gitDir)

	// The asymmetry that made this invisible: CLI git is entirely happy.
	if out := gitCLI(t, dir, "status", "--porcelain"); out != "" {
		t.Fatalf("CLI git sees changes in a clean fixture: %q", out)
	}

	r := Open(dir)
	if !r.Available() {
		t.Fatalf("repository not available: %s", r.Status())
	}
	dirty, err := r.Dirty()
	if err != nil {
		t.Fatalf("Dirty() failed on a repository CLI git reads cleanly: %v\n"+
			"  objects that live only in %v are invisible to the daemon", err, packNames(t, gitDir))
	}
	if len(dirty) != 0 {
		t.Fatalf("Dirty() = %v on a clean worktree", dirty)
	}
}

// TestUnlistedPack_CommitRecordsAChangeOverALoosePack is the same stall seen
// from the committer: a real edit has to reach history.
func TestUnlistedPack_CommitRecordsAChangeOverALoosePack(t *testing.T) {
	dir, gitDir := newCLIRepo(t)
	packIntoLoosePack(t, dir, gitDir)

	r := Open(dir)
	mustWrite(t, filepath.Join(dir, "personal", "kept.md"), note+"An edit.\n")
	hash, err := r.Commit(OriginLocal, []string{"personal/kept.md"})
	if err != nil {
		t.Fatalf("Commit failed over a loose pack: %v", err)
	}
	if hash == "" {
		t.Fatalf("a real edit was not committed over a loose pack")
	}
	if out := gitCLI(t, dir, "status", "--porcelain"); out != "" {
		t.Fatalf("the edit is still uncommitted after Commit: %q", out)
	}
}

// TestOwnStaleLock_CommitTakesBackALockADeadDaemonLeft is the second stall.
func TestOwnStaleLock_CommitTakesBackALockADeadDaemonLeft(t *testing.T) {
	r, dir := newRepo(t)
	r.lockWait = 200 * time.Millisecond
	lock := filepath.Join(r.gitDir, "index.lock")
	if err := os.WriteFile(lock, []byte(fmt.Sprintf("held by agentmd (pid %d)\n", deadPid(t))), 0o644); err != nil {
		t.Fatal(err)
	}

	mustWrite(t, filepath.Join(dir, "note.md"), "body\n")
	hash, err := r.Commit(OriginLocal, []string{"note.md"})
	if err != nil {
		t.Fatalf("Commit skipped on a lock its own dead predecessor left: %v", err)
	}
	if hash == "" {
		t.Fatalf("nothing was committed")
	}
	if _, err := os.Stat(lock); !os.IsNotExist(err) {
		t.Fatalf("index.lock survived the commit: %v", err)
	}
}

// ---------------------------------------------------------------------------

// isolateGit keeps every git process in the test — the CLI calls here and any
// the daemon makes — away from the machine's own global and system config,
// whose hooksPath and maintenance settings would otherwise leak in.
func isolateGit(t *testing.T) {
	t.Helper()
	t.Setenv("GIT_CONFIG_GLOBAL", os.DevNull)
	t.Setenv("GIT_CONFIG_NOSYSTEM", "1")
	t.Setenv("GIT_AUTHOR_NAME", "test")
	t.Setenv("GIT_AUTHOR_EMAIL", "t@example.com")
	t.Setenv("GIT_COMMITTER_NAME", "test")
	t.Setenv("GIT_COMMITTER_EMAIL", "t@example.com")
}

// newCLIRepo builds a repository with CLI git alone, so that no go-git process
// has cached anything about it. It has one committed note at personal/kept.md
// and a second at desk/other.md. Returns the worktree root and the git dir.
func newCLIRepo(t *testing.T) (string, string) {
	t.Helper()
	isolateGit(t)
	dir := t.TempDir()
	mustWrite(t, filepath.Join(dir, "personal", "kept.md"), note)
	mustWrite(t, filepath.Join(dir, "desk", "other.md"), "another note\n")
	gitCLI(t, dir, "init", "--initial-branch=main")
	gitCLI(t, dir, "add", ".")
	gitCLI(t, dir, "commit", "-m", "initial")
	return dir, filepath.Join(dir, ".git")
}

// packIntoLoosePack moves every object into a pack named the way git's
// loose-objects maintenance names one, and prunes the loose copies — the state
// the vault repository was in from 2026-10-04 00:31:55.
func packIntoLoosePack(t *testing.T, dir, gitDir string) {
	t.Helper()
	objects := gitCLI(t, dir, "rev-list", "--objects", "--all")
	cmd := exec.Command("git", "pack-objects", filepath.Join(gitDir, "objects", "pack", "loose"))
	cmd.Dir = dir
	cmd.Stdin = strings.NewReader(objects + "\n")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("git pack-objects: %v\n%s", err, out)
	}
	gitCLI(t, dir, "prune-packed")

	packs := packNames(t, gitDir)
	if len(packs) != 1 || !strings.HasPrefix(packs[0], "loose-") {
		t.Fatalf("fixture: want exactly one loose- pack, have %v", packs)
	}
	if loose := looseObjectCount(t, gitDir); loose != 0 {
		t.Fatalf("fixture: %d loose objects survived prune-packed", loose)
	}
}

func gitCLI(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return strings.TrimSpace(string(out))
}

func packNames(t *testing.T, gitDir string) []string {
	t.Helper()
	matches, err := filepath.Glob(filepath.Join(gitDir, "objects", "pack", "*.pack"))
	if err != nil {
		t.Fatal(err)
	}
	names := make([]string, 0, len(matches))
	for _, m := range matches {
		names = append(names, filepath.Base(m))
	}
	return names
}

func looseObjectCount(t *testing.T, gitDir string) int {
	t.Helper()
	matches, err := filepath.Glob(filepath.Join(gitDir, "objects", "??", "*"))
	if err != nil {
		t.Fatal(err)
	}
	return len(matches)
}

// deadPid returns the pid of a process that has already exited and been
// reaped, which is as close to "a daemon that was killed" as a test can get
// without killing one.
func deadPid(t *testing.T) int {
	t.Helper()
	cmd := exec.Command("true")
	if err := cmd.Run(); err != nil {
		t.Fatal(err)
	}
	return cmd.ProcessState.Pid()
}
