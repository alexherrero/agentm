package index

import (
	"fmt"
	"strings"
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// fuseRRF's contract, pinned to hand-computed literals.
//
// The reciprocals are written out rather than recomputed with the implementation's
// own expression: a test that derives its expected value from the code under test
// proves the two agree and nothing else, and the constant 60 is exactly the kind
// of thing that gets "improved" by someone who has not read why it is 60.
func TestRRFScoresAreTheDocumentedReciprocals(t *testing.T) {
	lexical := []Result{{Path: "a"}, {Path: "b"}}
	dense := []Result{{Path: "b"}, {Path: "c"}}

	got := fuseRRF(lexical, dense)

	// a: lexical rank 1                -> 1/61        = 0.0163934426
	// b: lexical rank 2 + dense rank 1 -> 1/62 + 1/61 = 0.0325224749
	// c: dense rank 2                  -> 1/62        = 0.0161290323
	want := map[string]float64{"a": 0.0163934, "b": 0.0325225, "c": 0.0161290}
	if len(got) != 3 {
		t.Fatalf("fused %d documents, want 3", len(got))
	}
	// b appears in both lists and must therefore come first: agreement between
	// two independent retrievers is the entire mechanism.
	if got[0].Path != "b" {
		t.Errorf("first result is %s; the document both arms found must rank first", got[0].Path)
	}
	for _, r := range got {
		if diff := r.Score - want[r.Path]; diff > 1e-6 || diff < -1e-6 {
			t.Errorf("%s scored %.7f, want %.7f", r.Path, r.Score, want[r.Path])
		}
	}
}

// A document that places first in one arm and nowhere in the other must still
// lose to one placing second in both. That is the property that makes RRF a
// consensus rule rather than a max, and it is what separates it from the
// max-score fusion the *lexical* arm uses internally.
func TestRRFPrefersAgreementOverASingleFirstPlace(t *testing.T) {
	lexical := []Result{{Path: "loner"}, {Path: "agreed"}}
	dense := []Result{{Path: "other"}, {Path: "agreed"}}
	got := fuseRRF(lexical, dense)
	if got[0].Path != "agreed" {
		t.Fatalf("first is %s, want agreed (1/62+1/62 beats a single 1/61)", got[0].Path)
	}
}

// Ties must break by path, so a re-run of the same query returns the same order.
// RRF produces exact ties routinely — two documents at the same rank in the same
// single list score identically — so this is the common case, not an edge one.
func TestRRFIsDeterministicUnderTies(t *testing.T) {
	first := fuseRRF([]Result{{Path: "z"}, {Path: "y"}}, []Result{{Path: "y"}, {Path: "z"}})
	for i := 0; i < 20; i++ {
		again := fuseRRF([]Result{{Path: "z"}, {Path: "y"}}, []Result{{Path: "y"}, {Path: "z"}})
		for j := range first {
			if first[j].Path != again[j].Path {
				t.Fatalf("run %d differs at rank %d: %s vs %s", i, j, first[j].Path, again[j].Path)
			}
		}
	}
	if first[0].Path != "y" {
		t.Errorf("tie broke to %s, want y (lexicographically first)", first[0].Path)
	}
}

// Hybrid with no query vector must return the lexical arm and say so, not fail.
// A caller that asked for hybrid and got one arm has a worse search; a caller
// that got an error has none, which would make this daemon less reliable than
// the lexical-only one it replaces.
func TestHybridWithoutVectorDegradesToLexical(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/hit.md", "homelab server", "the homelab server runs in the closet")
	addNote(t, x, "memory/miss.md", "unrelated", "nothing to see")

	out, err := x.Search(Query{Text: probeQuery, K: 5, Mode: ModeHybrid})
	if err != nil {
		t.Fatalf("hybrid without a vector returned an error: %v", err)
	}
	if len(out.Results) == 0 || out.Results[0].Path != "memory/hit.md" {
		t.Fatalf("results = %v, want the lexical hit first", out.Results)
	}
	if !strings.Contains(out.Note, "lexical arm alone") {
		t.Errorf("note = %q; a silently degraded search is the failure mode this must prevent", out.Note)
	}
}

// The dense arm must be able to surface a note the lexical arm cannot reach at
// all. This is the entire reason the vector arm exists — crossing a vocabulary
// gap — so it is asserted directly rather than inferred from a score.
func TestHybridSurfacesWhatLexicalCannot(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/lexical.md", "homelab server", "the homelab server runs in the closet")
	addNote(t, x, "memory/paraphrase.md", "home lab box", "the machine under the stairs")

	// The paraphrase shares no term with the query, so no lexical mode can find
	// it; the dense arm is given a vector identical to the query's.
	if err := x.PutVectors("m", []VectorRow{
		{DocID: docID(t, x, "memory/paraphrase.md"), MtimeNS: 1, Vec: unit(1, 0, 0)},
		{DocID: docID(t, x, "memory/lexical.md"), MtimeNS: 1, Vec: unit(0, 0, 1)},
	}); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}

	lex, err := x.Search(Query{Text: probeQuery, K: 5, Mode: ModeFusion})
	if err != nil {
		t.Fatalf("fusion: %v", err)
	}
	for _, r := range lex.Results {
		if r.Path == "memory/paraphrase.md" {
			t.Fatal("the lexical arm found the paraphrase; the fixture does not test what it claims")
		}
	}

	out, err := x.Search(Query{
		Text: probeQuery, K: 5, Mode: ModeHybrid, Vector: unit(1, 0, 0), EmbedModel: "m",
	})
	if err != nil {
		t.Fatalf("hybrid: %v", err)
	}
	var found bool
	for _, r := range out.Results {
		if r.Path == "memory/paraphrase.md" {
			found = true
		}
	}
	if !found {
		t.Fatalf("hybrid did not surface the paraphrase: %v", out.Results)
	}
}

