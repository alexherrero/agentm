package index

import (
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// A unit the ingest sweep has fetched and not yet promoted is raw external
// text sitting in the vault. Answering a question with one is answering with a
// web page somebody's link happened to point at.
//
// The Python recall path has never served it. This is the Go arm agreeing —
// in both arms, because a wall in one and not the other is the same
// disagreement one process further along.

func indexStaged(t *testing.T, idx *Index, rel, title, status, body string) {
	t.Helper()
	raw := "---\ntitle: " + title + "\nstatus: " + status + "\n---\n\n" + body
	n := note.Parse(rel, raw, time.Now())
	if err := idx.Upsert(n, time.Now().UnixNano(), int64(len(raw))); err != nil {
		t.Fatalf("indexing %s: %v", rel, err)
	}
}

func TestAStagedUnitIsWalledInBothArms(t *testing.T) {
	idx := openScratch(t)
	body := "The release gate waits for the checks to finish before the tag.\n"
	// Sorts first by path, so a tie would put it on top: its absence below is
	// the wall, not the tiebreak.
	indexStaged(t, idx, "Agent/memory/semantic/a-staged.md", "Gate", "ingest_staged", body)
	indexStaged(t, idx, "Agent/memory/semantic/b-active.md", "Gate", "active", body)

	for _, mode := range []string{ModeAnd, ModeFusion} {
		out, err := idx.Search(Query{Text: "release gate checks", K: 5, Mode: mode})
		if err != nil {
			t.Fatalf("%s: search: %v", mode, err)
		}
		if len(out.Results) != 1 || !strings.HasSuffix(out.Results[0].Path, "b-active.md") {
			t.Errorf("%s: a staged unit was served: %v", mode, resultPaths(out.Results))
		}
		if out.StagedHidden != 1 {
			t.Errorf("%s: the wall must count what it hid: staged_hidden=%d, want 1",
				mode, out.StagedHidden)
		}
		if out.Matched != 2 {
			t.Errorf("%s: the window saw both rows before the wall: matched=%d, want 2",
				mode, out.Matched)
		}
	}
}

// The wall lifts the same way every other walled class does — by asking for it
// by name. One rule, so a diagnostic query can still find what was staged.
func TestAStagedUnitComesBackOnTheExplicitQueryDemoted(t *testing.T) {
	idx := openScratch(t)
	body := "The release gate waits for the checks to finish before the tag.\n"
	indexStaged(t, idx, "Agent/memory/semantic/a-staged.md", "Gate", "ingest_staged", body)
	indexStaged(t, idx, "Agent/memory/semantic/b-active.md", "Gate", "active", body)

	for _, mode := range []string{ModeAnd, ModeFusion} {
		out, err := idx.Search(Query{
			Text: "release gate checks", K: 5, Mode: mode, IncludeArchived: true,
		})
		if err != nil {
			t.Fatalf("%s: explicit query: %v", mode, err)
		}
		if len(out.Results) != 2 {
			t.Fatalf("%s: the explicit query should see both: %v", mode, resultPaths(out.Results))
		}
		if !strings.HasSuffix(out.Results[0].Path, "b-active.md") {
			t.Errorf("%s: included, a staged unit is present and last, not restored to parity: %v",
				mode, resultPaths(out.Results))
		}
		if !strings.Contains(out.Results[1].Penalty, note.ClassIngestStaged) {
			t.Errorf("%s: the class is not visible on the row: penalty=%q",
				mode, out.Results[1].Penalty)
		}
		if out.StagedHidden != 0 {
			t.Errorf("%s: nothing hidden on the explicit query, got staged_hidden=%d",
				mode, out.StagedHidden)
		}
	}
}

// An ordinary note is untouched: the wall matches the sweep's status and
// nothing wider.
func TestTheStagedWallDoesNotCatchAnOrdinaryNote(t *testing.T) {
	idx := openScratch(t)
	for _, status := range []string{"active", "unfiled", "ingested"} {
		t.Run(status, func(t *testing.T) {
			idx := idx
			rel := "Agent/memory/semantic/" + status + ".md"
			indexStaged(t, idx, rel, "Gate "+status, status,
				"The release gate for "+status+" waits for the checks.\n")
			out, err := idx.Search(Query{Text: "release gate " + status, K: 5})
			if err != nil {
				t.Fatalf("search: %v", err)
			}
			if len(out.Results) == 0 {
				t.Fatalf("a %s note was walled", status)
			}
		})
	}
}

// The dense arm has its own call to the wall, and a wall in one arm and not
// the other is the same disagreement one process further along. Driven with
// hand-written vectors so the dense side genuinely runs: the staged unit is
// the better cosine match, so without the wall it takes rank 1 of that arm
// and wins the fusion.
func TestAStagedUnitIsWalledInsideTheDenseArm(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/clean.md", "clean", "the release gate body")
	addPenalizedNote(t, x, "memory/staged.md", "staged", "the release gate body",
		note.ClassIngestStaged)

	if err := x.PutVectors("m", []VectorRow{
		{DocID: docID(t, x, "memory/staged.md"), MtimeNS: 1, Vec: unit(1, 0, 0)},
		{DocID: docID(t, x, "memory/clean.md"), MtimeNS: 1, Vec: unit(0.99, 0.14, 0)},
	}); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}

	out, err := x.Search(Query{
		Text: "clean staged", K: 5, Mode: ModeHybrid, Vector: unit(1, 0, 0), EmbedModel: "m",
	})
	if err != nil {
		t.Fatalf("hybrid: %v", err)
	}
	for _, r := range out.Results {
		if r.Path == "memory/staged.md" {
			t.Fatalf("the dense arm served a staged unit: %v", resultPaths(out.Results))
		}
	}
	if out.StagedHidden == 0 {
		t.Error("the dense arm hid nothing; the wall did not run inside it")
	}
}
