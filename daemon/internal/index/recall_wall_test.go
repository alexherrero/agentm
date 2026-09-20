package index

import (
	"database/sql"
	"errors"
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
//
// These tests asked `Search` and `PendingEmbeds` whether a walled note was
// served, and both answered no — because both join docmeta, and the wall dropped
// the docmeta row. What they never asked was whether the *text* had left, and it
// had not: `Delete` cleared docmeta and left `chunks` holding the note's body
// and `entities` holding tokens lifted out of it. So the assertion below is on
// the rows, for every walled note and at every door, and "never indexed" is
// checked as the design words it rather than as "never returned".

// rowsFor is every per-document row the index holds for `rel`, by table.
//
// Only rows this note owns, found through its docmeta row. A note with no
// docmeta row owns nothing findable — its old rows are keyed to an id nothing
// maps back to a path, which is why `orphanRows` below is a separate question
// and not a fallback here. Guessing an id when docmeta has none reads a
// neighbour's rows as this note's.
func rowsFor(t *testing.T, x *Index, rel string) map[string]int {
	t.Helper()
	out := map[string]int{}
	var id int64
	err := x.db.QueryRow(`SELECT id FROM docmeta WHERE path = ?`, rel).Scan(&id)
	if errors.Is(err, sql.ErrNoRows) {
		return out
	}
	if err != nil {
		t.Fatalf("reading docmeta for %s: %v", rel, err)
	}
	out["docmeta"] = 1
	for _, tb := range perDocumentTables {
		var n int
		if err := x.db.QueryRow(
			`SELECT count(*) FROM `+tb.table+` WHERE `+tb.key+` = ?`, id).Scan(&n); err != nil {
			t.Fatalf("counting %s: %v", tb.table, err)
		}
		if n > 0 {
			out[tb.table] = n
		}
	}
	return out
}

// orphanRows is every row, by table, keyed to a document the index no longer
// holds. This is where a walled note's text went: `Delete` dropped the docmeta
// row, so nothing could name the note again, and the rows stayed.
func orphanRows(t *testing.T, x *Index) map[string]int {
	t.Helper()
	out := map[string]int{}
	for _, tb := range perDocumentTables {
		var n int
		if err := x.db.QueryRow(
			`SELECT count(*) FROM ` + tb.table +
				` WHERE ` + tb.key + ` NOT IN (SELECT id FROM docmeta)`).Scan(&n); err != nil {
			t.Fatalf("counting orphaned %s: %v", tb.table, err)
		}
		if n > 0 {
			out[tb.table] = n
		}
	}
	return out
}

// mustHoldNothing fails when the walled note still owns a row, or when any row
// survives keyed to a document that is gone. Both halves are needed: the first
// alone passes on an index that dropped the docmeta row and kept the body, which
// is the state this found.
func mustHoldNothing(t *testing.T, x *Index, rel, door string) {
	t.Helper()
	if held := rowsFor(t, x, rel); len(held) != 0 {
		t.Errorf("%s: the walled note still has rows in the index: %v. "+
			"The contract says the area is never indexed; a row in `chunks` is its "+
			"body and a row in `entities` is a token out of it.", door, held)
	}
	if held := orphanRows(t, x); len(held) != 0 {
		t.Errorf("%s: %v row(s) survive keyed to a document the index no longer holds — "+
			"the walled note's text with nothing left to name it by.", door, held)
	}
}

// walledBody is a walled note written so that every derived table has something
// to hold: two sections for `chunks`, a link for `links`, and a hex run for
// `entities` — which is what a recovery code looks like to the extractor.
const walledBody = "---\ntitle: Codes\n---\n\n# Codes\n\n" +
	"The recovery codes are a1b2c3d4e5f6 and 111111.\n\n" +
	"## Where they came from\n\nSee [[Marriage License]].\n"

// writeWalled writes walledBody at `rel` and proves the fixture would otherwise
// fill every table, so a later assertion that they are empty is about the wall
// and not about a note with nothing in it.
func writeWalled(t *testing.T, x *Index, rel string) {
	t.Helper()
	writeNote(t, x.vault, rel, walledBody)

	probe := newTestIndex(t)
	writeNote(t, probe.vault, "memory/probe.md", walledBody)
	if err := probe.IndexFile("memory/probe.md"); err != nil {
		t.Fatalf("the fixture probe: %v", err)
	}
	held := rowsFor(t, probe, "memory/probe.md")
	for _, want := range []string{"docmeta", "docs", "chunks", "links", "entities"} {
		if held[want] == 0 {
			t.Fatalf("this fixture writes no %s row even unwalled (%v), so a test that "+
				"finds none proves nothing; extend walledBody", want, held)
		}
	}
	// And the token the row assertions look for really is extracted, rather than
	// being a string that was never going to appear.
	assertRowsMention(t, probe, "a1b2c3d4e5f6")
}

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

	const walled = "personal/Home/Important Docs/Recovery Codes.md"
	writeWalled(t, x, walled)
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
		if r.Path == walled {
			t.Fatal("a walled note was served")
		}
	}
	got := resultPaths(out.Results)
	if len(got) != 2 {
		t.Fatalf("expected the two unwalled notes, got %v", got)
	}
	mustHoldNothing(t, x, walled, "the walk")

	// The text itself, asked of the tables rather than of a ranked read. The
	// extractor turns a hex run into an entity, which is how a recovery code
	// reaches a table a query does not join.
	assertNoRowMentions(t, x, "a1b2c3d4e5f6")
}

