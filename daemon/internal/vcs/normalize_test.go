package vcs

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/plumbing/object"
	"golang.org/x/text/unicode/norm"
)

// One accented filename used to hold the corpus-write gate shut forever.
//
// macOS writes filenames decomposed — "ó" as an "o" followed by a combining
// acute — while git's index stores them composed. C-git folds the two under
// core.precomposeunicode and calls the tree clean. go-git implements no such
// thing: it reads the composed index entry as a deletion and the decomposed name
// on disk as an untracked addition, so Dirty() reports two phantom changes that
// no commit can ever clear. The gate refuses on a dirty worktree, which meant a
// single "Lección 35.md" in the vault blocked every corpus-wide write job —
// alias_backfill, the heat policy, the arc migration, dreaming's inbox drain.
//
// These tests hold the fold in place. They build the split by hand rather than
// relying on the host filesystem to create it, so the same fixture reproduces on
// Linux and Windows, where the two names really are two files.

const (
	// "Lección" with a precomposed U+00F3.
	nfcNote = "Church/Prepared Lessons/Lecci\u00f3n 35.md"
	// The same name with "o" + U+0301 combining acute — what macOS writes.
	nfdNote = "Church/Prepared Lessons/Leccio\u0301n 35.md"

	noteBody = "---\ntype: reference\n---\nReconciliaos con Dios.\n"
)

// TestFixtureNamesAreActuallyTwoSpellings guards the fixture itself. Every test
// below is worthless if the two constants are the same string, and they are two
// renderings of one word that no editor, diff, or terminal shows differently —
// a tool that normalizes this file on save would collapse them and leave a suite
// that passes while asking nothing. The constants are written as escapes for
// that reason; this checks the escapes still say what they are supposed to.
func TestFixtureNamesAreActuallyTwoSpellings(t *testing.T) {
	if nfcNote == nfdNote {
		t.Fatal("the composed and decomposed fixture names are the same string; " +
			"something normalized this file and every test in it now proves nothing")
	}
	if norm.NFC.String(nfdNote) != nfcNote {
		t.Fatalf("the fixture names are not two normalizations of one name:\n"+
			"  composed:   %q\n  decomposed: %q", nfcNote, nfdNote)
	}
}

func TestDirty_DecomposedFilenameIsNotAPhantomChange(t *testing.T) {
	dir := commitNote(t, nfcNote, noteBody)
	decomposeOnDisk(t, dir, nfcNote, nfdNote)

	r := Open(dir)
	if !r.Available() {
		t.Fatalf("fixture repository did not open: %s", r.Status())
	}
	dirty, err := r.Dirty()
	if err != nil {
		t.Fatalf("Dirty: %v", err)
	}
	if len(dirty) != 0 {
		t.Errorf("a filename that differs from the index only by Unicode normalization\n"+
			"reported %d uncommitted change(s), which holds the corpus-write gate shut\n"+
			"forever — no commit can clear a difference that is not there:\n  %s",
			len(dirty), strings.Join(dirty, "\n  "))
	}
}

