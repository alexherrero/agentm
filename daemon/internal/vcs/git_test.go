package vcs

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// These tests exist because of a specific afternoon. On 2026-08-10, immediately
// after the git-transport cutover, the daemon recorded deletions for two notes
// that were on disk the entire time: Google Drive had made them briefly
// unstattable at their own paths while it replaced them, and a failed os.Stat
// was taken as proof the file was gone. Nothing was lost from the working tree,
// but the history — and the NAS backup pushed from it — described a vault
// missing notes that existed.
//
// The same cutover surfaced the second defect: the commit path had no equivalent
// of the indexer's dot-directory skip, so it batched over 1,400 transient
// `.tmp.driveupload` files and failed on every batch with "cannot create empty
// commit: clean working tree".
//
// Both are cheap to re-introduce and expensive to notice, so both are pinned here.

const note = `---
type: convention
status: active
---
A note that exists.
`

func TestCommit_FirstSightingOfAnAbsenceRecordsNothing(t *testing.T) {
	r, dir := newRepo(t)
	r.SetDeletionGrace(0) // the most aggressive setting there is

	rel := "personal/kept.md"
	mustRemove(t, dir, rel)

	hash, err := r.Commit(OriginLocal, []string{rel})
	if err != nil {
		t.Fatalf("Commit: %v", err)
	}
	if hash != "" {
		t.Fatalf("the first time a path was seen missing it was committed as a deletion (%s).\n"+
			"  an absence has to be confirmed; a cloud-sync mount makes a file "+
			"unstattable at its own path while it replaces it", hash[:8])
	}
	if got := headPaths(t, dir); !contains(got, rel) {
		t.Errorf("%s left HEAD on a single unconfirmed stat failure.\n  HEAD: %v", rel, got)
	}
}

// TestCommit_APathThatVanishesAndReturnsIsNotADeletion is the incident itself,
// as a test: the file is missing when the watcher looks and back before anything
// is confirmed.
func TestCommit_APathThatVanishesAndReturnsIsNotADeletion(t *testing.T) {
	r, dir := newRepo(t)
	r.SetDeletionGrace(0)

	rel := "personal/kept.md"
	abs := filepath.Join(dir, filepath.FromSlash(rel))
	body := mustRead(t, abs)

	// Drive moves the file out of the way...
	mustRemove(t, dir, rel)
	if _, err := r.Commit(OriginLocal, []string{rel}); err != nil {
		t.Fatalf("Commit: %v", err)
	}
	// ...and puts it back, unchanged, before the sweep looks again.
	mustWrite(t, abs, body)

	commits, err := r.SweepDeletions()
	if err != nil {
		t.Fatalf("SweepDeletions: %v", err)
	}
	if len(commits) != 0 {
		t.Fatalf("a path that vanished and returned within one cycle produced a "+
			"deletion commit: %+v", commits)
	}
	if got := headPaths(t, dir); !contains(got, rel) {
		t.Errorf("%s was deleted from history after a round trip through the sync "+
			"client.\n  HEAD: %v", rel, got)
	}
	if _, err := os.Stat(abs); err != nil {
		t.Errorf("%s is no longer on disk: %v", rel, err)
	}
	if n := len(r.pendingDeletes); n != 0 {
		t.Errorf("the quarantine still holds %d entry/entries for a path that came back", n)
	}
}

func TestSweepDeletions_HoldsAnAbsenceInsideTheGraceWindow(t *testing.T) {
	r, dir := newRepo(t)
	r.SetDeletionGrace(time.Hour)

	rel := "personal/kept.md"
	mustRemove(t, dir, rel)
	if _, err := r.Commit(OriginLocal, []string{rel}); err != nil {
		t.Fatalf("Commit: %v", err)
	}

	commits, err := r.SweepDeletions()
	if err != nil {
		t.Fatalf("SweepDeletions: %v", err)
	}
	if len(commits) != 0 {
		t.Fatalf("an absence was recorded before its grace window elapsed: %+v", commits)
	}
	if got := headPaths(t, dir); !contains(got, rel) {
		t.Errorf("%s left HEAD inside the grace window.\n  HEAD: %v", rel, got)
	}
}

