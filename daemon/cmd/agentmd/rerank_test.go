package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/note"
	"github.com/alexherrero/agentm/daemon/internal/rerank"
)

func testIndexWithNote(t *testing.T, rel, title, body string) *index.Index {
	t.Helper()
	dir := t.TempDir()
	x, err := index.Open(filepath.Join(dir, "index.db"), dir)
	if err != nil {
		t.Fatalf("opening index: %v", err)
	}
	t.Cleanup(func() { x.Close() })
	n := note.Note{
		Rel: rel, Title: title, Body: body,
		Captured: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), CapturedSource: "mtime",
	}
	if err := x.Upsert(n, 1, int64(len(body))); err != nil {
		t.Fatalf("indexing %s: %v", rel, err)
	}
	return x
}

// rerankModelForTest is a low floor so a test's own hand-picked scores land
// clearly on one side of it without needing to reproduce the real model's
// measured value.
func rerankModelForTest(floor float64) rerank.Model {
	return rerank.Model{Name: "test-reranker", CtxTokens: 2048, Floor: floor}
}

// fakeRerankServer replies with the score the handler chooses per document
// text, letting a test control exactly which candidate should survive the
// floor without depending on a real model's judgment.
func fakeRerankServer(t *testing.T, score func(doc string) float64) *rerank.Supervisor {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Query     string   `json:"query"`
			Documents []string `json:"documents"`
		}
		json.NewDecoder(r.Body).Decode(&body)
		results := make([]map[string]any, len(body.Documents))
		for i, d := range body.Documents {
			results[i] = map[string]any{"index": i, "relevance_score": score(d)}
		}
		json.NewEncoder(w).Encode(map[string]any{"results": results})
	}))
	t.Cleanup(srv.Close)
	return rerank.Attach(srv.URL, rerankModelForTest(0.5))
}

// A candidate whose best chunk sigmoids below the floor must not survive,
// even when it was the fused arm's top-ranked result — this is the floor
// doing exactly the job the design assigns it.
func TestRerankFusedFiltersBelowFloor(t *testing.T) {
	x := testIndexWithNote(t, "keep.md", "keep", "on-topic body")
	if err := x.Upsert(note.Note{
		Rel: "drop.md", Title: "drop", Body: "off-topic body",
		Captured: time.Now(), CapturedSource: "mtime",
	}, 1, 10); err != nil {
		t.Fatal(err)
	}

	sup := fakeRerankServer(t, func(doc string) float64 {
		if strings.Contains(doc, "on-topic") {
			return 5.0 // sigmoid(5) ~ 0.993, well above any reasonable floor
		}
		return -5.0 // sigmoid(-5) ~ 0.007, well below
	})

	hybrid := index.SearchOutcome{Results: []index.Result{
		{Path: "drop.md", Score: 0.9}, // ranked first by fusion, must still drop
		{Path: "keep.md", Score: 0.1},
	}}

	out, err := rerankFused(context.Background(), x, sup, "q", hybrid, 5)
	if err != nil {
		t.Fatalf("rerankFused: %v", err)
	}
	if len(out.Results) != 1 || out.Results[0].Path != "keep.md" {
		t.Fatalf("results = %v, want exactly keep.md", out.Results)
	}
}

// The floor is per-candidate, not all-or-nothing: a query with one strong
// candidate and one weak one must keep the strong one rather than being
// rejected wholesale.
func TestRerankFusedSortsSurvivorsByScore(t *testing.T) {
	x := testIndexWithNote(t, "second.md", "second", "second best relevant")
	x.Upsert(note.Note{Rel: "first.md", Title: "first", Body: "first best relevant"},
		1, 10)

	sup := fakeRerankServer(t, func(doc string) float64 {
		if strings.Contains(doc, "first best") {
			return 8.0
		}
		return 2.0
	})
	hybrid := index.SearchOutcome{Results: []index.Result{
		{Path: "second.md"}, // fusion ranked this first
		{Path: "first.md"},  // the cross-encoder disagrees and must win
	}}

	out, err := rerankFused(context.Background(), x, sup, "q", hybrid, 5)
	if err != nil {
		t.Fatalf("rerankFused: %v", err)
	}
	if len(out.Results) != 2 || out.Results[0].Path != "first.md" {
		t.Fatalf("results = %v, want first.md ranked ahead of second.md", out.Results)
	}
	if out.Results[0].Score <= out.Results[1].Score {
		t.Errorf("Score field is not the sigmoid-scale rerank score: %v", out.Results)
	}
}