// mentions counts the rows carrying `needle` in the two tables no ranked read
// joins — the ones a walled note's text reached. Counts only; the row is never
// read back.
func mentions(t *testing.T, x *Index, needle string) (chunks, entities int) {
	t.Helper()
	if err := x.db.QueryRow(
		`SELECT count(*) FROM chunks WHERE content LIKE '%' || ? || '%'`, needle).Scan(&chunks); err != nil {
		t.Fatalf("counting chunk rows: %v", err)
	}
	if err := x.db.QueryRow(
		`SELECT count(*) FROM entities WHERE entity_uri LIKE '%' || ? || '%'`, needle).Scan(&entities); err != nil {
		t.Fatalf("counting entity rows: %v", err)
	}
	return chunks, entities
}

func assertNoRowMentions(t *testing.T, x *Index, needle string) {
	t.Helper()
	chunks, entities := mentions(t, x, needle)
	if chunks != 0 {
		t.Errorf("%d chunk row(s) still hold the walled note's text", chunks)
	}
	if entities != 0 {
		t.Errorf("%d entity row(s) still name a token out of the walled note", entities)
	}
}

// assertRowsMention is the same reading on an unwalled index, so the assertion
// above is known to be capable of failing.
func assertRowsMention(t *testing.T, x *Index, needle string) {
	t.Helper()
	if chunks, entities := mentions(t, x, needle); chunks == 0 || entities == 0 {
		t.Fatalf("the fixture's token reaches %d chunk row(s) and %d entity row(s) "+
			"even unwalled, so finding none proves nothing", chunks, entities)
	}
}

// Naming an area in the contract has to remove what is already indexed, not
// merely stop adding to it — otherwise the wall does nothing on the only
// machine that matters, where the notes were indexed years ago.
func TestNamingAnAreaRemovesWhatWasAlreadyIndexed(t *testing.T) {
	x := newTestIndex(t)
	const walled = "personal/Home/Important Docs/Recovery Codes.md"
	writeWalled(t, x, walled)

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

	// This is the path the live index actually took — indexed for years, then
	// walled — and the one that left 27,891 chunk rows behind. The note is gone
	// from every ranked read either way; what this asks is whether its text left.
	mustHoldNothing(t, x, walled, "a wall named over an indexed note")
	assertNoRowMentions(t, x, "a1b2c3d4e5f6")
}

// The notifier calls IndexFile directly, so the walk's refusal is not the only
// door.
func TestIndexFileRefusesAWalledPath(t *testing.T) {
	x := newTestIndex(t)
	withWall(t, []string{"personal/Home/Important Docs"})
	rel := "personal/Home/Important Docs/Marriage License.md"
	writeWalled(t, x, rel)

	if err := x.IndexFile(rel); err != nil {
		t.Fatalf("IndexFile: %v", err)
	}
	out, err := x.Search(Query{Text: "recovery", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) != 0 {
		t.Errorf("IndexFile indexed a walled path: %v", resultPaths(out.Results))
	}
	mustHoldNothing(t, x, rel, "the notifier's door")
	assertNoRowMentions(t, x, "a1b2c3d4e5f6")
}

// The door that closes after the fact: a note indexed normally, then saved
// again once the area is walled. `IndexFile` answers this call with a delete,
// so what it clears is what the wall clears.
func TestTheNotifiersDoorRemovesAnAlreadyIndexedWalledNote(t *testing.T) {
	x := newTestIndex(t)
	rel := "personal/Home/Important Docs/Recovery Codes.md"
	writeWalled(t, x, rel)
	if err := x.IndexFile(rel); err != nil {
		t.Fatalf("indexing it unwalled: %v", err)
	}
	if held := rowsFor(t, x, rel); held["chunks"] == 0 {
		t.Fatalf("the fixture never indexed: %v", held)
	}

	withWall(t, []string{"personal/Home/Important Docs"})
	if err := x.IndexFile(rel); err != nil {
		t.Fatalf("IndexFile after the wall: %v", err)
	}
	mustHoldNothing(t, x, rel, "a save inside a newly walled area")
	assertNoRowMentions(t, x, "a1b2c3d4e5f6")
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
