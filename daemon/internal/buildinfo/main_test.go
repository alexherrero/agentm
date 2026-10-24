package buildinfo

import (
	"strings"
	"testing"
)

// clonePair is a bare origin and a clone of it, both isolated from the global
// git config.
func clonePair(t *testing.T) (origin, work string) {
	t.Helper()
	seed := t.TempDir()
	run(t, seed, "init", "-q", "-b", "main")
	commitFile(t, seed, "a.md", "one")
	origin = t.TempDir()
	run(t, origin, "clone", "-q", "--bare", seed, ".")
	work = t.TempDir()
	run(t, work, "clone", "-q", origin, ".")
	return origin, work
}

func TestCompareMainBehindNamesTheNewestMissing(t *testing.T) {
	origin, work := clonePair(t)
	other := t.TempDir()
	run(t, other, "clone", "-q", origin, ".")
	commitFile(t, other, "b.md", "two")
	commitFile(t, other, "c.md", "the fix that merged")
	run(t, other, "push", "-q", "origin", "main")
	run(t, work, "fetch", "-q", "origin")

	got := CompareMain(work)
	if got.Err != "" || got.Behind != 2 || got.Ahead != 0 {
		t.Fatalf("got %+v, want 2 behind", got)
	}
	if s := got.Sentence(); !strings.HasPrefix(s, "the primary clone is 2 commits behind main; the newest it lacks is ") ||
		!strings.HasSuffix(s, `"c.md"`) {
		t.Errorf("sentence = %q", s)
	}
}

func TestCompareMainLevelIsQuiet(t *testing.T) {
	_, work := clonePair(t)
	got := CompareMain(work)
	if got.Err != "" || got.Behind != 0 || got.Sentence() != "" || got.Note() != "" {
		t.Fatalf("got %+v, sentence %q, note %q", got, got.Sentence(), got.Note())
	}
}

func TestCompareMainOnABranchIsANoteNotAnError(t *testing.T) {
	_, work := clonePair(t)
	run(t, work, "checkout", "-q", "-b", "feature")
	commitFile(t, work, "d.md", "mine")
	got := CompareMain(work)
	if got.Err != "" || got.Sentence() != "" {
		t.Fatalf("got %+v", got)
	}
	if n := got.Note(); n != "the primary clone is on branch `feature` and 1 commit ahead of main" {
		t.Errorf("note = %q", n)
	}
}