// No reranker available must degrade to the unreranked hybrid arm, truncated
// to k, with a note — the same shape ModeHybrid itself falls back to lexical
// with, not an error.
func TestRerankFusedDegradesWhenReankerUnavailable(t *testing.T) {
	x := testIndexWithNote(t, "a.md", "a", "body")
	hybrid := index.SearchOutcome{Results: []index.Result{
		{Path: "a.md"}, {Path: "b.md"}, {Path: "c.md"},
	}}
	sup := rerank.New(rerank.Options{}) // no model installed -> StateOff

	out, err := rerankFused(context.Background(), x, sup, "q", hybrid, 2)
	if err != nil {
		t.Fatalf("rerankFused: %v", err)
	}
	if len(out.Results) != 2 {
		t.Fatalf("got %d results, want the hybrid arm truncated to k=2", len(out.Results))
	}
	if !strings.Contains(out.Note, "no reranker was available") {
		t.Errorf("note = %q; a silent degrade is the failure mode this must prevent", out.Note)
	}
}

// Every candidate below the floor must return an explicitly empty result set
// with an explanatory note, mirroring every other mode's "0 results" note —
// the honest empty the design's rejection story depends on.
func TestRerankFusedAllBelowFloorReturnsEmptyWithNote(t *testing.T) {
	x := testIndexWithNote(t, "a.md", "a", "nothing relevant here")
	sup := fakeRerankServer(t, func(doc string) float64 { return -10.0 })
	hybrid := index.SearchOutcome{Results: []index.Result{{Path: "a.md"}}}

	out, err := rerankFused(context.Background(), x, sup, "q", hybrid, 5)
	if err != nil {
		t.Fatalf("rerankFused: %v", err)
	}
	if len(out.Results) != 0 {
		t.Fatalf("results = %v, want none", out.Results)
	}
	if !strings.Contains(out.Note, "below the relevance floor") {
		t.Errorf("note = %q; does not explain the empty result", out.Note)
	}
}

// A candidate that vanished between the fused search and this pass (deleted,
// or a stale path) must be skipped rather than failing the whole rerank.
func TestRerankFusedSkipsVanishedCandidate(t *testing.T) {
	x := testIndexWithNote(t, "present.md", "present", "relevant text")
	sup := fakeRerankServer(t, func(doc string) float64 { return 5.0 })
	hybrid := index.SearchOutcome{Results: []index.Result{
		{Path: "present.md"},
		{Path: "gone.md"}, // never indexed
	}}

	out, err := rerankFused(context.Background(), x, sup, "q", hybrid, 5)
	if err != nil {
		t.Fatalf("rerankFused returned an error for a vanished candidate: %v", err)
	}
	if len(out.Results) != 1 || out.Results[0].Path != "present.md" {
		t.Fatalf("results = %v, want only present.md", out.Results)
	}
}

// A long candidate must be split into several chunks and scored by its best
// one — the same "score a note by its best chunk" rule the dense arm already
// applies, now applied to the cross-encoder. A short candidate must produce
// exactly one chunk, so the common case pays for exactly one pair.
func TestRerankFusedScoresCandidateByBestChunk(t *testing.T) {
	// Budget at ctxTokens=2048 is (2048-64)*3 = 5952 bytes; well past two
	// chunks' worth of body forces a split.
	long := strings.Repeat("filler prose that carries no signal. ", 500) +
		"THE ANSWER IS HERE near the end of a long document."
	x := testIndexWithNote(t, "long.md", "long", long)

	var gotPairs int
	sup := fakeRerankServer(t, func(doc string) float64 {
		gotPairs++
		if strings.Contains(doc, "THE ANSWER IS HERE") {
			return 6.0
		}
		return -6.0
	})
	hybrid := index.SearchOutcome{Results: []index.Result{{Path: "long.md"}}}

	out, err := rerankFused(context.Background(), x, sup, "q", hybrid, 5)
	if err != nil {
		t.Fatalf("rerankFused: %v", err)
	}
	if gotPairs < 2 {
		t.Fatalf("server saw %d pair(s); the long note was not chunked", gotPairs)
	}
	if len(out.Results) != 1 || out.Results[0].Path != "long.md" {
		t.Fatalf("results = %v, want the note kept on the strength of its best chunk", out.Results)
	}
	if out.RerankPairs != gotPairs {
		t.Errorf("RerankPairs = %d, want %d (what the server actually saw)", out.RerankPairs, gotPairs)
	}
}

