package dreaming

import (
	"encoding/base64"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

// ── a move keeps the note's identity ────────────────────────────────────────
//
// Google Drive for Desktop mirrors the vault. A move made as a copy at the new
// path and a delete at the old one reaches Drive as a new file and a trashed
// one, and every device that syncs from Drive downloads the note again. A
// rename reaches it as a move. These tests hold the journal to the rename.

// pinned stats path and pins its file identity. On Windows os.SameFile loads a
// file's id lazily from the path it was stated by, and a rename takes that
// path away, so the id is loaded now, while the path still names the file.
func pinned(t *testing.T, path string) os.FileInfo {
	t.Helper()
	fi, err := os.Stat(path)
	if err != nil {
		t.Fatalf("stat %s: %v", path, err)
	}
	os.SameFile(fi, fi)
	return fi
}

// moveFixture is a vault with one note at src, a journal, and the path the
// note moves to.
func moveFixture(t *testing.T) (vault string, j *Journal, src, dst string, before []byte) {
	t.Helper()
	vault = t.TempDir()
	j, err := OpenJournal(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	src = filepath.Join(vault, "projects", "p", "tasks", "042-x", "plan.md")
	dst = filepath.Join(vault, "projects", "p", "completed", "tasks", "042-x", "plan.md")
	before = []byte("---\ntitle: x\n---\nSee [[progress]].\n")
	if err := os.MkdirAll(filepath.Dir(src), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(src, before, 0o644); err != nil {
		t.Fatal(err)
	}
	return vault, j, src, dst, before
}

func rel(t *testing.T, vault, p string) string {
	t.Helper()
	r, err := filepath.Rel(vault, p)
	if err != nil {
		t.Fatal(err)
	}
	return filepath.ToSlash(r)
}

func lastEntry(t *testing.T, j *Journal) Entry {
	t.Helper()
	entries, err := j.Read()
	if err != nil || len(entries) == 0 {
		t.Fatalf("journal read: %d entries, err %v", len(entries), err)
	}
	return entries[len(entries)-1]
}

func readOr(p string) string {
	b, err := os.ReadFile(p)
	if err != nil {
		return "<missing>"
	}
	return string(b)
}

func TestAMoveKeepsTheNotesIdentity(t *testing.T) {
	vault, j, src, dst, before := moveFixture(t)
	was := pinned(t, src)
	in := Intent{Job: JobTasks, Rel: rel(t, vault, src), To: rel(t, vault, dst), Before: before, After: before}
	kind, err := j.Commit(vault, "r", "r-1", in, time.Now().UTC())
	if err != nil || kind != KindApplied {
		t.Fatalf("commit: kind=%s err=%v", kind, err)
	}
	if _, err := os.Stat(src); !os.IsNotExist(err) {
		t.Errorf("the source is gone after the move: %v", err)
	}
	now, err := os.Stat(dst)
	if err != nil {
		t.Fatalf("the note is at its new path: %v", err)
	}
	if !os.SameFile(was, now) {
		t.Errorf("a move is a rename: the note at its new path is the same file it was")
	}
	if got := readOr(dst); got != string(before) {
		t.Errorf("an unchanged move keeps its bytes: %q", got)
	}
}

func TestAMoveThatRepairsLinksLandsTheNewBytes(t *testing.T) {
	vault, j, src, dst, before := moveFixture(t)
	after := []byte("---\ntitle: x\n---\nSee [[completed/tasks/042-x/progress]].\n")
	var renamed []string
	j.rename = func(o, n string) error {
		renamed = append(renamed, o+" -> "+n)
		return os.Rename(o, n)
	}
	in := Intent{Job: JobTasks, Rel: rel(t, vault, src), To: rel(t, vault, dst), Before: before, After: after}
	kind, err := j.Commit(vault, "r", "r-1", in, time.Now().UTC())
	if err != nil || kind != KindApplied {
		t.Fatalf("commit: kind=%s err=%v", kind, err)
	}
	if len(renamed) != 1 || renamed[0] != src+" -> "+dst {
		t.Errorf("a move that rewrites the note renames it first, then rewrites it in place: %v", renamed)
	}
	if got := readOr(dst); got != string(after) {
		t.Errorf("the moved note carries its repaired links: %q", got)
	}
	if _, err := os.Stat(src); !os.IsNotExist(err) {
		t.Errorf("the source is gone after the move: %v", err)
	}
	if e := lastEntry(t, j); e.Kind != KindApplied || e.Note != "" {
		t.Errorf("a plain move is applied with no note: %+v", e)
	}
}

// moveIntent is the journal line a crashed move left: intent, no applied.
func moveIntent(vault, src, dst string, before, after []byte) Entry {
	r := func(p string) string { s, _ := filepath.Rel(vault, p); return filepath.ToSlash(s) }
	return Entry{Kind: KindIntent, RunID: "r", ID: "r-1", Job: JobTasks, Rel: r(src), To: r(dst),
		BeforeHash: Hash(before), AfterHash: Hash(after), After: base64.StdEncoding.EncodeToString(after)}
}

func TestResolveFinishesAMoveRenamedButNotRewritten(t *testing.T) {
	vault, j, src, dst, before := moveFixture(t)
	after := []byte("---\ntitle: x\n---\nrepaired\n")
	// The crash fell after the rename and before the rewrite.
	os.MkdirAll(filepath.Dir(dst), 0o755)
	if err := os.Rename(src, dst); err != nil {
		t.Fatal(err)
	}
	kind, err := j.Resolve(vault, moveIntent(vault, src, dst, before, after), time.Now().UTC())
	if err != nil || kind != KindApplied {
		t.Fatalf("a renamed, unrewritten move is finished on resume: kind=%s err=%v", kind, err)
	}
	if got := readOr(dst); got != string(after) {
		t.Errorf("resume writes the journaled content at the new path: %q", got)
	}
	if e := lastEntry(t, j); e.Note != "finished on resume: the note was renamed, not yet rewritten" {
		t.Errorf("the applied line names what resume finished: %q", e.Note)
	}
}

func TestResolveAppliesAPendingMoveByRenaming(t *testing.T) {
	vault, j, src, dst, before := moveFixture(t)
	was := pinned(t, src)
	kind, err := j.Resolve(vault, moveIntent(vault, src, dst, before, before), time.Now().UTC())
	if err != nil || kind != KindApplied {
		t.Fatalf("a move that never started is applied on resume: kind=%s err=%v", kind, err)
	}
	now, err := os.Stat(dst)
	if err != nil {
		t.Fatalf("the note is at its new path: %v", err)
	}
	if !os.SameFile(was, now) {
		t.Errorf("resume moves the note by renaming it too")
	}
	if _, err := os.Stat(src); !os.IsNotExist(err) {
		t.Errorf("the source is gone after the move: %v", err)
	}
	if e := lastEntry(t, j); e.Note != "applied on resume" {
		t.Errorf("note = %q", e.Note)
	}
}

func TestResolveFinishesAMoveJournaledAsACopy(t *testing.T) {
	vault, j, src, dst, before := moveFixture(t)
	after := []byte("---\ntitle: x\n---\nrepaired\n")
	// A journal written before moves were renames: the copy is at the
	// destination and the source is not yet removed.
	os.MkdirAll(filepath.Dir(dst), 0o755)
	os.WriteFile(dst, after, 0o644)
	kind, err := j.Resolve(vault, moveIntent(vault, src, dst, before, after), time.Now().UTC())
	if err != nil || kind != KindApplied {
		t.Fatalf("an old-style half-made move still finishes: kind=%s err=%v", kind, err)
	}
	if _, err := os.Stat(src); !os.IsNotExist(err) {
		t.Errorf("finishing the copy removes the source: %v", err)
	}
	if got := readOr(dst); got != string(after) {
		t.Errorf("the destination keeps the journaled bytes: %q", got)
	}
}

func TestAMoveToATakenDestinationIsSkipped(t *testing.T) {
	vault, j, src, dst, before := moveFixture(t)
	was := pinned(t, src)
	os.MkdirAll(filepath.Dir(dst), 0o755)
	os.WriteFile(dst, []byte("someone else's note\n"), 0o644)
	in := Intent{Job: JobTasks, Rel: rel(t, vault, src), To: rel(t, vault, dst), Before: before, After: before}
	kind, err := j.Commit(vault, "r", "r-1", in, time.Now().UTC())
	if err != nil || kind != KindSkipped {
		t.Fatalf("a taken destination skips the move: kind=%s err=%v", kind, err)
	}
	now, err := os.Stat(src)
	if err != nil || !os.SameFile(was, now) || readOr(src) != string(before) {
		t.Errorf("a skipped move leaves the source where it was, untouched")
	}
	if got := readOr(dst); got != "someone else's note\n" {
		t.Errorf("the destination is never overwritten: %q", got)
	}
}

func TestACrossDeviceRenameFallsBackToACopy(t *testing.T) {
	vault, j, src, dst, before := moveFixture(t)
	j.rename = func(o, n string) error { return &os.LinkError{Op: "rename", Old: o, New: n, Err: syscall.EXDEV} }
	after := []byte("---\ntitle: x\n---\nrepaired\n")
	in := Intent{Job: JobTasks, Rel: rel(t, vault, src), To: rel(t, vault, dst), Before: before, After: after}
	kind, err := j.Commit(vault, "r", "r-1", in, time.Now().UTC())
	if err != nil || kind != KindApplied {
		t.Fatalf("a cross-device move still lands: kind=%s err=%v", kind, err)
	}
	if got := readOr(dst); got != string(after) {
		t.Errorf("the copy carries the journaled bytes: %q", got)
	}
	if _, err := os.Stat(src); !os.IsNotExist(err) {
		t.Errorf("the copy's source is removed: %v", err)
	}
	if e := lastEntry(t, j); e.Note != "moved by copy: the destination is on another device" {
		t.Errorf("the applied line names the fallback: %q", e.Note)
	}
}

func TestARefusedRenameSkipsTheMoveAndLeavesTheSource(t *testing.T) {
	vault, j, src, dst, before := moveFixture(t)
	was := pinned(t, src)
	j.rename = func(o, n string) error {
		return &os.LinkError{Op: "rename", Old: o, New: n, Err: errors.New("the file is being used by another process")}
	}
	in := Intent{Job: JobTasks, Rel: rel(t, vault, src), To: rel(t, vault, dst), Before: before, After: before}
	kind, err := j.Commit(vault, "r", "r-1", in, time.Now().UTC())
	if err != nil || kind != KindSkipped {
		t.Fatalf("a refused rename skips the move, never fails the night: kind=%s err=%v", kind, err)
	}
	now, err := os.Stat(src)
	if err != nil || !os.SameFile(was, now) || readOr(src) != string(before) {
		t.Errorf("a refused rename leaves the source where it was, untouched")
	}
	if _, err := os.Stat(dst); !os.IsNotExist(err) {
		t.Errorf("nothing lands at the destination: %v", err)
	}
	if e := lastEntry(t, j); !strings.Contains(e.Note, "could not be renamed") || !strings.Contains(e.Note, "another process") {
		t.Errorf("the skip names the error: %q", e.Note)
	}
}

func TestANoteEditedDuringItsMoveIsPutBackAndSkipped(t *testing.T) {
	vault, j, src, dst, before := moveFixture(t)
	edited := "---\ntitle: x\n---\nThe operator typed this mid-move.\n"
	calls := 0
	j.rename = func(o, n string) error {
		calls++
		if calls == 1 {
			// The edit lands between the hash check and the rename.
			os.WriteFile(o, []byte(edited), 0o644)
		}
		return os.Rename(o, n)
	}
	after := []byte("---\ntitle: x\n---\nrepaired\n")
	in := Intent{Job: JobTasks, Rel: rel(t, vault, src), To: rel(t, vault, dst), Before: before, After: after}
	kind, err := j.Commit(vault, "r", "r-1", in, time.Now().UTC())
	if err != nil || kind != KindSkipped {
		t.Fatalf("an edit made mid-move skips it: kind=%s err=%v", kind, err)
	}
	if got := readOr(src); got != edited {
		t.Errorf("the edited note is back where the operator made the edit: %q", got)
	}
	if _, err := os.Stat(dst); !os.IsNotExist(err) {
		t.Errorf("nothing is left at the destination: %v", err)
	}
	if e := lastEntry(t, j); e.Note != ErrConflict.Error() {
		t.Errorf("the skip is a conflict: %q", e.Note)
	}
}
