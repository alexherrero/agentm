package index

import (
	"math"
	"strings"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// The ranking-side rung (task 184, ladder row 7): two stages that run after
// reciprocal-rank fusion and never replace it. Both are off unless the query
// asks for them, and neither changes a note, a term or an arm's own scores.
//
// Every parameter below was fixed before this file existed, in
// `scripts/health/results/goldv3/RULE-ranking-stages.md`, and none may be tuned
// on the gold questions: a value moved to make the column pass is a value
// fitted to the test.
const (
	// mmrLambda weighs relevance against redundancy: 0.7 of the pick is how
	// well the fused ranking placed the note, 0.3 is how unlike the notes
	// already picked it is.
	mmrLambda = 0.7
)

// mmrRerank reorders the fused pool by maximal marginal relevance: each pick
// is the row with the highest λ·relevance − (1−λ)·(its closest cosine to a row
// already picked), so a near-duplicate of a note already in the list gives way
// to one that covers something else.
//
// Relevance is the fused score min-max normalised over the pool, which puts it
// on cosine's scale; RRF's own scores span about 0.009–0.033 and would lose to
// any similarity term. A note's vector is the mean of its chunk vectors,
// normalised. A note without one has no similarity term, so it is ranked on
// its relevance alone — the hook still serves notes the embedder never saw.
//
// The scores are left as fusion set them, so the call log still shows the
// evidence; only the order changes.
//
// A demoted row — consolidated, superseded or archived — is eligible only once
// every row above it in the fused order is placed. Diversity may push it down,
// never past a note that outranked it: the demotion was the corpus's judgment
// about that note, and a stage about redundancy has no business undoing it.
func (x *Index) mmrRerank(rows []Result, model string) ([]Result, error) {
	n := len(rows)
	if n < 2 {
		return rows, nil
	}
	vecs, err := x.noteVectors(model, pathsOf(rows))
	if err != nil {
		return rows, err
	}

	hi, lo := rows[0].Score, rows[0].Score
	for _, r := range rows {
		hi, lo = math.Max(hi, r.Score), math.Min(lo, r.Score)
	}
	rel := make([]float64, n)
	for i, r := range rows {
		rel[i] = 1
		if hi > lo {
			rel[i] = (r.Score - lo) / (hi - lo)
		}
	}

	picked := make([]bool, n)
	// closest[i] is the highest cosine from row i to any picked row with a
	// vector; compared[i] says whether there has been one. The max over an
	// empty set is 0.
	closest := make([]float64, n)
	compared := make([]bool, n)
	firstOpen := 0
	out := make([]Result, 0, n)
	for len(out) < n {
		best, bestScore := -1, math.Inf(-1)
		for i := firstOpen; i < n; i++ {
			if picked[i] || (demoted(rows[i]) && i != firstOpen) {
				continue
			}
			s := mmrLambda*rel[i] - (1-mmrLambda)*closest[i]
			// Strictly greater, scanning in fused order: a tie goes to the row
			// fusion placed higher, and fusion's own ties already went by path.
			if s > bestScore {
				best, bestScore = i, s
			}
		}
		picked[best] = true
		out = append(out, rows[best])
		for firstOpen < n && picked[firstOpen] {
			firstOpen++
		}
		vb, ok := vecs[rows[best].Path]
		if !ok {
			continue
		}
		for i := firstOpen; i < n; i++ {
			vi, ok := vecs[rows[i].Path]
			if picked[i] || !ok {
				continue
			}
			c := dot(vi, vb)
			if !compared[i] || c > closest[i] {
				closest[i], compared[i] = c, true
			}
		}
	}
	return out, nil
}

// demoted is a row whose class the ranking holds down, which MMR's guard keeps
// from rising past what outranked it.
func demoted(r Result) bool {
	flags := splitFlags(r.Penalty)
	return hasFlag(flags, note.ClassConsolidated) ||
		hasFlag(flags, note.ClassSuperseded) || hasFlag(flags, note.ClassArchived)
}

// noteVectors is each note's vector for the stages: the normalised mean of its
// chunk vectors under one model, read in one query. A note with none is absent.
func (x *Index) noteVectors(model string, paths []string) (map[string][]float32, error) {
	out := make(map[string][]float32, len(paths))
	if len(paths) == 0 || model == "" {
		return out, nil
	}
	x.mu.Lock()
	defer x.mu.Unlock()

	marks := strings.TrimSuffix(strings.Repeat("?,", len(paths)), ",")
	args := make([]any, 0, len(paths)+1)
	args = append(args, model)
	for _, p := range paths {
		args = append(args, p)
	}
	rows, err := x.db.Query(`
		SELECT m.path, e.vec FROM embeddings e JOIN docmeta m ON m.id = e.doc_id
		WHERE e.model = ? AND m.path IN (`+marks+`)
		ORDER BY m.path, e.chunk_idx`, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	sums := map[string][]float64{}
	for rows.Next() {
		var p string
		var blob []byte
		if err := rows.Scan(&p, &blob); err != nil {
			return nil, err
		}
		v := decodeVec(blob, nil)
		s, ok := sums[p]
		if !ok {
			s = make([]float64, len(v))
			sums[p] = s
		}
		if len(v) != len(s) {
			continue
		}
		for i, f := range v {
			s[i] += float64(f)
		}
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	for p, s := range sums {
		v := make([]float32, len(s))
		for i, f := range s {
			v[i] = float32(f)
		}
		out[p] = normalised(v)
	}
	return out, nil
}

func pathsOf(rows []Result) []string {
	out := make([]string, len(rows))
	for i, r := range rows {
		out[i] = r.Path
	}
	return out
}

// normalised is v scaled to unit length, so a dot product between two of them
// is their cosine. A zero vector stays zero.
func normalised(v []float32) []float32 {
	var sum float64
	for _, f := range v {
		sum += float64(f) * float64(f)
	}
	out := make([]float32, len(v))
	if sum == 0 {
		return out
	}
	inv := 1 / math.Sqrt(sum)
	for i, f := range v {
		out[i] = float32(float64(f) * inv)
	}
	return out
}

func dot(a, b []float32) float64 {
	if len(a) != len(b) {
		return 0
	}
	var s float64
	for i := range a {
		s += float64(a[i]) * float64(b[i])
	}
	return s
}
