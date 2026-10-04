package vcs

import (
	"bytes"
	"fmt"
	"log/slog"
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

// TestUnlistedPack_TheRetryRecordsWhatTheFirstAttemptStaged: the first attempt
// can stage a confirmed deletion before it reaches the missing object. The
// retry starts from the index and quarantine the first attempt found, so the
// deletion is recorded once and named in the message, not swept in unnamed.
func TestUnlistedPack_TheRetryRecordsWhatTheFirstAttemptStaged(t *testing.T) {
	dir, gitDir := newCLIRepo(t)
	r := Open(dir)
	r.SetDeletionGrace(0)
	gone := "desk/other.md"
	mustRemove(t, dir, gone)
	if hash, err := r.Commit(OriginLocal, []string{gone}); err != nil || hash != "" {
		t.Fatalf("first sighting of the absence: hash %q, err %v", hash, err)
	}

	packIntoLoosePack(t, dir, gitDir)
	kept := "personal/kept.md"
	mustWrite(t, filepath.Join(dir, kept), note+"An edit.\n")
	hash, err := r.Commit(OriginLocal, []string{gone, kept})
	if err != nil || hash == "" {
		t.Fatalf("Commit over a loose pack: hash %q, err %v", hash, err)
	}
	if contains(headPaths(t, dir), gone) {
		t.Fatalf("%s is still in HEAD after its deletion was confirmed", gone)
	}
	msg := gitCLI(t, dir, "log", "-1", "--format=%B")
	for _, want := range []string{"2 files", "- " + gone, "- " + kept} {
		if !strings.Contains(msg, want) {
			t.Errorf("the commit message does not say %q:\n%s", want, msg)
		}
	}
}

// TestUnlistedPack_CommitSurvivesAGCInAnotherProcess is the other half of the
// library limit: go-git keeps the pack list it read first, so a `git gc` run by
// anything else hides the objects it moved from a daemon that is already
// running. The heal's reopen is what brings them back.
func TestUnlistedPack_CommitSurvivesAGCInAnotherProcess(t *testing.T) {
	dir, _ := newCLIRepo(t)
	// Packed from the start, so the daemon's first read loads a pack list.
	gitCLI(t, dir, "gc", "-q")

	r := Open(dir)
	logs := captureLog(r)
	rel := "personal/kept.md"
	mustWrite(t, filepath.Join(dir, rel), note+"First edit.\n")
	if hash, err := r.Commit(OriginLocal, []string{rel}); err != nil || hash == "" {
		t.Fatalf("first commit: hash %q, err %v", hash, err)
	}

	gitCLI(t, dir, "gc", "-q", "--prune=now")

	mustWrite(t, filepath.Join(dir, rel), note+"Second edit.\n")
	hash, err := r.Commit(OriginLocal, []string{rel})
	if err != nil {
		t.Fatalf("the commit after a gc in another process failed: %v\n%s", err, logs)
	}
	if hash == "" {
		t.Fatalf("the commit after a gc in another process recorded nothing")
	}
	if out := gitCLI(t, dir, "status", "--porcelain"); out != "" {
		t.Fatalf("the edit is still uncommitted: %q", out)
	}
	if got := gitCLI(t, dir, "rev-list", "--count", "HEAD"); got != "3" {
		t.Fatalf("history has %s commits, want 3", got)
	}
}

// TestRepairUnlistedPacks_KeepsEveryObject is the no-data-loss constraint.
// Unreachable objects are kept, whether they sat in the loose- pack or loose.
func TestRepairUnlistedPacks_KeepsEveryObject(t *testing.T) {
	dir, gitDir := newCLIRepo(t)
	orphanPacked := hashObject(t, dir, "an orphan that only the loose- pack holds\n")
	objects := gitCLI(t, dir, "rev-list", "--objects", "--all") + "\n" + orphanPacked + "\n"
	cmd := exec.Command("git", "pack-objects", "-q", filepath.Join(gitDir, "objects", "pack", "loose"))
	cmd.Dir = dir
	cmd.Stdin = strings.NewReader(objects)
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("git pack-objects: %v\n%s", err, out)
	}
	gitCLI(t, dir, "prune-packed")
	orphanLoose := hashObject(t, dir, "an orphan that stays loose\n")

	before := allObjects(t, dir)
	for _, h := range []string{orphanPacked, orphanLoose} {
		if !contains(before, h) {
			t.Fatalf("fixture: %s is not in the object database", h)
		}
	}

	r := Open(dir)
	logs := captureLog(r)
	repaired, err := r.RepairUnlistedPacks()
	if err != nil || !repaired {
		t.Fatalf("RepairUnlistedPacks = %v, %v\n%s", repaired, err, logs)
	}

	after := allObjects(t, dir)
	if len(after) < len(before) {
		t.Fatalf("the repair lost objects: %d before, %d after", len(before), len(after))
	}
	for _, h := range before {
		if !contains(after, h) {
			t.Errorf("object %s did not survive the repair", h)
		}
	}
	for _, name := range packNames(t, gitDir) {
		if !strings.HasPrefix(name, "pack-") {
			t.Errorf("a pack go-git can't list survived the repair: %s", name)
		}
	}
	for _, want := range []string{
		`msg="repaired unlisted packs"`,
		fmt.Sprintf("objects_before=%d", len(before)),
		fmt.Sprintf("objects_after=%d", len(after)),
	} {
		if !strings.Contains(logs.String(), want) {
			t.Errorf("the repair log does not say %s:\n%s", want, logs)
		}
	}
	if _, err := r.Dirty(); err != nil {
		t.Fatalf("Dirty after the repair: %v", err)
	}
	if again, err := r.RepairUnlistedPacks(); again || err != nil {
		t.Fatalf("a second repair ran (%v, %v) with nothing left to repair", again, err)
	}
}

