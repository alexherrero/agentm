package index

import (
	"fmt"
	"strings"
	"testing"
)

// The snippet pass is priced in documents scanned, not rows returned, so the one
// invariant these tests exist to hold is that snippet() is called for the k rows
// a caller reads and for nothing else.
//
// It is asserted on the counter rather than on wall-clock time because the cost
// is a property of the corpus, not of the code: on a fixture of small notes an
// extra forty-five snippet() calls are free, and on the operator's 9,971-note
// corpus the same forty-five cost 6.4 seconds, because eleven of the notes they
// land on are 0.8–1.3 MB. A timing assertion would pass here and tell nobody
// anything; a count assertion fails the moment the shape regresses.
//
// The shape did regress once, which is why these are here. searchHybrid reads
// its lexical arm to rrfDepth (50) because RRF needs the ranks that deep, and
// the arm it called snippeted everything it ranked — so every hybrid query paid
// to scan fifty documents in order to show at most k. Ranking is flat in k (the
// over-fetch window is note.Overfetch either way), so all of it was decorative
// text for rows nobody would ever see.

// snippetFixture builds an index of n notes that all match probeQuery, so a
// search can rank far deeper than any k a caller would ask for.
func snippetFixture(tb testing.TB, n int) *Index {
	tb.Helper()
	x := newTestIndex(tb)
	for i := 0; i < n; i++ {
		addNote(tb, x,
			fmt.Sprintf("memory/n%03d.md", i),
			"homelab server",
			fmt.Sprintf("the homelab server note number %d", i))
	}
	return x
}

func TestSnippetsAreComputedOnlyForTheRowsReturned(t *testing.T) {
	// Deeper than rrfDepth, so a mode that snippets its whole fusion window is
	// distinguishable from one that snippets k.
	const corpus = 80

	for _, tc := range []struct {
		name string
		q    Query
	}{
		{"and", Query{Text: probeQuery, K: 5, Mode: ModeAnd}},
		{"fusion", Query{Text: probeQuery, K: 5, Mode: ModeFusion}},
		{"fusion lex3", Query{Text: probeQuery, K: 5, Mode: ModeFusion, Lex3: true}},
		// No vector: the degrade branch, which truncates the lexical arm to k and
		// must snippet what it returns rather than what it ranked.
		{"hybrid degraded", Query{Text: probeQuery, K: 5, Mode: ModeHybrid}},
		{"hybrid", Query{
			Text: probeQuery, K: 5, Mode: ModeHybrid,
			Vector: unit(1, 0, 0), EmbedModel: "m",
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			x := snippetFixture(t, corpus)
			if err := x.PutVectors("m", []VectorRow{
				{DocID: docID(t, x, "memory/n007.md"), MtimeNS: 1, Vec: unit(1, 0, 0)},
			}); err != nil {
				t.Fatalf("PutVectors: %v", err)
			}

			out, err := x.Search(tc.q)
			if err != nil {
				t.Fatalf("search: %v", err)
			}
			if len(out.Results) != tc.q.K {
				t.Fatalf("returned %d rows, want %d — the fixture is not deep enough to test what this claims",
					len(out.Results), tc.q.K)
			}
			if got := x.snippeted(); got != int64(tc.q.K) {
				t.Errorf("snippet() saw %d documents for a k=%d search, want %d",
					got, tc.q.K, tc.q.K)
			}
		})
	}
}

// The regression itself, stated as its own case: a hybrid search must not price
// its snippet pass at rrfDepth. Separate from the table above because this is
// the specific number that was wrong, and a future edit to the table's k should
// not quietly stop testing it.
func TestHybridDoesNotSnippetItsWholeFusionWindow(t *testing.T) {
	x := snippetFixture(t, 80)

	out, err := x.Search(Query{Text: probeQuery, K: 5, Mode: ModeHybrid})
	if err != nil {
		t.Fatalf("hybrid: %v", err)
	}
	if len(out.Results) != 5 {
		t.Fatalf("returned %d rows, want 5", len(out.Results))
	}
	if got := x.snippeted(); got >= rrfDepth {
		t.Fatalf("snippet() saw %d documents, which is the fusion depth (%d) rather "+
			"than the caller's k (5)", got, rrfDepth)
	}
}

// A row the dense arm promoted into the result set, which matched lexically but
// ranked below the fusion window, must still come back bare.
//
// This is the case a no-embedder capture cannot reach — with no query vector
// there is no dense arm, nothing is promoted, and the whole question is
// invisible. It is pinned because the ranking split makes it easy to get wrong
// in the generous direction: fusionRanked's wonBy covers every candidate the
// subset sweep considered, so snippeting straight from it would start
// highlighting rows that used to come back bare. That is a change to what gets
// injected into a prompt, and it is not this fix's to make.
func TestDenseArmPromotionDoesNotWidenSnippetCoverage(t *testing.T) {
	x := newTestIndex(t)
	// Deep enough that the deliberately-worst lexical match falls outside the
	// fusion window the hybrid arm reads.
	for i := 0; i < 80; i++ {
		addNote(t, x, fmt.Sprintf("memory/n%03d.md", i), "homelab server",
			"the homelab server note "+fmt.Sprint(i))
	}
	// One note that matches both terms, so it is a lexical candidate, but whose
	// single mention leaves it ranked far below the others.
	addNote(t, x, "memory/faint.md", "unrelated title",
		"a wall of text about gardening that happens to mention homelab and server once, "+
			strings.Repeat("filler about compost and tomatoes ", 400))

	if err := x.PutVectors("m", []VectorRow{
		{DocID: docID(t, x, "memory/faint.md"), MtimeNS: 1, Vec: unit(1, 0, 0)},
	}); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}

	lex, err := x.Search(Query{Text: probeQuery, K: rrfDepth, Mode: ModeFusion})
	if err != nil {
		t.Fatalf("fusion: %v", err)
	}
	for _, r := range lex.Results {
		if r.Path == "memory/faint.md" {
			t.Skip("the faint note reached the fusion window; fixture cannot test promotion from below it")
		}
	}

	out, err := x.Search(Query{
		Text: probeQuery, K: 5, Mode: ModeHybrid,
		Vector: unit(1, 0, 0), EmbedModel: "m",
	})
	if err != nil {
		t.Fatalf("hybrid: %v", err)
	}

	var seen bool
	for _, r := range out.Results {
		if r.Path != "memory/faint.md" {
			continue
		}
		seen = true
		if r.Snippet != "" {
			t.Errorf("the promoted row carries snippet %q; it ranked below the fusion "+
				"window, where the previous code returned it bare — a latency fix must "+
				"not widen what gets injected", r.Snippet)
		}
	}
	if !seen {
		t.Fatal("the dense arm did not promote the faint note; the fixture does not test what it claims")
	}
}

// Every row a caller receives from a lexical mode still carries its snippet.
// The fix moved when the pass runs, not whether it runs, and a "fix" that made
// searches fast by dropping the field would pass the counter assertions above.
func TestReturnedRowsStillCarryTheirSnippets(t *testing.T) {
	for _, mode := range []string{ModeAnd, ModeFusion, ModeHybrid} {
		t.Run(mode, func(t *testing.T) {
			x := snippetFixture(t, 80)
			out, err := x.Search(Query{Text: probeQuery, K: 5, Mode: mode})
			if err != nil {
				t.Fatalf("search: %v", err)
			}
			for i, r := range out.Results {
				if r.Snippet == "" {
					t.Errorf("rank %d (%s) came back without a snippet", i+1, r.Path)
				}
			}
		})
	}
}
