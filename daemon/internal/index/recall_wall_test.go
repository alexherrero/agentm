package index

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// The contract's `recall_exempt_areas`: never indexed, never embedded, never
// served to any surface (agentm-vault § Lifecycle per space).
//
// The folder it was written for holds birth certificates, a marriage licence
// and a file named `Recovery Codes`. Before this they sat in the lexical index
// and a query that happened to match them was served at x0.30 into whatever
// asked — a Claude session, or anything reading over MCP. Dampening was the only
// tool the contract had, and dampening is a weight, not a wall.

func withWall(t *testing.T, areas []string) {
	t.Helper()
	before := note.RecallExemptAreas()
	note.SetRecallExemptAreas(areas)
	t.Cleanup(func() { note.SetRecallExemptAreas(before) })
}

func writeNote(t *testing.T, vault, rel, body string) {
	t.Helper()
	abs := filepath.Join(vault, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(abs, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestAWalledAreaIsNeverWalkedIntoTheIndex(t *testing.T) {
	x := newTestIndex(t)
	withWall(t, []string{"personal/Home/Important Docs"})

	body := "---\ntitle: Codes\n---\n\nThe recovery codes are 111111 and 222222.\n"
	writeNote(t, x.vault, "personal/Home/Important Docs/Recovery Codes.md", body)
	writeNote(t, x.vault, "personal/Home/Recipes/turkey.md",
		"---\ntitle: Turkey\n---\n\nThe recovery time for the brine is two days.\n")
	// The near-miss. A string-prefix wall would swallow this one.
	writeNote(t, x.vault, "personal/Homework/algebra.md",
		"---\ntitle: Algebra\n---\n\nRecovery of the constant term.\n")

	if _, err := x.Reconcile(); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	out, err := x.Search(Query{Text: "recovery", K: 10})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	for _, r := range out.Results {
		if r.Path == "personal/Home/Important Docs/Recovery Codes.md" {
			t.Fatal("a walled note was served")
		}
	}
	got := resultPaths(out.Results)
	if len(got) != 2 {
		t.Fatalf("expected the two unwalled notes, got %v", got)
	}
}

// Naming an area in the contract has to remove what is already indexed, not
// merely stop adding to it — otherwise the wall does nothing on the only
// machine that matters, where the notes were indexed years ago.
func TestNamingAnAreaRemovesWhatWasAlreadyIndexed(t *testing.T) {
	x := newTestIndex(t)
	body := "---\ntitle: Codes\n---\n\nThe recovery codes are 111111 and 222222.\n"
	writeNote(t, x.vault, "personal/Home/Important Docs/Recovery Codes.md", body)

	if _, err := x.Reconcile(); err != nil {
		t.Fatalf("first reconcile: %v", err)
	}
	out, err := x.Search(Query{Text: "recovery", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) != 1 {
		t.Fatalf("the fixture never indexed: %v", resultPaths(out.Results))
	}

	withWall(t, []string{"personal/Home/Important Docs"})
	rep, err := x.Reconcile()
	if err != nil {
		t.Fatalf("second reconcile: %v", err)
	}
	if rep.Removed != 1 {
		t.Errorf("the reconcile removed %d rows, want the one walled note", rep.Removed)
	}
	out, err = x.Search(Query{Text: "recovery", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) != 0 {
		t.Errorf("still served after the wall landed: %v", resultPaths(out.Results))
	}
}

// The notifier calls IndexFile directly, so the walk's refusal is not the only
// door.
func TestIndexFileRefusesAWalledPath(t *testing.T) {
	x := newTestIndex(t)
	withWall(t, []string{"personal/Home/Important Docs"})
	rel := "personal/Home/Important Docs/Marriage License.md"
	writeNote(t, x.vault, rel, "---\ntitle: Licence\n---\n\nA marriage licence.\n")

	if err := x.IndexFile(rel); err != nil {
		t.Fatalf("IndexFile: %v", err)
	}
	out, err := x.Search(Query{Text: "marriage", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) != 0 {
		t.Errorf("IndexFile indexed a walled path: %v", resultPaths(out.Results))
	}
}

// The wall answers to no caller flag. `include_archived` is the one lever a
// query has over the other three walls, and it must not reach this one.
func TestNoQueryFlagLiftsTheWall(t *testing.T) {
	x := newTestIndex(t)
	rel := "personal/Home/Important Docs/Recovery Codes.md"
	addNote(t, x, rel, "Codes", "The recovery codes are 111111 and 222222.")
	withWall(t, []string{"personal/Home/Important Docs"})

	for _, q := range []Query{
		{Text: "recovery", K: 5},
		{Text: "recovery", K: 5, IncludeArchived: true},
		{Text: "recovery", K: 5, Mode: ModeFusion, IncludeArchived: true},
	} {
		out, err := x.Search(q)
		if err != nil {
			t.Fatalf("search %+v: %v", q, err)
		}
		if len(out.Results) != 0 {
			t.Errorf("query %+v served a walled note: %v", q, resultPaths(out.Results))
		}
		if out.RecallWalled != 1 {
			t.Errorf("query %+v walled %d rows, want 1 counted", q, out.RecallWalled)
		}
	}
}

// With no area named, nothing is walled. The shipped state of every vault
// before this landed, and the state of one whose contract does not carry the
// line.
func TestWithNoAreaNamedNothingIsWalled(t *testing.T) {
	x := newTestIndex(t)
	withWall(t, nil)
	addNote(t, x, "personal/Home/Important Docs/Recovery Codes.md", "Codes",
		"The recovery codes are 111111 and 222222.")
	out, err := x.Search(Query{Text: "recovery", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) != 1 {
		t.Errorf("an unwalled vault hid something: %v", resultPaths(out.Results))
	}
}

// No index row, no embedding: the dense arm draws its queue from the index, so
// the wall at the walk is what keeps a walled note out of the vector store too.
// Asserted rather than assumed — "never embedded" is the design's word.
func TestAWalledNoteIsNeverPendingAnEmbedding(t *testing.T) {
	x := newTestIndex(t)
	withWall(t, []string{"personal/Home/Important Docs"})
	writeNote(t, x.vault, "personal/Home/Important Docs/Recovery Codes.md",
		"---\ntitle: Codes\n---\n\nThe recovery codes are 111111 and 222222.\n")
	writeNote(t, x.vault, "personal/Home/Recipes/turkey.md",
		"---\ntitle: Turkey\n---\n\nA brine for the bird.\n")
	if _, err := x.Reconcile(); err != nil {
		t.Fatalf("reconcile: %v", err)
	}

	// No scope filter, so nothing but the wall can be keeping it out.
	pending, err := x.PendingEmbeds("test-model", nil, 100)
	if err != nil {
		t.Fatalf("pending embeds: %v", err)
	}
	for _, p := range pending {
		if p.Path == "personal/Home/Important Docs/Recovery Codes.md" {
			t.Fatal("a walled note is queued for embedding")
		}
	}
}
