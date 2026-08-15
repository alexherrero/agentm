package main

import (
	"context"
	"fmt"
	"sort"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/rerank"
)

// modeRerank is the CLI's arm name for "hybrid, then cross-encoder rerank,
// then floor." It is not an index.Query.Mode value: package index stays a
// pure SQLite consumer with no notion of a child process (see the vector
// arm's own doc comment), so the rerank pass runs here, one layer up, over
// results index.Search already produced — the same layering ModeHybrid's
// query vector already uses, extended by one stage.
const modeRerank = "rerank"

// rerankDepth is how many fused candidates the cross-encoder reranks — the
// design's own "top ~20", and the input to task 5's ~20-pairs-per-query
// latency budget.
const rerankDepth = 20

// maxChunksPerCandidate bounds how many of one candidate's chunks the
// cross-encoder scores. Inside the vector arm's scope (memory/desk/external)
// this essentially never binds — task 2.5 measured an average of about five
// chunks among the minority of notes that split at all. It exists for a
// candidate the lexical arm alone surfaces from outside that scope (a
// `_meta/` file, say, never chunked-and-capped for embedding), which could
// otherwise multiply one query's pair count far past the ~20 the latency
// budget assumes.
const maxChunksPerCandidate = 20

// rerankQueryReserve is headroom subtracted from the reranker's context
// window before chunking a candidate for scoring — measured necessary, not
// assumed. A cross-encoder scores query and document as one combined
// sequence, unlike the embedder, which scores them in two separate passes
// each fitting its own window whole; a chunk sized to the embedder's full
// window leaves no room for the query once the two share one sequence. Fed
// straight through, the very first real query tried overflowed the
// reranker's 2048-token physical batch on 5 of 43 real fused-candidate
// chunks (measured 2064-2271 tokens for a query the extractor reduces to a
// handful of terms). 128 tokens is not a precise query-length budget so much
// as a working margin: re-measuring 20 real candidates' chunks at this
// reserve against the same query found zero overflows, against 5 at zero
// reserve. It does not need to be exact, because scoreDocuments' retry
// ladder is the actual backstop — this constant only decides how often that
// ladder has to run.
const rerankQueryReserve = 128

// rerankChunkBudget is the ctxTokens value a candidate is chunked at for
// scoring — the reranker's own window minus the query's reserve.
func rerankChunkBudget(model rerank.Model) int {
	budget := model.CtxTokens - rerankQueryReserve
	if budget <= 0 {
		return model.CtxTokens
	}
	return budget
}

