package index

import (
	"fmt"
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// "Every index is a deletable cache" is the design's load-bearing claim, and it
// is what makes a drifted cache cost a rebuild rather than data. This file
// tests the property once over all three derived tables rather than three times
// inside three implementations — asserting it per index tests the code that was
// written; asserting it here tests the claim.

// corpus is a small vault with the shape each table needs: sections to chunk,
// links in both forms including one that resolves nowhere, and entity references
// of every form.
func corpus() []note.Note {
	return []note.Note{
		{
			Rel:   "memory/semantic/capture.md",
			Title: "Capture",
			Body: "# What it does\n\nWrites the file and updates the index.\n\n" +
				"## Why it is synchronous\n\nSee [[storage-rules]] and " +
				"[the design](wiki/designs/filing.md). Fixed in alexherrero/agentm#466.\n",
		},
		{
			Rel:   "standards/storage-rules.md",
			Title: "Storage rules",
			Body: "# The block\n\nThe machine-readable core.\n\n" +
				"Landed as 8296fc5. See [[capture|the capture path]] and [[missing-note]].\n",
		},
		{
			Rel:   "wiki/designs/filing.md",
			Title: "Filing",
			Body:  "Plain prose with no structure at all, and no references.\n",
		},
	}
}

func seed(t *testing.T, idx *Index) {
	t.Helper()
	// Twice: link resolution is point-in-time, so a link written before its
	// target was indexed resolves on the second pass. Seeding once and asserting
	// on the result would bake a half-resolved graph into the expectation.
	for pass := 0; pass < 2; pass++ {
		for i, n := range corpus() {
			if err := idx.Upsert(n, int64(i+1), int64(len(n.Body))); err != nil {
				t.Fatalf("pass %d, %s: %v", pass, n.Rel, err)
			}
		}
	}
}

type snapshot struct {
	chunks   map[string][]Chunk
	links    map[string][]Link
	entities map[string][]string
}

func take(t *testing.T, idx *Index) snapshot {
	t.Helper()
	s := snapshot{
		chunks:   map[string][]Chunk{},
		links:    map[string][]Link{},
		entities: map[string][]string{},
	}
	for _, n := range corpus() {
		id, err := idx.docID(n.Rel)
		if err != nil {
			t.Fatalf("resolving %s: %v", n.Rel, err)
		}
		if s.chunks[n.Rel], err = idx.ChunksFor(id); err != nil {
			t.Fatal(err)
		}
		if s.links[n.Rel], err = idx.LinksFrom(id); err != nil {
			t.Fatal(err)
		}
	}
	for _, uri := range []string{"issue:alexherrero/agentm#466", "commit:8296fc5"} {
		paths, err := idx.NotesMentioning(uri)
		if err != nil {
			t.Fatal(err)
		}
		s.entities[uri] = paths
	}
	return s
}

func compare(t *testing.T, before, after snapshot) {
	t.Helper()
	for rel, want := range before.chunks {
		got := after.chunks[rel]
		if len(got) != len(want) {
			t.Errorf("%s: %d chunks after rebuild, %d before", rel, len(got), len(want))
			continue
		}
		for i := range want {
			if got[i] != want[i] {
				t.Errorf("%s chunk %d differs after rebuild:\n got %+v\nwant %+v",
					rel, i, got[i], want[i])
			}
		}
	}
	for rel, want := range before.links {
		got := after.links[rel]
		if len(got) != len(want) {
			t.Errorf("%s: %d links after rebuild, %d before", rel, len(got), len(want))
			continue
		}
		for i := range want {
			if got[i] != want[i] {
				t.Errorf("%s link %d differs after rebuild:\n got %+v\nwant %+v",
					rel, i, got[i], want[i])
			}
		}
	}
	for uri, want := range before.entities {
		got := after.entities[uri]
		if fmt.Sprint(got) != fmt.Sprint(want) {
			t.Errorf("%s: %v after rebuild, %v before", uri, got, want)
		}
	}
}

// The property, stated once: delete every derived table and rebuild from the
// notes; what comes back is what was there.
func TestEveryDerivedIndexRebuildsIdentically(t *testing.T) {
	idx := openScratch(t)
	seed(t, idx)
	before := take(t, idx)

	if len(before.chunks["memory/semantic/capture.md"]) == 0 {
		t.Fatal("the fixture produced no chunks, so this test would pass on an " +
			"index that rebuilt nothing")
	}
	if len(before.links["memory/semantic/capture.md"]) == 0 {
		t.Fatal("the fixture produced no links; the comparison below would be vacuous")
	}
	if len(before.entities["issue:alexherrero/agentm#466"]) == 0 {
		t.Fatal("the fixture produced no entity rows; the comparison below would be vacuous")
	}

	for _, table := range []string{"chunks", "links", "entities"} {
		if _, err := idx.db.Exec(`DELETE FROM ` + table); err != nil {
			t.Fatalf("clearing %s: %v", table, err)
		}
	}
	// Nothing survives the delete — otherwise the comparison would be measuring
	// what was left rather than what was rebuilt.
	for _, count := range []func() (int, error){idx.CountChunks, idx.CountLinks, idx.CountEntities} {
		n, err := count()
		if err != nil {
			t.Fatal(err)
		}
		if n != 0 {
			t.Fatalf("a derived table still holds %d rows after being cleared", n)
		}
	}

	seed(t, idx)
	compare(t, before, take(t, idx))
}

// A corrupted row must be repaired by the rebuild rather than survive it. This
// is the difference between a cache and a store: a store would keep the damage.
func TestARebuildRepairsACorruptedRow(t *testing.T) {
	idx := openScratch(t)
	seed(t, idx)
	before := take(t, idx)

	if _, err := idx.db.Exec(`UPDATE chunks SET content = 'corrupted', header_path = 'wrong'`); err != nil {
		t.Fatal(err)
	}
	if _, err := idx.db.Exec(`UPDATE links SET resolved = 'nowhere.md'`); err != nil {
		t.Fatal(err)
	}

	damaged := take(t, idx)
	if fmt.Sprint(damaged.chunks) == fmt.Sprint(before.chunks) {
		t.Fatal("the corruption did not take, so the repair below proves nothing")
	}

	seed(t, idx)
	compare(t, before, take(t, idx))
}

// Reindexing an unchanged corpus must not accumulate rows. An index that grew on
// every pass would be a slow leak that only showed up as ranking drift.
func TestRepeatedIndexingDoesNotAccumulate(t *testing.T) {
	idx := openScratch(t)
	seed(t, idx)

	chunks, _ := idx.CountChunks()
	links, _ := idx.CountLinks()
	entities, _ := idx.CountEntities()

	for i := 0; i < 3; i++ {
		seed(t, idx)
	}

	for _, c := range []struct {
		name string
		want int
		got  func() (int, error)
	}{
		{"chunks", chunks, idx.CountChunks},
		{"links", links, idx.CountLinks},
		{"entities", entities, idx.CountEntities},
	} {
		n, err := c.got()
		if err != nil {
			t.Fatal(err)
		}
		if n != c.want {
			t.Errorf("%s grew from %d to %d over repeated indexing", c.name, c.want, n)
		}
	}
}

// The backward direction is the reason the link table exists: answering "what
// points at this note" from the files means reading every file.
func TestBacklinksAnswerTheBackwardDirection(t *testing.T) {
	idx := openScratch(t)
	seed(t, idx)

	got, err := idx.Backlinks("memory/semantic/capture.md")
	if err != nil {
		t.Fatal(err)
	}
	if len(got) == 0 {
		t.Fatal("storage-rules links to capture and no backlink was recorded")
	}
	if got[0].Text != "the capture path" {
		t.Errorf("backlink text %q; the display text is what makes a backlink readable", got[0].Text)
	}
	if got[0].Context == "" {
		t.Error("backlink carries no context; a bare edge says two notes are " +
			"connected and nothing about how")
	}
}

// A dangling link is a fact about the corpus, and it is what the stub synthesis
// in a later part reads.
func TestAnUnresolvedLinkIsRecordedNotDropped(t *testing.T) {
	idx := openScratch(t)
	seed(t, idx)

	dangling, err := idx.DanglingLinks()
	if err != nil {
		t.Fatal(err)
	}
	var found bool
	for _, d := range dangling {
		if d == "missing-note" {
			found = true
		}
	}
	if !found {
		t.Errorf("the link to a note that does not exist was dropped: %v", dangling)
	}
}
