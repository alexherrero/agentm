package index

import (
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// The contract's `always_load_areas`: read whole by the loader at session start,
// so the ranked arms never serve a second copy (agentm-vault § Surfaces).
//
// The fourth list, and the one most easily mistaken for the wall beside it. The
// difference is the point: a walled area is refused at the walk and never enters
// the corpus; an always-load area is indexed, embedded and findable by name on
// purpose, because Drive-side surfaces search by title and `agentmd search` for
// a rule should answer. Only the *ranked* answer drops it.
//
// What it is for: the filing contract is the largest file in the vault and every
// session opens with it, so before this a prompt that merely mentioned filing
// could spend fifteen kilobytes re-reading what was already in the window.

func withAlwaysLoad(t *testing.T, areas []string) {
	t.Helper()
	before := note.AlwaysLoadAreas()
	note.SetAlwaysLoadAreas(areas)
	t.Cleanup(func() { note.SetAlwaysLoadAreas(before) })
}

func TestAnAlwaysLoadAreaIsIndexedButNeverRanked(t *testing.T) {
	x := newTestIndex(t)
	withAlwaysLoad(t, []string{"standards"})

	writeNote(t, x.vault, "standards/storage-rules.md",
		"---\ntitle: The filing contract\n---\n\nThe routing table for a capture.\n")
	writeNote(t, x.vault, "agent/memory/semantic/routing.md",
		"---\ntitle: Routing\n---\n\nHow a capture takes its routing.\n")

	if _, err := x.Reconcile(); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	out, err := x.Search(Query{Text: "routing", K: 10})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	for _, r := range out.Results {
		if r.Path == "standards/storage-rules.md" {
			t.Fatal("an always-load file was ranked into the answer")
		}
	}
	if len(out.Results) != 1 {
		t.Fatalf("expected the one memory note, got %v", resultPaths(out.Results))
	}
	// The count, so the drop is a number in the call log rather than a hit list
	// that quietly got shorter.
	if out.AlwaysLoadHidden != 1 {
		t.Errorf("AlwaysLoadHidden = %d, want 1", out.AlwaysLoadHidden)
	}
}

// The property that separates this list from the wall above it. A test that only
// checked the drop would still pass if someone "simplified" the two into one and
// started refusing these files at the walk — and a `standards/` file that is not
// in the index cannot be found by name, which is what Drive-side surfaces and
// `agentmd search` both need.
func TestAnAlwaysLoadFileStaysInTheIndex(t *testing.T) {
	x := newTestIndex(t)
	writeNote(t, x.vault, "standards/storage-rules.md",
		"---\ntitle: The filing contract\n---\n\nThe routing table for a capture.\n")

	if _, err := x.Reconcile(); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	// Indexed, with no area set: the baseline.
	out, err := x.Search(Query{Text: "routing", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) != 1 {
		t.Fatalf("the fixture never indexed: %v", resultPaths(out.Results))
	}

	// Naming the area must not cause a reconcile to *remove* the row, which is
	// exactly what the wall's own contract requires and this one forbids.
	withAlwaysLoad(t, []string{"standards"})
	rep, err := x.Reconcile()
	if err != nil {
		t.Fatalf("second reconcile: %v", err)
	}
	if rep.Removed != 0 {
		t.Errorf("the reconcile removed %d row(s); an always-load area is not a wall",
			rep.Removed)
	}
}

// The rule this list got wrong first, and the only reason it is written the way
// it is. `standards/voice/` is the operator's voice library; the loader's glob
// is `<area>/*.md`, non-recursive, so it has never been injected. A subtree rule
// excluded it from recall as well, and the library left every memory surface at
// once — four gold questions to a miss, and the retrieval gate refused it.
//
// The same reasoning covers `moc-`: the loader skips a generated map, so a
// session never has one, so recall has to keep answering with it.
func TestASubdirectoryTheLoaderNeverReadsStaysInRecall(t *testing.T) {
	x := newTestIndex(t)
	withAlwaysLoad(t, []string{"standards"})

	writeNote(t, x.vault, "standards/storage-rules.md",
		"---\ntitle: Contract\n---\n\nThe prose style of a routing rule.\n")
	writeNote(t, x.vault, "standards/voice/design-doc-prose.md",
		"---\ntitle: Voice\n---\n\nThe prose style for a design document.\n")
	writeNote(t, x.vault, "standards/moc-standards.md",
		"---\ntitle: Map\n---\n\nA map of the prose rules.\n")

	if _, err := x.Reconcile(); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	out, err := x.Search(Query{Text: "prose", K: 10})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	got := map[string]bool{}
	for _, r := range out.Results {
		got[r.Path] = true
	}
	if got["standards/storage-rules.md"] {
		t.Error("a loaded file was ranked; the session already has it")
	}
	if !got["standards/voice/design-doc-prose.md"] {
		t.Error("the voice library left recall — the loader never injected it, " +
			"so dropping it here makes it unreachable from every surface")
	}
	if !got["standards/moc-standards.md"] {
		t.Error("a generated map left recall — the loader skips `moc-`, so a " +
			"session never has one")
	}
}

// An area is a directory. A file whose name merely starts with the word is not
// inside it — the same near-miss the wall's own test pins.
func TestAWordInsideANameIsNotTheArea(t *testing.T) {
	x := newTestIndex(t)
	withAlwaysLoad(t, []string{"standards"})

	writeNote(t, x.vault, "standards-draft/proposal.md",
		"---\ntitle: Draft\n---\n\nA proposal about routing.\n")
	writeNote(t, x.vault, "standards/storage-rules.md",
		"---\ntitle: Contract\n---\n\nThe routing table.\n")

	if _, err := x.Reconcile(); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	out, err := x.Search(Query{Text: "routing", K: 10})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	got := resultPaths(out.Results)
	if len(got) != 1 || got[0] != "standards-draft/proposal.md" {
		t.Fatalf("expected only the draft, got %v", got)
	}
}

// Empty means nothing is dropped. The list fails open by design — the cost of
// missing it is a duplicate in the window, not a leak — and an empty list read
// as "match everything" would blank every answer.
func TestNoAreaDropsNothing(t *testing.T) {
	x := newTestIndex(t)
	withAlwaysLoad(t, nil)

	writeNote(t, x.vault, "standards/storage-rules.md",
		"---\ntitle: Contract\n---\n\nThe routing table.\n")
	if _, err := x.Reconcile(); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	out, err := x.Search(Query{Text: "routing", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) != 1 {
		t.Fatalf("an empty area list dropped a row: %v", resultPaths(out.Results))
	}
	if out.AlwaysLoadHidden != 0 {
		t.Errorf("AlwaysLoadHidden = %d with no area set", out.AlwaysLoadHidden)
	}
}

// An explicit archive query lifts the archive wall. It must not lift this one:
// asking for cold notes is not asking to be told twice.
func TestIncludeArchivedDoesNotLiftIt(t *testing.T) {
	x := newTestIndex(t)
	withAlwaysLoad(t, []string{"standards"})

	writeNote(t, x.vault, "standards/storage-rules.md",
		"---\ntitle: Contract\n---\n\nThe routing table.\n")
	if _, err := x.Reconcile(); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	out, err := x.Search(Query{Text: "routing", K: 5, IncludeArchived: true})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) != 0 {
		t.Errorf("served under IncludeArchived: %v", resultPaths(out.Results))
	}
}
