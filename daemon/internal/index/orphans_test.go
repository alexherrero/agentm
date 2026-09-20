package index

import (
	"reflect"
	"sort"
	"testing"
)

// A deleted document leaves nothing behind, in any table.
//
// `Delete` cleared `docs`, `embeddings` and `docmeta`, and the three derived
// tables that arrived after it was written — `chunks`, `links`, `entities` —
// kept their rows. Counted on the operator's index on 2026-09-19: 27,891 chunk
// rows, 11,898 entity rows and 8,145 link rows belonging to 4,209 documents
// with no docmeta row, among them every note the recall wall had removed. A
// chunk row is the note's text; an entity row can be a token lifted out of it
// (`commit:<hex>` is any hex-looking word, which is what a recovery code looks
// like); a link row carries the sentence around the link.
//
// These tests read the schema rather than a list of tables. A list kept in the
// test would be a second copy of the one in the code, and the defect was
// precisely a table missing from the code's list.

// notPerDocument is every table whose row count says nothing about one
// document, with the reason. Anything else in the schema has to return to its
// count from before a note was indexed once that note is deleted.
var notPerDocument = map[string]string{
	"meta":            "the schema version",
	"sqlite_sequence": "SQLite's AUTOINCREMENT counter: a number, and the reason an id is never reused",
	"docs_data":       "FTS5's segment store, where a delete is a tombstone until the next merge",
	"docs_idx":        "FTS5's segment index",
	"docs_config":     "FTS5's own version row",
}

// tableCounts is the row count of every per-document table the schema holds.
func tableCounts(t *testing.T, x *Index) map[string]int {
	t.Helper()
	rows, err := x.db.Query(`SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name`)
	if err != nil {
		t.Fatalf("reading the schema: %v", err)
	}
	var names []string
	for rows.Next() {
		var name string
		if err := rows.Scan(&name); err != nil {
			t.Fatal(err)
		}
		if _, skip := notPerDocument[name]; !skip {
			names = append(names, name)
		}
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}

	out := make(map[string]int, len(names))
	for _, name := range names {
		var n int
		if err := x.db.QueryRow(`SELECT count(*) FROM "` + name + `"`).Scan(&n); err != nil {
			t.Fatalf("counting %s: %v", name, err)
		}
		out[name] = n
	}
	return out
}

// leakyBody writes to every derived table: two sections for `chunks`, a wikilink
// and a markdown link for `links`, and an issue and a hex token for `entities`.
// The hex token is the point of the fixture — it is what a recovery code looks
// like to the entity extractor.
const leakyBody = "# Codes\n\n" +
	"The backup key is a1b2c3d4e5f6, filed under alexherrero/agentm#123.\n\n" +
	"## Where\n\n" +
	"See [[Marriage License]] and [the scan](scan.md).\n"

// indexLeaky indexes one note that writes to every per-document table, vector
// included, and returns its id.
func indexLeaky(t *testing.T, x *Index, rel string) int64 {
	t.Helper()
	addNote(t, x, rel, "Codes", leakyBody)
	id := docID(t, x, rel)
	if _, err := x.PutVectors("m", []VectorRow{
		{DocID: id, ChunkIdx: 0, MtimeNS: 1, Vec: unit(1, 0, 0)},
		{DocID: id, ChunkIdx: 1, MtimeNS: 1, Vec: unit(0, 1, 0)},
	}); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}
	return id
}

// mustHaveWrittenEverywhere fails when the fixture left a per-document table
// untouched. Without it a table added later would pass every test below
// vacuously: nothing written, so nothing left behind.
func mustHaveWrittenEverywhere(t *testing.T, before, after map[string]int) {
	t.Helper()
	for table, n := range after {
		if n <= before[table] {
			t.Fatalf("the fixture wrote nothing to %s, so this test says nothing about it; "+
				"extend leakyBody (or name the table in notPerDocument, with the reason)", table)
		}
	}
}

func TestADeletedDocumentLeavesNoRowInAnyTable(t *testing.T) {
	x := newTestIndex(t)
	// A neighbour with rows in every table, which must all survive.
	keeper := indexLeaky(t, x, "memory/keeper.md")
	before := tableCounts(t, x)

	indexLeaky(t, x, "memory/gone.md")
	mustHaveWrittenEverywhere(t, before, tableCounts(t, x))

	if err := x.Delete("memory/gone.md"); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	after := tableCounts(t, x)
	for table, want := range before {
		if after[table] != want {
			t.Errorf("%s holds %d row(s) after the delete, want the %d it held before the note existed",
				table, after[table], want)
		}
	}

	// Equal counts could hide the wrong rows going. The neighbour's are still
	// served, and it is the only note the entity still names.
	chunks, err := x.ChunksFor(keeper)
	if err != nil || len(chunks) == 0 {
		t.Errorf("the surviving note lost its chunks (%d rows, err %v)", len(chunks), err)
	}
	mentions, err := x.NotesMentioning("commit:a1b2c3d4e5f6")
	if err != nil {
		t.Fatalf("NotesMentioning: %v", err)
	}
	if !reflect.DeepEqual(mentions, []string{"memory/keeper.md"}) {
		t.Errorf("the entity is mentioned by %v, want only the surviving note", mentions)
	}
}

