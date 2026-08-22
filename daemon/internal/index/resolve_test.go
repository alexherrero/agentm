package index

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

// newVaultIndex opens an index over a real directory, so Reconcile has files to
// walk. The link tests need the reconcile path specifically — resolution is a
// property of when a note is re-read, and Upsert on its own never exercises the
// skip that caused the bug.
func newVaultIndex(t *testing.T) (*Index, string) {
	t.Helper()
	dir := t.TempDir()
	vault := filepath.Join(dir, "vault")
	if err := os.MkdirAll(filepath.Join(vault, "memory"), 0o755); err != nil {
		t.Fatal(err)
	}
	x, err := Open(filepath.Join(dir, "index.db"), vault, "", false)
	if err != nil {
		t.Fatalf("opening index: %v", err)
	}
	t.Cleanup(func() { x.Close() })
	return x, vault
}

func writeVaultNote(t *testing.T, vault, rel, body string) {
	t.Helper()
	abs := filepath.Join(vault, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(abs, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

// A link written before its target existed finds it on the next reconcile.
//
// The ordinary write order — you link to something because you are about to
// write it — so the source almost always predates the target and nothing edits
// the source again afterwards. Resolution happens when a note is re-read, and
// Reconcile skips a file whose mtime and size have not moved, so without a
// re-resolution pass that link stays dangling forever.
func TestALinkResolvesOnceItsTargetExists(t *testing.T) {
	x, vault := newVaultIndex(t)

	writeVaultNote(t, vault, "memory/a.md",
		"---\ntitle: a\n---\n\nPoints at [[target]].\n")
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}
	back, err := x.Backlinks("memory/target.md")
	if err != nil {
		t.Fatal(err)
	}
	if len(back) != 0 {
		t.Fatalf("a link resolved to a note that does not exist: %+v", back)
	}

	// The target appears. The source is untouched, which is the case that used
	// to stay broken.
	writeVaultNote(t, vault, "memory/target.md",
		"---\ntitle: the target\n---\n\nNow it exists.\n")
	rep, err := x.Reconcile()
	if err != nil {
		t.Fatal(err)
	}
	if rep.Resolved != 1 {
		t.Errorf("Reconcile resolved %d links, want 1 — and a reconcile that "+
			"fixed the graph should say so", rep.Resolved)
	}

	back, err = x.Backlinks("memory/target.md")
	if err != nil {
		t.Fatal(err)
	}
	if len(back) != 1 {
		t.Fatalf("%d backlinks after the target appeared, want 1 — the footer "+
			"stage would write nothing and the stub stage would propose creating "+
			"a note that already exists", len(back))
	}
	// `Resolved` carries the source path on a backlink query.
	if back[0].Resolved != "memory/a.md" {
		t.Errorf("the backlink came from %q", back[0].Resolved)
	}
}

// And the target stops being reported as missing, which is the half the stub
// stage reads.
func TestAResolvedLinkLeavesTheDanglingList(t *testing.T) {
	ctx := context.Background()
	x, vault := newVaultIndex(t)

	writeVaultNote(t, vault, "memory/a.md",
		"---\ntitle: a\n---\n\nPoints at [[target]].\n")
	writeVaultNote(t, vault, "memory/b.md",
		"---\ntitle: b\n---\n\nAlso points at [[target]].\n")
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}
	dangling, err := x.DanglingTargets(ctx, 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(dangling) != 1 {
		t.Fatalf("dangling targets are %+v, want the one nothing answers", dangling)
	}

	writeVaultNote(t, vault, "memory/target.md",
		"---\ntitle: the target\n---\n\nNow it exists.\n")
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}
	dangling, err = x.DanglingTargets(ctx, 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(dangling) != 0 {
		t.Errorf("%+v still reads as missing after being written; the stub stage "+
			"would propose creating it", dangling)
	}
}

// A link that points at nothing stays recorded. A dangling link is a fact about
// the corpus rather than an error in it, and it is what the stub stage reads.
func TestResolveDanglingLeavesGenuinelyMissingTargetsAlone(t *testing.T) {
	ctx := context.Background()
	x, vault := newVaultIndex(t)

	writeVaultNote(t, vault, "memory/a.md",
		"---\ntitle: a\n---\n\nPoints at [[nobody-has-written-this]].\n")
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}
	n, err := x.ResolveDangling(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if n != 0 {
		t.Errorf("ResolveDangling resolved %d links against a corpus that has no "+
			"such note", n)
	}
	dangling, err := x.DanglingTargets(ctx, 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(dangling) != 1 {
		t.Errorf("a genuinely missing target stopped being recorded: %+v", dangling)
	}
}

// Running it twice changes nothing the first run did not. A reconcile is
// periodic, and a pass that re-reported the same fixes every night would put a
// number on the digest that never fell.
func TestResolveDanglingIsIdempotent(t *testing.T) {
	ctx := context.Background()
	x, vault := newVaultIndex(t)

	writeVaultNote(t, vault, "memory/a.md",
		"---\ntitle: a\n---\n\nPoints at [[target]].\n")
	writeVaultNote(t, vault, "memory/target.md",
		"---\ntitle: the target\n---\n\nHere.\n")
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 3; i++ {
		n, err := x.ResolveDangling(ctx)
		if err != nil {
			t.Fatal(err)
		}
		if n != 0 {
			t.Errorf("run %d resolved %d links that were already resolved", i, n)
		}
	}
}

// It uses the same resolver the write path uses. Two implementations of "which
// note does this name mean" is the drift surface every seam in this design
// exists to close — and a second one here would resolve differently from the
// first on exactly the cases that are hard to notice.
func TestResolutionAgreesWithTheWritePath(t *testing.T) {
	x, vault := newVaultIndex(t)

	// A target naming two segments, which the resolver scores above a bare
	// basename — the case where two implementations would most plausibly differ.
	writeVaultNote(t, vault, "memory/deep/target.md",
		"---\ntitle: deep\n---\n\nHere.\n")
	writeVaultNote(t, vault, "memory/a.md",
		"---\ntitle: a\n---\n\nPoints at [[deep/target]].\n")
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}
	viaWrite, err := x.Backlinks("memory/deep/target.md")
	if err != nil {
		t.Fatal(err)
	}
	if len(viaWrite) != 1 {
		t.Fatalf("the write path resolved %d links, want 1", len(viaWrite))
	}

	// Now force the re-resolution path over the same link, from scratch.
	if _, err := x.db.Exec(`UPDATE links SET resolved = ''`); err != nil {
		t.Fatal(err)
	}
	n, err := x.ResolveDangling(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Fatalf("the re-resolution path resolved %d links, want the same 1", n)
	}
	viaResolve, err := x.Backlinks("memory/deep/target.md")
	if err != nil {
		t.Fatal(err)
	}
	if len(viaResolve) != len(viaWrite) || viaResolve[0].Resolved != viaWrite[0].Resolved {
		t.Errorf("the two paths disagree:\n write   %+v\n resolve %+v",
			viaWrite, viaResolve)
	}
}

// An empty corpus resolves nothing and does not fail. A fresh vault reconciles
// before it has notes.
func TestResolveDanglingOnAnEmptyCorpus(t *testing.T) {
	x, _ := newVaultIndex(t)
	n, err := x.ResolveDangling(context.Background())
	if err != nil {
		t.Fatalf("ResolveDangling on an empty index: %v", err)
	}
	if n != 0 {
		t.Errorf("resolved %d links with no notes at all", n)
	}
}