// TestSweepDeletions_RecordsAnAbsenceThatPersists is the other side of the
// asymmetry. Quarantining deletions is only correct if real ones still land —
// the reconcile pass offers a vanished path exactly once, so without the sweep
// this fix would lose deletions rather than delay them.
func TestSweepDeletions_RecordsAnAbsenceThatPersists(t *testing.T) {
	r, dir := newRepo(t)
	r.SetDeletionGrace(0)

	rel := "personal/kept.md"
	mustRemove(t, dir, rel)
	if _, err := r.Commit(OriginPhone, []string{rel}); err != nil {
		t.Fatalf("Commit: %v", err)
	}

	commits, err := r.SweepDeletions()
	if err != nil {
		t.Fatalf("SweepDeletions: %v", err)
	}
	if len(commits) != 1 {
		t.Fatalf("a genuine deletion was never recorded; got %d commits: %+v",
			len(commits), commits)
	}
	if commits[0].Origin != OriginPhone {
		t.Errorf("the deletion was attributed to %q, not to what caused it (%q)",
			commits[0].Origin, OriginPhone)
	}
	if got := headPaths(t, dir); contains(got, rel) {
		t.Errorf("%s is still in HEAD after a confirmed deletion.\n  HEAD: %v", rel, got)
	}
}

// TestCommit_AnUnreadablePathIsNotADeletion separates "gone" from "could not
// tell". Only ENOENT is evidence of absence; every other stat error means the
// question was not answered, and unknown must never be recorded as gone.
func TestCommit_AnUnreadablePathIsNotADeletion(t *testing.T) {
	r, dir := newRepo(t)
	r.SetDeletionGrace(0)

	// Replace the containing directory with a regular file, so stat of a path
	// inside it fails with ENOTDIR rather than ENOENT.
	rel := "personal/kept.md"
	if err := os.RemoveAll(filepath.Join(dir, "personal")); err != nil {
		t.Fatal(err)
	}
	mustWrite(t, filepath.Join(dir, "personal"), "not a directory\n")

	if _, err := r.Commit(OriginLocal, []string{rel}); err == nil {
		t.Error("a stat error that was not ENOENT was reported as an ordinary no-op; " +
			"it should be surfaced as a problem")
	}
	if n := len(r.pendingDeletes); n != 0 {
		t.Errorf("an unreadable path was quarantined as a candidate deletion (%d entries); "+
			"only ENOENT is evidence of absence", n)
	}
	if got := headPaths(t, dir); !contains(got, rel) {
		t.Errorf("%s left HEAD on a stat error that was not ENOENT.\n  HEAD: %v", rel, got)
	}
}

// TestStageDeletion_LeavesTheWorkingTreeAlone pins the reason this code edits the
// index directly. go-git's Worktree.Remove deletes the file from disk as well,
// which turns a misread of a transient absence from a wrong history into a lost
// note.
func TestStageDeletion_LeavesTheWorkingTreeAlone(t *testing.T) {
	r, dir := newRepo(t)

	rel := "personal/kept.md"
	abs := filepath.Join(dir, filepath.FromSlash(rel))

	r.mu.Lock()
	ok := r.stageDeletion(rel)
	r.mu.Unlock()
	if !ok {
		t.Fatal("staging the removal of a tracked path reported nothing to remove")
	}
	if _, err := os.Stat(abs); err != nil {
		t.Fatalf("staging a deletion removed the file from the working tree: %v\n"+
			"  a misread absence must cost history, which is recoverable, and not "+
			"the operator's note, which is not", err)
	}
}