// TestUnlistedPack_WithoutGitItLogsOnceAndDoesNotLoop: the heal needs CLI git.
// Without it the daemon says so, leaves the packs alone, and doesn't try the
// repack again on every cycle.
func TestUnlistedPack_WithoutGitItLogsOnceAndDoesNotLoop(t *testing.T) {
	dir, gitDir := newCLIRepo(t)
	packIntoLoosePack(t, dir, gitDir)
	packs := packNames(t, gitDir)

	r := Open(dir)
	logs := captureLog(r)
	t.Setenv("PATH", t.TempDir())

	for i := 0; i < 3; i++ {
		if _, err := r.Dirty(); !missingObject(err) {
			t.Fatalf("Dirty #%d without git: err = %v, want the missing object", i+1, err)
		}
	}
	if n := strings.Count(logs.String(), "could not repack unlisted packs"); n != 1 {
		t.Fatalf("the failed repack was reported %d times over three cycles, want once:\n%s", n, logs)
	}
	if got := packNames(t, gitDir); len(got) != len(packs) || got[0] != packs[0] {
		t.Fatalf("the packs changed without git: %v -> %v", packs, got)
	}
}

// TestOwnStaleLock_OnlyADeadDaemonsLockIsRemoved: every other lock is still
// waited out and then refused, and still there afterwards.
func TestOwnStaleLock_OnlyADeadDaemonsLockIsRemoved(t *testing.T) {
	r, dir := newRepo(t)
	r.lockWait = 150 * time.Millisecond

	live := exec.Command("git", "cat-file", "--batch")
	live.Dir = dir
	stdin, err := live.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := live.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { stdin.Close(); live.Wait() }()

	dead := deadPid(t)
	for _, tc := range []struct{ name, body string }{
		{"a live agentmd", fmt.Sprintf("held by agentmd (pid %d)\n", live.Process.Pid)},
		{"this process", fmt.Sprintf("held by agentmd (pid %d)\n", os.Getpid())},
		{"another client", "held by test\n"},
		{"another client naming a dead pid", fmt.Sprintf("held by sync-tool (pid %d)\n", dead)},
		{"a near miss naming a dead pid", fmt.Sprintf("held by agentmd (pid %d) since boot\n", dead)},
		{"git's own index data", "DIRC\x00\x00\x00\x02"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			lock := filepath.Join(r.gitDir, "index.lock")
			if err := os.WriteFile(lock, []byte(tc.body), 0o644); err != nil {
				t.Fatal(err)
			}
			defer os.Remove(lock)

			mustWrite(t, filepath.Join(dir, "note.md"), tc.name+"\n")
			_, err := r.Commit(OriginLocal, []string{"note.md"})
			if err == nil || !strings.Contains(err.Error(), "Not stealing") {
				t.Fatalf("Commit under %s's lock: err = %v, want a refusal", tc.name, err)
			}
			if got, err := os.ReadFile(lock); err != nil || string(got) != tc.body {
				t.Fatalf("%s's lock was disturbed: %q, %v", tc.name, got, err)
			}
		})
	}
}