// `resolved` is "the vault path the target refers to, or empty when nothing in
// the corpus matches". Once the target is deleted nothing matches, and a row
// still naming it is the index claiming a note it no longer holds — 536 live
// link rows on the operator's index, two of them naming a walled path.
func TestALinkToADeletedNoteGoesDanglingAndComesBack(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/target.md", "target", "The thing itself.")
	addNote(t, x, "memory/source.md", "source", "See [[target]] for the rest.")

	back, err := x.Backlinks("memory/target.md")
	if err != nil || len(back) != 1 {
		t.Fatalf("the fixture never linked: %d backlink(s), err %v", len(back), err)
	}

	if err := x.Delete("memory/target.md"); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	back, err = x.Backlinks("memory/target.md")
	if err != nil {
		t.Fatalf("Backlinks: %v", err)
	}
	if len(back) != 0 {
		t.Errorf("%d link row(s) still resolve to a note the index no longer holds", len(back))
	}
	dangling, err := x.DanglingLinks()
	if err != nil {
		t.Fatalf("DanglingLinks: %v", err)
	}
	if !reflect.DeepEqual(dangling, []string{"target"}) {
		t.Errorf("dangling links are %v, want the one whose target went", dangling)
	}

	// A note deleted and written again — a sync client moving it into place —
	// gets its backlinks back on the next pass, from the row that was kept.
	addNote(t, x, "memory/target.md", "target", "The thing itself, again.")
	if _, err := x.ResolveDangling(t.Context()); err != nil {
		t.Fatalf("ResolveDangling: %v", err)
	}
	if back, _ = x.Backlinks("memory/target.md"); len(back) != 1 {
		t.Errorf("the link did not resolve again once its target returned: %d backlink(s)", len(back))
	}
}

// orphan puts the index in the state every index built before the fix is in:
// the docmeta row went and nothing else did.
func orphan(t *testing.T, x *Index, id int64) {
	t.Helper()
	if _, err := x.db.Exec(`DELETE FROM docmeta WHERE id = ?`, id); err != nil {
		t.Fatalf("seeding the orphans: %v", err)
	}
}

func TestTheSweepRemovesWhatADeletedDocumentLeftBehind(t *testing.T) {
	x := newTestIndex(t)
	indexLeaky(t, x, "memory/keeper.md")
	clean := tableCounts(t, x)

	gone := indexLeaky(t, x, "memory/gone.md")
	mustHaveWrittenEverywhere(t, clean, tableCounts(t, x))
	orphan(t, x, gone)
	dirty := tableCounts(t, x)

	rep, err := x.SweepOrphans()
	if err != nil {
		t.Fatalf("SweepOrphans: %v", err)
	}
	after := tableCounts(t, x)
	for table, want := range clean {
		if after[table] != want {
			t.Errorf("%s holds %d row(s) after the sweep, want %d", table, after[table], want)
		}
	}

	// The report is what the operator reads, so it has to be the truth. Checked
	// against counts taken around the sweep rather than against the sweep's own
	// arithmetic.
	var named []string
	for table, removed := range rep.Removed {
		named = append(named, table)
		if want := dirty[table] - after[table]; removed != want {
			t.Errorf("the sweep reports %d row(s) removed from %s; the table lost %d", removed, table, want)
		}
	}
	sort.Strings(named)
	if want := []string{"chunks", "docs", "embeddings", "entities", "links"}; !reflect.DeepEqual(named, want) {
		t.Errorf("the sweep reported on %v, want %v", named, want)
	}
	if rep.Total() == 0 {
		t.Error("the sweep removed rows and reports a total of zero")
	}

	// Idempotent: what it removed is gone, so a second run has nothing to do.
	again, err := x.SweepOrphans()
	if err != nil {
		t.Fatalf("second sweep: %v", err)
	}
	if again.Total() != 0 || again.Unresolved != 0 {
		t.Errorf("a second sweep still found work: %s", again)
	}
}