// TestCommit_UntrackedChurnDoesNotForceAnEmptyCommit is the second cutover
// defect. Status().IsClean() counts untracked files, which do not affect the
// tree, so Drive's staging directory made every batch of unchanged notes look
// committable — and go-git rejected each one with "cannot create empty commit".
func TestCommit_UntrackedChurnDoesNotForceAnEmptyCommit(t *testing.T) {
	r, dir := newRepo(t)

	// Drive's staging directory, mid-upload.
	churn := filepath.Join(dir, ".tmp.driveupload")
	if err := os.MkdirAll(churn, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"3781.md", "3782.md", "3783.md"} {
		mustWrite(t, filepath.Join(churn, name), "in flight\n")
	}

	// The batch names a tracked note whose content did not change.
	rel := "personal/kept.md"
	hash, err := r.Commit(OriginLocal, []string{rel})
	if err != nil {
		t.Fatalf("a batch that changed nothing returned an error: %v\n"+
			"  untracked files do not change the tree; only the staged paths decide "+
			"whether there is a commit to make", err)
	}
	if hash != "" {
		t.Errorf("a batch that changed nothing produced commit %s", hash[:8])
	}
	if n := countCommits(t, dir); n != 1 {
		t.Errorf("history grew to %d commits with nothing to record", n)
	}
}

func TestCommit_AChangedFileStillCommits(t *testing.T) {
	r, dir := newRepo(t)

	rel := "personal/kept.md"
	mustWrite(t, filepath.Join(dir, filepath.FromSlash(rel)), note+"\nAnd an edit.\n")

	hash, err := r.Commit(OriginLocal, []string{rel})
	if err != nil {
		t.Fatalf("Commit: %v", err)
	}
	if hash == "" {
		t.Fatal("a real edit was not committed; the guard against empty commits is " +
			"swallowing real ones")
	}
	if n := countCommits(t, dir); n != 2 {
		t.Errorf("expected the edit to add one commit, history has %d", n)
	}
}

// ---------------------------------------------------------------------------

// newRepo builds a throwaway vault that is a real git repository with one
// tracked, committed note at personal/kept.md.
func newRepo(t *testing.T) (*Repo, string) {
	t.Helper()
	dir := t.TempDir()
	mustWrite(t, filepath.Join(dir, "personal", "kept.md"), note)

	for _, args := range [][]string{
		{"init", "--initial-branch=main"},
		{"config", "user.email", "test@example.com"},
		{"config", "user.name", "test"},
		{"add", "."},
		{"commit", "-m", "initial"},
	} {
		cmd := exec.Command("git", args...)
		cmd.Dir = dir
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v\n%s", args, err, out)
		}
	}

	r := Open(dir)
	if !r.Available() {
		t.Fatalf("repository not available: %s", r.Status())
	}
	return r, dir
}

func mustWrite(t *testing.T, abs, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(abs, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func mustRead(t *testing.T, abs string) string {
	t.Helper()
	raw, err := os.ReadFile(abs)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func mustRemove(t *testing.T, dir, rel string) {
	t.Helper()
	if err := os.Remove(filepath.Join(dir, filepath.FromSlash(rel))); err != nil {
		t.Fatal(err)
	}
}

// headPaths is what the recorded history says the vault contains — which is the
// thing the incident got wrong, and so the thing worth asserting on.
func headPaths(t *testing.T, dir string) []string {
	t.Helper()
	cmd := exec.Command("git", "ls-tree", "-r", "--name-only", "HEAD")
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git ls-tree: %v\n%s", err, out)
	}
	var paths []string
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if line != "" {
			paths = append(paths, line)
		}
	}
	return paths
}

func countCommits(t *testing.T, dir string) int {
	t.Helper()
	cmd := exec.Command("git", "rev-list", "--count", "HEAD")
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git rev-list: %v\n%s", err, out)
	}
	n := 0
	for _, c := range strings.TrimSpace(string(out)) {
		n = n*10 + int(c-'0')
	}
	return n
}

func contains(haystack []string, needle string) bool {
	for _, s := range haystack {
		if s == needle {
			return true
		}
	}
	return false
}