// rerankFused reranks a hybrid search's fused top-N with a cross-encoder and
// applies the per-model relevance floor: a candidate survives only if its
// best-scoring chunk reaches the floor, and the design's "inject the
// survivors, or inject nothing" is exactly what the caller gets back — a
// result set that may be shorter than k, down to empty.
func rerankFused(
	ctx context.Context, idx *index.Index, sup *rerank.Supervisor,
	query string, hybrid index.SearchOutcome, k int,
) (index.SearchOutcome, error) {
	out := hybrid // carries forward whatever note searchHybrid already set,
	// e.g. a "lexical arm alone" degrade note if the embedder was unavailable.

	if sup == nil || !sup.Available() {
		if len(out.Results) > k {
			out.Results = out.Results[:k]
		}
		out.Note = joinNote(out.Note,
			"rerank was requested but no reranker was available; this is the "+
				"hybrid arm alone, unfloored (check `agentmd status` for the reranker)")
		return out, nil
	}
	model := sup.Model()

	// Every candidate is chunked the same way regardless of which arm
	// surfaced it or whether it was ever embedded — a lexical-only hit gets
	// exactly the same treatment as a dense hit, because both are just text
	// fetched fresh from the index here. This is the pre-registered policy:
	// CE-score every chunk of a candidate and take the max, the same
	// "score a note by its best chunk" rule VectorSearch already applies to
	// the dense arm (see internal/index/vector.go), now applied to the
	// cross-encoder too. A candidate outside the vector arm's scope was
	// never chunked for embedding and has no stored chunk to prefer — it is
	// chunked here for the first time, capped by maxChunksPerCandidate so
	// one long out-of-scope file cannot dominate a single query's pair
	// budget.
	var documents []string
	var owner []int
	for ci, r := range hybrid.Results {
		title, body, ok, err := idx.DocText(r.Path)
		if err != nil {
			return hybrid, fmt.Errorf("fetching %s for rerank: %w", r.Path, err)
		}
		if !ok {
			continue // vanished between the fused search and this pass
		}
		chunks := index.ChunkText(title, body, rerankChunkBudget(model))
		if len(chunks) > maxChunksPerCandidate {
			chunks = chunks[:maxChunksPerCandidate]
		}
		for _, c := range chunks {
			documents = append(documents, c)
			owner = append(owner, ci)
		}
	}

	out.Matched = len(hybrid.Results)
	if len(documents) == 0 {
		out.Results = nil
		out.Note = joinNote(out.Note, "0 results. The fused candidates had no text to rerank.")
		return out, nil
	}

	started := time.Now()
	scores, scored, err := scoreDocuments(ctx, sup, query, documents)
	elapsed := time.Since(started)
	if err != nil {
		return hybrid, fmt.Errorf("reranking: %w", err)
	}
	out.RerankMS = float64(elapsed) / float64(time.Millisecond)
	out.RerankPairs = len(documents)

	bestRaw := make(map[int]float64, len(hybrid.Results))
	for i, s := range scores {
		if !scored[i] {
			continue // unscorable even after scoreDocuments' shortening retries
		}
		ci := owner[i]
		if prev, seen := bestRaw[ci]; !seen || s > prev {
			bestRaw[ci] = s
		}
	}

	survivors := make([]index.Result, 0, len(hybrid.Results))
	for ci, r := range hybrid.Results {
		raw, ok := bestRaw[ci]
		if !ok {
			continue
		}
		sig := rerank.Sigmoid(raw)
		if sig < model.Floor {
			continue
		}
		r.RawScore = raw
		r.Score = sig
		survivors = append(survivors, r)
	}
	sort.SliceStable(survivors, func(i, j int) bool {
		if survivors[i].Score != survivors[j].Score {
			return survivors[i].Score > survivors[j].Score
		}
		return survivors[i].Path < survivors[j].Path
	})
	if len(survivors) > k {
		survivors = survivors[:k]
	}
	out.Results = survivors
	if len(survivors) == 0 {
		out.Note = joinNote(out.Note, "0 results. Every fused candidate scored below the relevance floor.")
	}
	return out, nil
}

// scoreDocuments reranks a batch of documents against one query in a single
// request, falling back to one document at a time — each shortened by the
// same halving ladder index.EmbedRetryCut already provides for the identical
// class of failure on the embedder's side — when the batch itself fails.
//
// This is not a hypothetical: rerankChunkBudget's reserve reduces, but does
// not eliminate, a chunk overflowing the reranker's physical batch once
// joined with the query, because the underlying cause is charsPerToken's own
// documented error margin (a markdown chunk can tokenize denser than the
// 3-chars/token estimate assumes — task 2.5 hit the identical failure mode
// embedding 269 of 11,761 chunks). A batch failure says nothing about which
// pair was oversized, so the retry has to go one at a time to find out, the
// same reasoning embedBatch documents.
//
// scored[i] is false for a document that could not be scored even after
// shortening; scores[i] is meaningless in that case and callers must check
// scored before reading it. embedBatch (embedder.go) is the sibling that
// established this exact fallback shape for the embedder's own child.
func scoreDocuments(
	ctx context.Context, sup *rerank.Supervisor, query string, docs []string,
) (scores []float64, scored []bool, err error) {
	if s, e := sup.Rerank(ctx, query, docs); e == nil {
		scored = make([]bool, len(docs))
		for i := range scored {
			scored[i] = true
		}
		return s, scored, nil
	}

	scores = make([]float64, len(docs))
	scored = make([]bool, len(docs))
	for i, d := range docs {
		text := d
		// Up to four halvings, the same bound embedBatch uses and for the
		// same reason: it takes any real document in this corpus under any
		// window this daemon runs at.
		for attempt := 0; attempt < 5; attempt++ {
			s, e := sup.Rerank(ctx, query, []string{text})
			if e == nil {
				scores[i] = s[0]
				scored[i] = true
				break
			}
			if ctx.Err() != nil {
				return scores, scored, ctx.Err()
			}
			text = index.EmbedRetryCut(text)
			if text == "" {
				break
			}
		}
	}
	return scores, scored, nil
}

// joinNote appends a second sentence to a note, the same join search.go's own
// unexported joinNotes performs — duplicated rather than exported across the
// package boundary for six lines that carry no other coupling.
func joinNote(a, b string) string {
	switch {
	case a == "":
		return b
	case b == "":
		return a
	}
	return a + "; " + b
}
