package index

import (
	"database/sql"
	"fmt"
	"path/filepath"
	"testing"
)

// The notes this rung exists for: a folder named for its subject holding an
// `_index.md` whose title is the word "index" and whose prose never repeats the
// folder's name. Twenty-three notes in the measured corpus have this shape, and
// until `path` was indexed the only column that said what they were about was
// the one FTS5 could not see.
func TestPathTokenFindsANoteItsTitleAndBodyNeverName(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "external/primos/_index.md", "index",
		"landing page for the family research; see the analysis folder for detail")
	addNote(t, x, "external/other/_index.md", "index",
		"landing page for something else entirely")

	out, err := x.Search(Query{Text: "primos", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	got := paths(out.Results)
	if len(got) != 1 || got[0] != "external/primos/_index.md" {
		t.Fatalf("a path-only term should retrieve exactly its own note, got %v", got)
	}
}

// The production shape is not a bare path term — it is an implicit AND over the
// terms a question reduces to, where one of them lives in the directory and the
// rest live in the prose. Before this rung that conjunction could not be
// satisfied by any note whose folder carried the distinguishing word, which is
// the whole of the defect.
func TestPathTokenSatisfiesTheImplicitAnd(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "external/primos/analysis/_summary.md", "summary",
		"where we kept the notes on the birth records")
	addNote(t, x, "external/other/analysis/_summary.md", "summary",
		"where we kept the notes on something else")

	out, err := x.Search(Query{Text: "kept notes primos", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	got := paths(out.Results)
	if len(got) != 1 || got[0] != "external/primos/analysis/_summary.md" {
		t.Fatalf("path term should satisfy one conjunct of an AND query, got %v", got)
	}
}

// The weight is deliberately below `title`, because a path is a title diluted by
// the structure around it. A note that says the word in its title must still beat
// a note that only lives in a folder named for it.
func TestPathRanksBelowTitle(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "archive/homelab/_index.md", "index",
		"a landing page whose prose says nothing about the subject")
	addNote(t, x, "notes/a.md", "homelab",
		"a landing page whose prose says nothing about the subject")

	out, err := x.Search(Query{Text: "homelab", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	got := paths(out.Results)
	if len(got) != 2 {
		t.Fatalf("both notes should match, got %v", got)
	}
	if got[0] != "notes/a.md" {
		t.Fatalf("a title match must outrank a path match, got %v", got)
	}
}

// Where no path carries the query's terms, nothing about the ranking changes:
// title still outweighs body by the margin it was measured at. This is the
// "otherwise unchanged" half of the rung, and it is the half that would break
// first if the new weight were applied to the wrong column.
func TestTitleStillOutranksBodyWhenNoPathMatches(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "notes/a.md", "homelab guide",
		"a page about the closet and the rack and the cabling in it")
	addNote(t, x, "notes/b.md", "unrelated page",
		"the homelab is mentioned here, and the homelab again, and once more the homelab")

	out, err := x.Search(Query{Text: "homelab", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	got := paths(out.Results)
	if len(got) != 2 {
		t.Fatalf("both notes should match, got %v", got)
	}
	if got[0] != "notes/a.md" {
		t.Fatalf("title should still outrank repeated body mentions, got %v", got)
	}
}

// A column's UNINDEXED-ness is fixed when the virtual table is created, and
// `CREATE VIRTUAL TABLE IF NOT EXISTS` is happy to leave an older table exactly
// as it found it. So the schema-version bump is not bookkeeping here — it is the
// only thing standing between this build and an index that silently keeps
// weighting a column that contributes nothing.
func TestAnIndexBuiltOnTheOldShapeIsDiscarded(t *testing.T) {
	dir := t.TempDir()
	dbPath := filepath.Join(dir, "index.db")

	db, err := sql.Open("sqlite", "file:"+dbPath)
	if err != nil {
		t.Fatalf("creating the old-shape index: %v", err)
	}
	for _, stmt := range []string{
		fmt.Sprintf(`CREATE VIRTUAL TABLE docs USING fts5(
			path UNINDEXED, title, meta, body, tokenize='%s')`, tokenizer),
		`CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)`,
		`INSERT INTO meta(key, value) VALUES('schema_version', '4')`,
	} {
		if _, err := db.Exec(stmt); err != nil {
			t.Fatalf("creating the old-shape index: %v", err)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatalf("closing the old-shape index: %v", err)
	}

	x, err := Open(dbPath, dir)
	if err != nil {
		t.Fatalf("opening: %v", err)
	}
	t.Cleanup(func() { x.Close() })

	addNote(t, x, "external/primos/_index.md", "index", "a landing page")
	out, err := x.Search(Query{Text: "primos", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if got := paths(out.Results); len(got) != 1 {
		t.Fatalf("the old-shape table survived the open, so the path is still "+
			"unsearchable; got %v", got)
	}
}