// TestDirty_DecomposedFilenameWithRealEditIsStillDirty is the other half. Folding
// normalization must not fold away content: an accented note that was actually
// edited has to keep reporting dirty, or the fix trades a gate that never opens
// for one that never closes.
func TestDirty_DecomposedFilenameWithRealEditIsStillDirty(t *testing.T) {
	dir := commitNote(t, nfcNote, noteBody)
	decomposeOnDisk(t, dir, nfcNote, nfdNote)
	if err := os.WriteFile(filepath.Join(dir, filepath.FromSlash(nfdNote)),
		[]byte(noteBody+"\nAn edit made after the commit.\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	r := Open(dir)
	dirty, err := r.Dirty()
	if err != nil {
		t.Fatalf("Dirty: %v", err)
	}
	if len(dirty) != 1 {
		t.Fatalf("an edited note reported %d path(s), want exactly 1: %v", len(dirty), dirty)
	}
	if dirty[0] != nfcNote {
		t.Errorf("edited note reported as %q, want the composed name %q", dirty[0], nfcNote)
	}
}

// TestDirty_DecomposedDeletionIsStillDirty covers the third shape: the note is
// gone from disk entirely. There is no untracked twin to pair the index entry
// with, so the deletion must survive the fold.
func TestDirty_DecomposedDeletionIsStillDirty(t *testing.T) {
	dir := commitNote(t, nfcNote, noteBody)
	if err := os.Remove(filepath.Join(dir, filepath.FromSlash(nfcNote))); err != nil {
		t.Fatal(err)
	}

	r := Open(dir)
	dirty, err := r.Dirty()
	if err != nil {
		t.Fatalf("Dirty: %v", err)
	}
	if len(dirty) != 1 || dirty[0] != nfcNote {
		t.Errorf("a deleted note reported %v, want exactly [%q]", dirty, nfcNote)
	}
}

// TestCommit_DecomposedPathUpdatesTheComposedIndexEntry is the write side. The
// watcher hands Commit the name the filesystem gave it, which on macOS is the
// decomposed one. Staged verbatim it adds a second index entry beside the
// composed one, and the vault ends up carrying the same note twice under two
// spellings of one name.
func TestCommit_DecomposedPathUpdatesTheComposedIndexEntry(t *testing.T) {
	dir := commitNote(t, nfcNote, noteBody)
	decomposeOnDisk(t, dir, nfcNote, nfdNote)
	if err := os.WriteFile(filepath.Join(dir, filepath.FromSlash(nfdNote)),
		[]byte(noteBody+"\nAn edit the watcher saw.\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	r := Open(dir)
	hash, err := r.Commit(OriginLocal, []string{nfdNote})
	if err != nil {
		t.Fatalf("Commit: %v", err)
	}
	if hash == "" {
		t.Fatal("an edited note produced no commit")
	}

	names := indexNames(t, dir)
	if len(names) != 1 {
		t.Fatalf("the index carries %d entries after committing one edit, want 1: %v",
			len(names), names)
	}
	if names[0] != nfcNote {
		t.Errorf("index entry is %q, want the composed name %q", names[0], nfcNote)
	}

	dirty, err := r.Dirty()
	if err != nil {
		t.Fatalf("Dirty after commit: %v", err)
	}
	if len(dirty) != 0 {
		t.Errorf("the worktree is still dirty after committing the edit: %v", dirty)
	}
}

// TestDirty_PrecomposeOffKeepsTheFormsApart is why the fold is a setting and not
// simply what this package always does. On a filesystem that stores what it is
// given, the two spellings are two files, and folding them there would hide a
// real deletion behind a real addition. Git turns core.precomposeunicode on for
// macOS and leaves it off elsewhere; so does this.
func TestDirty_PrecomposeOffKeepsTheFormsApart(t *testing.T) {
	dir := commitNote(t, nfcNote, noteBody)
	decomposeOnDisk(t, dir, nfcNote, nfdNote)

	repo, err := git.PlainOpen(dir)
	if err != nil {
		t.Fatal(err)
	}
	setPrecompose(t, repo, "false")

	dirty, err := Open(dir).Dirty()
	if err != nil {
		t.Fatalf("Dirty: %v", err)
	}
	if len(dirty) != 2 {
		t.Errorf("with core.precomposeunicode off the two spellings are two files and\n"+
			"both must be reported; got %d path(s): %v", len(dirty), dirty)
	}
}

// TestPrecomposeDefaultsToGitsOwn pins the fallback for a repository that says
// nothing, which is every repository the operator's git created — git writes the
// setting into new repositories on macOS, but a vault cloned or initialized
// elsewhere and carried over will not carry it.
func TestPrecomposeDefaultsToGitsOwn(t *testing.T) {
	dir := t.TempDir()
	if _, err := git.PlainInit(dir, false); err != nil {
		t.Fatal(err)
	}
	want := runtime.GOOS == "darwin"
	if got := Open(dir).precompose; got != want {
		t.Errorf("a repository with no core.precomposeunicode folds=%v on %s, want %v",
			got, runtime.GOOS, want)
	}
}

// ---------------------------------------------------------------------------

// commitNote builds a repository holding one committed note at `rel`, and
// returns its root. The note is written and staged under the name given, so the
// index and HEAD both carry that exact spelling.
//
// core.precomposeunicode is set explicitly rather than left to the platform
// default. On macOS it would be on anyway, which is where the defect lives — but
// stated in the config it is also on for the Linux and Windows runners, so the
// fold is exercised everywhere instead of only on the one machine where a
// regression would already have shipped.
func commitNote(t *testing.T, rel, body string) string {
	t.Helper()
	dir := t.TempDir()

	repo, err := git.PlainInit(dir, false)
	if err != nil {
		t.Fatalf("init: %v", err)
	}
	setPrecompose(t, repo, "true")
	abs := filepath.Join(dir, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(abs, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	wt, err := repo.Worktree()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := wt.Add(rel); err != nil {
		t.Fatalf("add %s: %v", rel, err)
	}
	if _, err := wt.Commit("fixture", &git.CommitOptions{Author: fixtureSignature()}); err != nil {
		t.Fatalf("commit: %v", err)
	}
	return dir
}

// decomposeOnDisk renames the file so the directory entry carries `to`'s exact
// bytes while the index still carries `from`'s.
//
// The rename goes through an ASCII intermediate on purpose. APFS is
// normalization-insensitive, so renaming straight from the composed name to the
// decomposed one resolves to the same file and leaves the original bytes in
// place — the rename succeeds and changes nothing. Landing on an unrelated name
// first forces the directory entry to be created fresh from the bytes given.
func decomposeOnDisk(t *testing.T, dir, from, to string) {
	t.Helper()
	fromAbs := filepath.Join(dir, filepath.FromSlash(from))
	toAbs := filepath.Join(dir, filepath.FromSlash(to))
	via := filepath.Join(filepath.Dir(fromAbs), "renaming-in-progress.tmp")

	if err := os.Rename(fromAbs, via); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(via, toAbs); err != nil {
		t.Fatal(err)
	}

	// A filesystem that normalizes on write (HFS+ did) cannot hold the two forms
	// apart at all, so the defect cannot occur there and neither can the test.
	entries, err := os.ReadDir(filepath.Dir(toAbs))
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if e.Name() == filepath.Base(toAbs) {
			return
		}
	}
	t.Skipf("this filesystem normalized %q on write; it cannot store the decomposed "+
		"form, so the composed/decomposed split under test cannot occur here",
		filepath.Base(toAbs))
}

func setPrecompose(t *testing.T, repo *git.Repository, value string) {
	t.Helper()
	cfg, err := repo.Config()
	if err != nil {
		t.Fatal(err)
	}
	cfg.Raw.Section("core").SetOption("precomposeunicode", value)
	if err := repo.SetConfig(cfg); err != nil {
		t.Fatal(err)
	}
}

func fixtureSignature() *object.Signature {
	return &object.Signature{
		Name:  "fixture",
		Email: "fixture@localhost",
		When:  time.Unix(1754870400, 0).UTC(),
	}
}

func indexNames(t *testing.T, dir string) []string {
	t.Helper()
	repo, err := git.PlainOpen(dir)
	if err != nil {
		t.Fatal(err)
	}
	idx, err := repo.Storer.Index()
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, e := range idx.Entries {
		names = append(names, e.Name)
	}
	return names
}