// A note the dense arm found and the lexical arm did not has no matching phrase
// to highlight. It must come back with an empty snippet rather than a misleading
// extract of some other query's terms.
func TestHybridLeavesDenseOnlyHitsWithoutASnippet(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/lexical.md", "homelab server", "the homelab server runs in the closet")
	addNote(t, x, "memory/paraphrase.md", "home lab box", "the machine under the stairs")
	if err := x.PutVectors("m", []VectorRow{
		{DocID: docID(t, x, "memory/paraphrase.md"), MtimeNS: 1, Vec: unit(1, 0, 0)},
	}); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}
	out, err := x.Search(Query{
		Text: probeQuery, K: 5, Mode: ModeHybrid, Vector: unit(1, 0, 0), EmbedModel: "m",
	})
	if err != nil {
		t.Fatalf("hybrid: %v", err)
	}
	for _, r := range out.Results {
		if r.Path == "memory/paraphrase.md" && r.Snippet != "" {
			t.Errorf("dense-only hit carries snippet %q, which highlights terms it does not contain", r.Snippet)
		}
		if r.Path == "memory/lexical.md" && r.Snippet == "" {
			t.Error("the lexical hit lost its snippet")
		}
	}
}

// The rank penalty has to be applied inside each arm, before the ranks are taken.
// Applied after fusion it would have no effect at all — the penalized note would
// already have contributed its rank-1 reciprocal.
func TestHybridPenalizesInsideTheDenseArm(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/clean.md", "clean", "body")
	// `status` is the penalty *class*; `superseded` is the frontmatter value that
	// puts a note into it. Passing the value here would carry no weight at all
	// and the test would pass for the wrong reason.
	addPenalizedNote(t, x, "memory/retired.md", "retired", "body", note.ClassStatus)

	// The penalized note is a marginally better cosine match. Without the
	// in-arm penalty it takes rank 1 of the dense arm and wins the fusion.
	if err := x.PutVectors("m", []VectorRow{
		{DocID: docID(t, x, "memory/retired.md"), MtimeNS: 1, Vec: unit(1, 0, 0)},
		{DocID: docID(t, x, "memory/clean.md"), MtimeNS: 1, Vec: unit(0.99, 0.14, 0)},
	}); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}
	out, err := x.Search(Query{
		Text: "clean retired", K: 5, Mode: ModeHybrid, Vector: unit(1, 0, 0), EmbedModel: "m",
	})
	if err != nil {
		t.Fatalf("hybrid: %v", err)
	}
	if len(out.Results) < 2 {
		t.Fatalf("got %d results, want both notes", len(out.Results))
	}
	if out.Results[0].Path != "memory/clean.md" {
		t.Errorf("first is %s; the superseded note outranked a live one, so the "+
			"penalty was applied after the ranks were taken", out.Results[0].Path)
	}
}

// TestHybridLex3WidensTheDegradedLexicalArm confirms Query.Lex3 reaches
// searchHybrid's own internal searchFusion call, not just the top-level
// ModeFusion dispatch. The no-vector degrade path is what lets this run without
// an embedder: hybrid-minus-the-dense-arm is exactly the same searchFusion call
// ModeFusion makes, at rrfDepth instead of k, so the fixture from
// TestFusionLex3FindsWhatTwoTermSubsetsCannot proves the same thing here.
func TestHybridLex3WidensTheDegradedLexicalArm(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "compAB.md", "notes", "alpha alpha alpha beta beta beta")
	addNote(t, x, "compAG.md", "notes", "alpha alpha alpha gamma gamma gamma")
	addNote(t, x, "compBG.md", "notes", "beta beta beta gamma gamma gamma")
	addNote(t, x, "target.md", "notes", "alpha beta gamma")

	const q = "alpha beta gamma"
	out, err := x.Search(Query{Text: q, K: 1, Mode: ModeHybrid, Lex3: true})
	if err != nil {
		t.Fatalf("hybrid (degraded to lexical) with Lex3: %v", err)
	}
	if got := paths(out.Results); len(got) != 1 || got[0] != "target.md" {
		t.Fatalf("hybrid's degraded lexical arm should widen the same way "+
			"ModeFusion's does, got %v", got)
	}
	if !strings.Contains(out.Note, "lexical arm alone") {
		t.Errorf("note = %q; this test relies on the no-vector degrade path", out.Note)
	}
}

