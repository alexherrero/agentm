package vcs

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// The clobber this file guards against: the daemon read the index, a CLI
// `git rm` staged a deletion, the daemon wrote an index built from its stale
// read, and the staging vanished. Twice, live, on 2026-08-11. Every
// expectation below is written from what git's own protocol requires, not
// from what the implementation happens to do.

func TestResolveGitDir_Directory(t *testing.T) {
	_, dir := newRepo(t)
	got, err := resolveGitDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if got != filepath.Join(dir, ".git") {
		t.Fatalf("resolveGitDir = %q, want %q", got, filepath.Join(dir, ".git"))
	}
}

func TestResolveGitDir_PointerFile(t *testing.T) {
	// The vault's real shape: `git init --separate-git-dir` leaves a one-line
	// pointer file so the object database stays out of the Drive sync set.
	root := t.TempDir()
	gitDir := filepath.Join(t.TempDir(), "vault.git")
	cmd := exec.Command("git", "init", "--initial-branch=main",
		"--separate-git-dir="+gitDir, root)
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("git init --separate-git-dir: %v\n%s", err, out)
	}
	got, err := resolveGitDir(root)
	if err != nil {
		t.Fatal(err)
	}
	// macOS TempDir sits behind the /var -> /private/var symlink; git writes
	// the resolved form into the pointer, so compare resolved to resolved.
	wantResolved, _ := filepath.EvalSymlinks(gitDir)
	gotResolved, _ := filepath.EvalSymlinks(got)
	if gotResolved != wantResolved {
		t.Fatalf("resolveGitDir = %q, want %q", gotResolved, wantResolved)
	}
	// And the full open wires the resolved dir into the lock machinery.
	r := Open(root)
	if !r.Available() {
		t.Fatalf("repo not available: %s", r.Status())
	}
	rResolved, _ := filepath.EvalSymlinks(r.gitDir)
	if rResolved != wantResolved {
		t.Fatalf("Open kept gitDir %q, want %q", rResolved, wantResolved)
	}
}

func TestCommit_WaitsForAForeignIndexLock(t *testing.T) {
	r, dir := newRepo(t)
	lock := filepath.Join(r.gitDir, "index.lock")
	if err := os.WriteFile(lock, []byte("held by test\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	mustWrite(t, filepath.Join(dir, "note.md"), "body\n")
	done := make(chan struct{})
	var hash string
	var commitErr error
	go func() {
		hash, commitErr = r.Commit(OriginLocal, []string{"note.md"})
		close(done)
	}()

	// While the lock is held, the commit must not complete.
	select {
	case <-done:
		t.Fatalf("commit completed while another process held index.lock")
	case <-time.After(400 * time.Millisecond):
	}

	if err := os.Remove(lock); err != nil {
		t.Fatal(err)
	}
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatalf("commit never completed after the lock was released")
	}
	if commitErr != nil {
		t.Fatalf("commit after lock release: %v", commitErr)
	}
	if hash == "" {
		t.Fatalf("nothing was committed after the lock was released")
	}
}

func TestCommit_BoundedWaitRefusesLoudly(t *testing.T) {
	r, dir := newRepo(t)
	r.lockWait = 200 * time.Millisecond
	lock := filepath.Join(r.gitDir, "index.lock")
	if err := os.WriteFile(lock, []byte("held forever\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	defer os.Remove(lock)

	mustWrite(t, filepath.Join(dir, "note.md"), "body\n")
	_, err := r.Commit(OriginLocal, []string{"note.md"})
	if err == nil {
		t.Fatalf("commit succeeded under a lock that never cleared")
	}
	for _, want := range []string{"index.lock", "Not stealing"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error %q does not mention %q", err.Error(), want)
		}
	}
	// The foreign lock must still be there — refusing means not stealing.
	if _, statErr := os.Stat(lock); statErr != nil {
		t.Fatalf("the foreign index.lock was removed: %v", statErr)
	}
}

func TestCommit_ReleasesTheLockAndLeavesNoTempFiles(t *testing.T) {
	r, dir := newRepo(t)
	mustWrite(t, filepath.Join(dir, "note.md"), "body\n")
	if _, err := r.Commit(OriginLocal, []string{"note.md"}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(r.gitDir, "index.lock")); !os.IsNotExist(err) {
		t.Fatalf("index.lock survived the commit: %v", err)
	}
	leftovers, _ := filepath.Glob(filepath.Join(r.gitDir, "index.agentmd-*"))
	if len(leftovers) > 0 {
		t.Fatalf("temp index files survived: %v", leftovers)
	}
}

func TestCommit_PreservesCLIStagingItDidNotMake(t *testing.T) {
	// The regression proper. A deletion staged by the real git CLI before the
	// daemon's cycle starts must survive into history, not be overwritten by
	// an index write built from a stale read. With the lock held across the
	// whole read-modify-write this holds by construction; without it, it held
	// only when the interleaving was lucky.
	r, dir := newRepo(t)
	mustWrite(t, filepath.Join(dir, "doomed.md"), "tracked then removed\n")
	for _, args := range [][]string{
		{"add", "doomed.md"},
		{"commit", "-m", "track doomed"},
		{"rm", "doomed.md"},
	} {
		cmd := exec.Command("git", args...)
		cmd.Dir = dir
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v\n%s", args, err, out)
		}
	}

	mustWrite(t, filepath.Join(dir, "other.md"), "the daemon's own batch\n")
	if _, err := r.Commit(OriginLocal, []string{"other.md"}); err != nil {
		t.Fatal(err)
	}

	// After the daemon's commit, the CLI's staged deletion must not have been
	// resurrected: doomed.md is gone from HEAD or still staged for deletion —
	// never quietly back in the tree with the staging lost.
	cmd := exec.Command("git", "status", "--porcelain", "--", "doomed.md")
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatal(err)
	}
	status := strings.TrimSpace(string(out))
	if status != "" && !strings.HasPrefix(status, "D ") {
		t.Fatalf("the CLI's staged deletion was clobbered; git status now says %q", status)
	}
}