// A pathologically long candidate — outside the vector arm's scope, so never
// bounded by the embedder's own chunking — must not multiply one query's
// pair count without limit.
func TestRerankFusedCapsChunksPerCandidate(t *testing.T) {
	huge := strings.Repeat("x", maxChunksPerCandidate*6000)
	x := testIndexWithNote(t, "huge.md", "huge", huge)

	var gotPairs int
	sup := fakeRerankServer(t, func(doc string) float64 {
		gotPairs++
		return 1.0
	})
	hybrid := index.SearchOutcome{Results: []index.Result{{Path: "huge.md"}}}

	if _, err := rerankFused(context.Background(), x, sup, "q", hybrid, 5); err != nil {
		t.Fatalf("rerankFused: %v", err)
	}
	if gotPairs > maxChunksPerCandidate {
		t.Fatalf("server saw %d pairs for one candidate, want capped at %d", gotPairs, maxChunksPerCandidate)
	}
}

// A batch failure must not fail the whole rerank — scoreDocuments falls back
// to one document at a time, exactly the shape embedBatch already
// established for the identical class of failure on the embedder's side.
func TestScoreDocumentsFallsBackOnBatchFailure(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Documents []string `json:"documents"`
		}
		json.NewDecoder(r.Body).Decode(&body)
		if len(body.Documents) > 1 {
			w.WriteHeader(http.StatusInternalServerError)
			w.Write([]byte(`{"error":{"message":"input is too large to process"}}`))
			return
		}
		json.NewEncoder(w).Encode(map[string]any{
			"results": []map[string]any{{"index": 0, "relevance_score": 3.0}},
		})
	}))
	t.Cleanup(srv.Close)
	sup := rerank.Attach(srv.URL, rerankModelForTest(0.5))

	scores, scored, err := scoreDocuments(context.Background(), sup, "q", []string{"a", "b", "c"})
	if err != nil {
		t.Fatalf("scoreDocuments: %v", err)
	}
	for i := range scores {
		if !scored[i] {
			t.Errorf("doc %d not scored after the per-document fallback", i)
		}
		if scores[i] != 3.0 {
			t.Errorf("doc %d score = %v, want 3.0", i, scores[i])
		}
	}
}

// A document that is oversized even alone must be shortened until it fits,
// mirroring EmbedRetryCut's halving ladder on the embedder's side.
//
// The fixture's threshold is tuned to succeed on the second shortening
// (200 -> 100), not the fourth: the Supervisor's own failThreshold (three
// *consecutive* failures condemns the child, see rerank/supervisor.go) is a
// real interaction here, not a fixture accident. The batch attempt and the
// loop's own first try both fail before the text is short enough, which is
// two consecutive failures — realistic for what rerankChunkBudget's reserve
// leaves as residual overflow (measured at 16-223 tokens over a ~1920-token
// budget, comfortably inside one halving) and safely under the threshold
// that would otherwise degrade the child out from under a retry that was
// about to succeed.
func TestScoreDocumentsShortensUntilItFits(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Documents []string `json:"documents"`
		}
		json.NewDecoder(r.Body).Decode(&body)
		if len(body.Documents[0]) > 100 {
			w.WriteHeader(http.StatusInternalServerError)
			w.Write([]byte(`{"error":{"message":"too large"}}`))
			return
		}
		json.NewEncoder(w).Encode(map[string]any{
			"results": []map[string]any{{"index": 0, "relevance_score": 7.0}},
		})
	}))
	t.Cleanup(srv.Close)
	sup := rerank.Attach(srv.URL, rerankModelForTest(0.5))

	scores, scored, err := scoreDocuments(context.Background(), sup, "q", []string{strings.Repeat("x", 200)})
	if err != nil {
		t.Fatalf("scoreDocuments: %v", err)
	}
	if !scored[0] {
		t.Fatal("an oversized document was never scored despite shortening")
	}
	if scores[0] != 7.0 {
		t.Errorf("score = %v, want 7.0", scores[0])
	}
}

// A document the server refuses at every size must be reported unscorable
// rather than failing the whole call — one bad chunk must not take down a
// query's entire rerank pass.
func TestScoreDocumentsGivesUpAfterExhaustingRetries(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte(`{"error":{"message":"always fails"}}`))
	}))
	t.Cleanup(srv.Close)
	sup := rerank.Attach(srv.URL, rerankModelForTest(0.5))

	scores, scored, err := scoreDocuments(context.Background(), sup, "q", []string{"a", strings.Repeat("y", 100)})
	if err != nil {
		t.Fatalf("scoreDocuments returned an error instead of scored=false: %v", err)
	}
	for i, s := range scored {
		if s {
			t.Errorf("doc %d reported scored=true against a server that always fails", i)
		}
	}
	_ = scores
}