// Hybrid must price its snippet pass for the k rows the caller receives, not for
// the rrfDepth rows its lexical arm ranks internally.
//
// searchHybrid reads rrfDepth rows from the lexical arm purely as an RRF input
// and then keeps k of the fused list, so highlighting all rrfDepth of them
// computes a snippet for rows nobody will ever see. snippet() is priced per
// document scanned, which is what makes that waste unbounded rather than merely
// untidy: on the frozen gold corpus one research question's discarded tail was
// twelve megabytes of vendored awesome-list caches carrying 100,110 occurrences
// of its own terms, and highlighting them cost 6,417ms of a 6,474ms search that
// returns in 127ms once the pass is priced for k.
//
// The counter is asserted rather than the clock. The defect is "how many
// documents were handed to snippet()", and a timing test measures that only
// indirectly — and flakily, since the cost depends on how large the discarded
// documents happen to be.
func TestHybridSnippetsOnlyTheRowsItReturns(t *testing.T) {
	x := newTestIndex(t)
	// More matching notes than the lexical arm's own window, so a hybrid search
	// that priced snippets at rrfDepth would have rrfDepth of them to price.
	for i := 0; i < rrfDepth+10; i++ {
		addNote(t, x, fmt.Sprintf("memory/n%02d.md", i), probeQuery,
			"the homelab server runs in the closet")
	}
	if err := x.PutVectors("m", []VectorRow{
		{DocID: docID(t, x, "memory/n00.md"), MtimeNS: 1, Vec: unit(1, 0, 0)},
	}); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}

	const k = 5
	before := x.snippeted()
	out, err := x.Search(Query{
		Text: probeQuery, K: k, Mode: ModeHybrid, Vector: unit(1, 0, 0), EmbedModel: "m",
	})
	if err != nil {
		t.Fatalf("hybrid: %v", err)
	}
	if len(out.Results) != k {
		t.Fatalf("returned %d rows, want %d — the fixture must fill the k window",
			len(out.Results), k)
	}
	if got := x.snippeted() - before; got > k {
		t.Fatalf("hybrid snippeted %d documents for a k=%d search; it is pricing the "+
			"rrfDepth (%d) fusion window again rather than the rows it returns",
			got, k, rrfDepth)
	}
}

// The rows hybrid does return must still be highlighted. Pricing the snippet
// pass for k is only correct if it still happens — an optimization that returned
// k rows with no snippets at all would pass the counter assertion above while
// silently stripping the extract recall.py prefers over a head excerpt.
func TestHybridStillHighlightsTheRowsItReturns(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/hit.md", probeQuery, "the homelab server runs in the closet")
	if err := x.PutVectors("m", []VectorRow{
		{DocID: docID(t, x, "memory/hit.md"), MtimeNS: 1, Vec: unit(1, 0, 0)},
	}); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}
	out, err := x.Search(Query{
		Text: probeQuery, K: 5, Mode: ModeHybrid, Vector: unit(1, 0, 0), EmbedModel: "m",
	})
	if err != nil {
		t.Fatalf("hybrid: %v", err)
	}
	if len(out.Results) == 0 {
		t.Fatal("no results")
	}
	if !strings.Contains(out.Results[0].Snippet, "[homelab]") {
		t.Errorf("snippet = %q, want the query's own terms highlighted",
			out.Results[0].Snippet)
	}
}

// A one-term query has no two-term subset, so the lexical arm falls back to the
// plain AND ranking. That fallback moved behind fuseLexical when the snippet
// pass was hoisted to the caller, and it has to keep carrying the expression its
// rows were matched under — otherwise a single-word prompt comes back through
// hybrid with every snippet silently empty.
func TestHybridSingleTermQueryStillHighlights(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/hit.md", "homelab", "the homelab runs in the closet")
	out, err := x.Search(Query{Text: "homelab", K: 5, Mode: ModeHybrid})
	if err != nil {
		t.Fatalf("hybrid: %v", err)
	}
	if len(out.Results) == 0 {
		t.Fatal("no results")
	}
	if !strings.Contains(out.Results[0].Snippet, "[homelab]") {
		t.Errorf("snippet = %q, want the single term highlighted", out.Results[0].Snippet)
	}
}