// A sweep that fails reports nothing, because nothing happened.
//
// The deletes run inside a transaction with a deferred rollback, so any error
// after the first statement — a commit that fails, a second process holding the
// write lock, a disk error — undoes all of them. A report filled in as the
// statements ran would credit the sweep with rows still sitting in their
// tables, and the daemon logs that number every five minutes.
func TestAFailedSweepReportsNothingRemoved(t *testing.T) {
	x := newTestIndex(t)
	indexLeaky(t, x, "memory/keeper.md")
	orphan(t, x, indexLeaky(t, x, "memory/gone.md"))
	before := tableCounts(t, x)

	// A statement mid-transaction fails while the ones before it succeed —
	// which is the shape of every real cause.
	if _, err := x.db.Exec(
		`CREATE TRIGGER no_link_delete BEFORE DELETE ON links
		 BEGIN SELECT RAISE(ABORT, 'simulated failure'); END`); err != nil {
		t.Fatal(err)
	}

	rep, err := x.SweepOrphans()
	if err == nil {
		t.Fatal("the sweep was supposed to fail")
	}
	if after := tableCounts(t, x); !reflect.DeepEqual(after, before) {
		t.Fatalf("the transaction did not roll back:\nbefore %v\nafter  %v", before, after)
	}
	if rep.Total() != 0 || rep.Unresolved != 0 {
		t.Errorf("the sweep reports %q, and every one of those rows is still in its table",
			rep)
	}
}

func TestTheSweepLeavesACleanIndexAlone(t *testing.T) {
	x := newTestIndex(t)
	indexLeaky(t, x, "memory/one.md")
	indexLeaky(t, x, "memory/two.md")
	addNote(t, x, "memory/Marriage License.md", "Marriage License", "A licence.")
	// Resolved once the target exists, so the index holds both kinds of link row
	// the sweep must not touch: one naming a live note, one naming nothing.
	if _, err := x.ResolveDangling(t.Context()); err != nil {
		t.Fatalf("ResolveDangling: %v", err)
	}
	before := tableCounts(t, x)
	resolvedBefore := resolvedLinks(t, x)
	if resolvedBefore == 0 {
		t.Fatal("the fixture holds no resolved link, so the sweep had nothing to leave alone")
	}

	rep, err := x.SweepOrphans()
	if err != nil {
		t.Fatalf("SweepOrphans: %v", err)
	}
	if rep.Total() != 0 || rep.Unresolved != 0 {
		t.Errorf("the sweep found work on a clean index: %s", rep)
	}
	if after := tableCounts(t, x); !reflect.DeepEqual(after, before) {
		t.Errorf("the sweep changed a clean index:\nbefore %v\nafter  %v", before, after)
	}
	if got := resolvedLinks(t, x); got != resolvedBefore {
		t.Errorf("%d link(s) resolved after the sweep, %d before", got, resolvedBefore)
	}
}

