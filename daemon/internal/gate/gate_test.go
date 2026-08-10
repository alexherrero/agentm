package gate

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/plumbing/object"

	"github.com/alexherrero/agentm/daemon/internal/config"
)

// The gate is where the Unicode-normalization defect was actually felt, so this
// is where it is pinned.
//
// macOS writes filenames decomposed and git's index stores them composed. go-git
// implements none of git's core.precomposeunicode reconciliation, so it read one
// accented note as a deletion plus an untracked addition — a worktree that is
// permanently dirty, and a gate that refuses forever. Nothing could clear it,
// because there was nothing there to clear. Every corpus-wide write job sat
// behind that refusal: the alias backfill, the heat policy, the arc migration,
// dreaming's inbox drain.
//
// vcs.Dirty is where the fold lives and vcs's own tests cover its edges. This
// test exists because the fold is only worth having if the gate opens, and that
// is a different question from whether Dirty returns an empty slice.

const (
	// "Lección" composed, as git's index spells it.
	nfcNote = "Church/Prepared Lessons/Lecci\u00f3n 35.md"
	// The same name decomposed, as macOS writes it to disk.
	nfdNote = "Church/Prepared Lessons/Leccio\u0301n 35.md"
)

func TestEvaluate_AccentedFilenameDoesNotHoldTheGateShut(t *testing.T) {
	dir := vaultWithCommittedNote(t, nfcNote)
	decomposeOnDisk(t, dir, nfcNote, nfdNote)

	res, err := Evaluate(&config.Config{VaultPath: dir})
	if err != nil {
		t.Errorf("the gate refused a clean vault: %v", err)
	}
	if !res.Pass {
		t.Errorf("one accented filename held the corpus-write gate shut. No job "+
			"behind it can ever run, and no commit can open it:\n%s", res.Explain())
	}
	if res.Head == "" {
		t.Error("the gate passed without naming a head; a job that has to be undone " +
			"needs a point to be undone to")
	}
}

// TestEvaluate_AccentedFilenameWithRealEditStillRefuses is the negative. The
// fold must not be a way for genuine uncommitted work to slip past the gate — an
// accented note that was actually edited has to keep it shut.
func TestEvaluate_AccentedFilenameWithRealEditStillRefuses(t *testing.T) {
	dir := vaultWithCommittedNote(t, nfcNote)
	decomposeOnDisk(t, dir, nfcNote, nfdNote)
	if err := os.WriteFile(filepath.Join(dir, filepath.FromSlash(nfdNote)),
		[]byte("An edit that was never committed.\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	res, err := Evaluate(&config.Config{VaultPath: dir})
	if err == nil || res.Pass {
		t.Fatal("the gate opened over an uncommitted edit to an accented note; " +
			"reverting a corpus-wide job would revert that edit with it")
	}
	if len(res.Reasons) != 1 || res.Reasons[0].Code != ReasonDirtyTree {
		t.Errorf("refused for %v, want a single %s", res.Reasons, ReasonDirtyTree)
	}
}

// ---------------------------------------------------------------------------

// vaultWithCommittedNote returns a vault root holding one committed note.
//
// core.precomposeunicode is stated rather than left to the platform default so
// the Linux and Windows runners exercise the same fold macOS gets for free.
func vaultWithCommittedNote(t *testing.T, rel string) string {
	t.Helper()
	dir := t.TempDir()

	repo, err := git.PlainInit(dir, false)
	if err != nil {
		t.Fatalf("init: %v", err)
	}
	cfg, err := repo.Config()
	if err != nil {
		t.Fatal(err)
	}
	cfg.Raw.Section("core").SetOption("precomposeunicode", "true")
	if err := repo.SetConfig(cfg); err != nil {
		t.Fatal(err)
	}

	abs := filepath.Join(dir, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(abs, []byte("Reconciliaos con Dios.\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	wt, err := repo.Worktree()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := wt.Add(rel); err != nil {
		t.Fatalf("add: %v", err)
	}
	if _, err := wt.Commit("fixture", &git.CommitOptions{Author: &object.Signature{
		Name:  "fixture",
		Email: "fixture@localhost",
		When:  time.Unix(1754870400, 0).UTC(),
	}}); err != nil {
		t.Fatalf("commit: %v", err)
	}
	return dir
}

// decomposeOnDisk leaves the directory entry carrying `to`'s exact bytes while
// the index still carries `from`'s. The ASCII intermediate is required: APFS is
// normalization-insensitive, so renaming straight between the two forms resolves
// to the same file and leaves the original bytes untouched.
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

	entries, err := os.ReadDir(filepath.Dir(toAbs))
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if e.Name() == filepath.Base(toAbs) {
			return
		}
	}
	t.Skipf("this filesystem normalized %q on write, so it cannot hold the two "+
		"spellings apart and the defect under test cannot occur here",
		filepath.Base(toAbs))
}