// TestOwnStaleLock_TheDaemonsLockCarriesTheContentItReclaims pins the content
// shape. Reclaiming keys on it, so a change here has to be a deliberate one:
// a daemon that wrote anything else would leave locks its successor can't take
// back.
func TestOwnStaleLock_TheDaemonsLockCarriesTheContentItReclaims(t *testing.T) {
	r, _ := newRepo(t)
	unlock, err := r.lockIndex()
	if err != nil {
		t.Fatal(err)
	}
	lock := filepath.Join(r.gitDir, "index.lock")
	raw, err := os.ReadFile(lock)
	unlock()
	if err != nil {
		t.Fatal(err)
	}
	if want := fmt.Sprintf("held by agentmd (pid %d)\n", os.Getpid()); string(raw) != want {
		t.Fatalf("index.lock reads %q, want %q", raw, want)
	}
	dead := deadPid(t)
	stale := strings.Replace(string(raw), fmt.Sprint(os.Getpid()), fmt.Sprint(dead), 1)
	if err := os.WriteFile(lock, []byte(stale), 0o644); err != nil {
		t.Fatal(err)
	}
	defer os.Remove(lock)
	if pid, ok := ownStaleLockPid(lock); !ok || pid != dead {
		t.Fatalf("ownStaleLockPid on the daemon's own content with a dead pid = %d, %v", pid, ok)
	}
}

// ---------------------------------------------------------------------------

// isolateGit keeps every git process in the test — the CLI calls here and any
// the daemon makes — away from the machine's own global and system config,
// whose hooksPath and maintenance settings would otherwise leak in.
func isolateGit(t *testing.T) {
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
	cmd := exec.Command("git", "--version")
	if err := cmd.Run(); err != nil {
		t.Fatal(err)
	}
	return cmd.ProcessState.Pid()
}

func hashObject(t *testing.T, dir, body string) string {
	t.Helper()
	cmd := exec.Command("git", "hash-object", "-w", "--stdin")
	cmd.Dir = dir
	cmd.Stdin = strings.NewReader(body)
	out, err := cmd.Output()
	if err != nil {
		t.Fatalf("git hash-object: %v", err)
	}
	return strings.TrimSpace(string(out))
}

// allObjects lists every distinct object CLI git can read, reachable or not.
func allObjects(t *testing.T, dir string) []string {
	t.Helper()
	return strings.Fields(gitCLI(t, dir, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)"))
}

// captureLog points the repository's logger at a buffer the test can read.
func captureLog(r *Repo) *bytes.Buffer {
	var buf bytes.Buffer
	r.SetLogger(slog.New(slog.NewTextHandler(&buf, &slog.HandlerOptions{Level: slog.LevelDebug})))
	return &buf
}