func resolvedLinks(t *testing.T, x *Index) int {
	t.Helper()
	var n int
	if err := x.db.QueryRow(`SELECT count(*) FROM links WHERE resolved != ''`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

// The rows an index already carries: a link that resolved to a path which then
// left the index under the old Delete, which never looked at `resolved`.
func TestTheSweepUnresolvesALinkToAPathTheIndexNoLongerHolds(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/target.md", "target", "The thing itself.")
	addNote(t, x, "memory/source.md", "source", "See [[target]] for the rest.")
	orphan(t, x, docID(t, x, "memory/target.md"))

	rep, err := x.SweepOrphans()
	if err != nil {
		t.Fatalf("SweepOrphans: %v", err)
	}
	if rep.Unresolved != 1 {
		t.Errorf("the sweep un-resolved %d link(s), want the one whose target is gone", rep.Unresolved)
	}
	if back, _ := x.Backlinks("memory/target.md"); len(back) != 0 {
		t.Errorf("%d link row(s) still resolve to the vanished path", len(back))
	}
	// The source note is live, and its row is a fact about it: kept, dangling.
	links, err := x.LinksFrom(docID(t, x, "memory/source.md"))
	if err != nil || len(links) != 1 || links[0].Resolved != "" {
		t.Errorf("the live note's link row should survive, dangling; got %+v (err %v)", links, err)
	}
}

// `agentmd reindex` and the daemon's own passes are both Reconcile, so this is
// what makes the sweep something that happens rather than something one can run.
func TestReconcileSweepsAndSaysWhatItRemoved(t *testing.T) {
	x := newTestIndex(t)
	writeNote(t, x.vault, "memory/keeper.md", "---\ntitle: Keeper\n---\n\n"+leakyBody)
	writeNote(t, x.vault, "memory/gone.md", "---\ntitle: Gone\n---\n\n"+leakyBody)
	if _, err := x.Reconcile(); err != nil {
		t.Fatalf("first reconcile: %v", err)
	}
	clean := tableCounts(t, x)

	// The file stays on disk and only its docmeta row goes, so the pass re-adds
	// the note under a fresh id and the old id's rows are the orphans. Counts
	// return to `clean` only if they were swept.
	orphan(t, x, docID(t, x, "memory/gone.md"))
	rep, err := x.Reconcile()
	if err != nil {
		t.Fatalf("second reconcile: %v", err)
	}
	if len(rep.Errors) != 0 {
		t.Fatalf("reconcile errors: %v", rep.Errors)
	}
	for _, table := range []string{"chunks", "links", "entities"} {
		if rep.Swept.Removed[table] == 0 {
			t.Errorf("the pass reports no %s row swept; it left the old id's rows behind: %s",
				table, rep.Swept)
		}
	}
	if after := tableCounts(t, x); !reflect.DeepEqual(after, clean) {
		t.Errorf("the index did not return to its clean counts:\nclean %v\nafter %v", clean, after)
	}

	// And a pass over an index with nothing to sweep says so.
	rep, err = x.Reconcile()
	if err != nil {
		t.Fatalf("third reconcile: %v", err)
	}
	if rep.Swept.Total() != 0 {
		t.Errorf("a clean pass swept %s", rep.Swept)
	}
}

// The embedder works on a batch for seconds to minutes, and a note can leave the
// index in that time — deleted, or walled by a contract edit. Its vectors must
// not arrive after it has gone.
func TestPutVectorsRefusesADocumentThatIsGone(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/stays.md", "stays", "body")
	addNote(t, x, "memory/goes.md", "goes", "body")
	stays, goes := docID(t, x, "memory/stays.md"), docID(t, x, "memory/goes.md")
	if err := x.Delete("memory/goes.md"); err != nil {
		t.Fatalf("Delete: %v", err)
	}

	if _, err := x.PutVectors("m", []VectorRow{
		{DocID: goes, MtimeNS: 1, Vec: unit(1, 0, 0)},
		{DocID: stays, MtimeNS: 1, Vec: unit(0, 1, 0)},
	}); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}
	var forGone, forLive int
	if err := x.db.QueryRow(`SELECT count(*) FROM embeddings WHERE doc_id = ?`, goes).Scan(&forGone); err != nil {
		t.Fatal(err)
	}
	if err := x.db.QueryRow(`SELECT count(*) FROM embeddings WHERE doc_id = ?`, stays).Scan(&forLive); err != nil {
		t.Fatal(err)
	}
	if forGone != 0 {
		t.Errorf("%d vector(s) stored for a document that no longer exists", forGone)
	}
	if forLive != 1 {
		t.Errorf("the live document in the same batch has %d vector(s), want 1", forLive)
	}
}

// And it says which documents it refused, once per document rather than once
// per chunk. A caller that counts what it embedded has no other way to learn
// that a note it handed over was not stored, and the backfill reports both a
// note count and a chunk count.
func TestPutVectorsNamesTheDocumentsItRefused(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/stays.md", "stays", "body")
	addNote(t, x, "memory/goes.md", "goes", "body")
	stays, goes := docID(t, x, "memory/stays.md"), docID(t, x, "memory/goes.md")
	if err := x.Delete("memory/goes.md"); err != nil {
		t.Fatalf("Delete: %v", err)
	}

	vanished, err := x.PutVectors("m", []VectorRow{
		{DocID: goes, ChunkIdx: 0, MtimeNS: 1, Vec: unit(1, 0, 0)},
		{DocID: goes, ChunkIdx: 1, MtimeNS: 1, Vec: unit(0, 1, 0)},
		{DocID: stays, ChunkIdx: 0, MtimeNS: 1, Vec: unit(0, 0, 1)},
	})
	if err != nil {
		t.Fatalf("PutVectors: %v", err)
	}
	if !reflect.DeepEqual(vanished, []int64{goes}) {
		t.Errorf("refused %v, want the one deleted document named once for its two chunks", vanished)
	}

	// A batch with nothing to refuse says so, rather than reporting the notes
	// it stored.
	vanished, err = x.PutVectors("m", []VectorRow{
		{DocID: stays, ChunkIdx: 0, MtimeNS: 2, Vec: unit(1, 0, 0)},
	})
	if err != nil {
		t.Fatalf("PutVectors: %v", err)
	}
	if len(vanished) != 0 {
		t.Errorf("a clean batch refused %v", vanished)
	}
}
