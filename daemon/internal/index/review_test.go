package index

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

func indexScored(t *testing.T, idx *Index, rel, status string, conf string) {
	t.Helper()
	raw := "---\ntitle: A note\nstatus: " + status + "\n"
	if conf != "" {
		raw += "confidence: " + conf + "\n"
	}
	raw += "---\n\nThe staging gate runs first.\n"
	n := note.Parse(rel, raw, time.Now())
	if err := idx.Upsert(n, time.Now().UnixNano(), int64(len(raw))); err != nil {
		t.Fatalf("indexing %s: %v", rel, err)
	}
}

// The queue is a query. The `personal/_inbox/` directory it replaces reached
// 9,860 notes and nothing ever moved anything out of it, because a note in an
// inbox is somewhere else — out of search results, so nobody encounters it, so
// nobody triages it.
func TestTheReviewQueueIsAQueryOverUnfiledNotes(t *testing.T) {
	idx := openScratch(t)
	indexScored(t, idx, "Agent/memory/semantic/doubtful.md", "unfiled", "0.20")
	indexScored(t, idx, "Agent/memory/semantic/shaky.md", "unfiled", "0.45")
	indexScored(t, idx, "Agent/memory/semantic/settled.md", "active", "0.95")

	got, err := idx.ReviewQueue(context.Background(), 10)
	if err != nil {
		t.Fatalf("ReviewQueue: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("queue has %d items, want the 2 unfiled ones: %+v", len(got), got)
	}
	for _, it := range got {
		if strings.HasSuffix(it.Path, "settled.md") {
			t.Error("an active note is in the review queue")
		}
	}
	// And the queued notes are still findable, which is the whole difference
	// from an inbox.
	out, err := idx.Search(Query{Text: "staging gate", K: 5})
	if err != nil {
		t.Fatal(err)
	}
	if len(out.Results) != 3 {
		t.Errorf("search returned %d of 3 notes; a queued note that cannot be "+
			"found is an inbox with extra steps", len(out.Results))
	}
}

// Least confident first: the queue is a work list, and the note the system was
// least sure about is the one a person adds the most by looking at.
func TestTheQueueIsOrderedLeastConfidentFirst(t *testing.T) {
	idx := openScratch(t)
	indexScored(t, idx, "Agent/memory/semantic/c.md", "unfiled", "0.55")
	indexScored(t, idx, "Agent/memory/semantic/a.md", "unfiled", "0.10")
	indexScored(t, idx, "Agent/memory/semantic/b.md", "unfiled", "0.30")

	got, err := idx.ReviewQueue(context.Background(), 10)
	if err != nil {
		t.Fatal(err)
	}
	var order []string
	for _, it := range got {
		order = append(order, strings.TrimSuffix(it.Path[strings.LastIndexByte(
			it.Path, '/')+1:], ".md"))
	}
	if strings.Join(order, ",") != "a,b,c" {
		t.Errorf("queue order = %v, want a,b,c (least confident first)", order)
	}
}

// "Scored zero" and "never scored" are different facts, and conflating them puts
// every unreached note at the front of a list nobody can act on.
func TestAnUnscoredNoteSortsAfterAScoredOne(t *testing.T) {
	idx := openScratch(t)
	indexScored(t, idx, "Agent/memory/semantic/unreached.md", "unfiled", "")
	indexScored(t, idx, "Agent/memory/semantic/doubted.md", "unfiled", "0.10")

	got, err := idx.ReviewQueue(context.Background(), 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 {
		t.Fatalf("queue has %d items", len(got))
	}
	if !strings.HasSuffix(got[0].Path, "doubted.md") {
		t.Errorf("a note enrichment never reached outranks one it judged and "+
			"doubted: %+v", got)
	}
	if !got[0].Scored {
		t.Error("a scored note reports itself unscored")
	}
	if got[1].Scored {
		t.Error("a note with no confidence key reports itself scored")
	}
}

// A note that genuinely scored zero is scored, and sorts first.
func TestAZeroScoreIsAScore(t *testing.T) {
	idx := openScratch(t)
	indexScored(t, idx, "Agent/memory/semantic/zero.md", "unfiled", "0.00")
	indexScored(t, idx, "Agent/memory/semantic/none.md", "unfiled", "")

	got, err := idx.ReviewQueue(context.Background(), 10)
	if err != nil {
		t.Fatal(err)
	}
	if !got[0].Scored || !strings.HasSuffix(got[0].Path, "zero.md") {
		t.Errorf("a genuine zero was treated as absent: %+v", got)
	}
}

// There is no inbox directory, and nothing in this pass creates one.
func TestNoInboxDirectoryIsCreated(t *testing.T) {
	idx := openScratch(t)
	indexScored(t, idx, "Agent/memory/semantic/doubtful.md", "unfiled", "0.20")

	var paths []string
	rows, err := idx.db.Query(`SELECT path FROM docmeta`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	for rows.Next() {
		var p string
		if err := rows.Scan(&p); err != nil {
			t.Fatal(err)
		}
		paths = append(paths, p)
	}
	for _, p := range paths {
		if strings.Contains(strings.ToLower(p), "_inbox") {
			t.Errorf("a low-confidence note was filed into an inbox: %s", p)
		}
	}
}
