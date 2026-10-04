package index

import (
	"database/sql"
	"math"
	"sort"
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
	// spreadSeeds is how many of the fused list's leaders activation starts from.
	spreadSeeds = 5
	// spreadPerSeed caps what one seed admits. Entity pages link to everything —
	// a repo page here links to 171 notes — and an uncapped hop would fill the
	// list with a hub's neighbourhood.
	spreadPerSeed = 3
	// spreadDecay is what one hop costs: a neighbour scores at half its seed.
	spreadDecay = 0.5
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

// spreadActivation follows one hop of the link graph out from the fused
// list's top five and merges what it finds back in.
//
// The edges are the ones a writer drew on purpose: a seed's own wikilinks,
// body and frontmatter alike — which is where `related:`,
// `consolidated_into:` and `consolidated_from:` live — and the entity pages
// that link to it. Markdown links, every other backlink and second hops are
// not followed, and an activated note never seeds, so a cycle ends after one
// step.
//
// A neighbour goes through the same walls the arms apply and the query's date
// bounds, before the cap, so a walled note neither appears nor takes a slot.
// Each seed admits the three whose vector sits nearest the query; notes with no
// vector follow, by path. A neighbour scores its seed's fused score × 0.5 ×
// its own multiplier — class penalty, project activity, age and project, by
// the function the arms use — with a consolidated neighbour always demoted. A
// note already in the list keeps the better of its two scores.
func (x *Index) spreadActivation(rows []Result, q Query, after, before string) ([]Result, error) {
	seeds := rows
	if len(seeds) > spreadSeeds {
		seeds = seeds[:spreadSeeds]
	}
	isSeed := make(map[string]bool, len(seeds))
	for _, s := range seeds {
		isSeed[s.Path] = true
	}

	activated := map[string]Result{}
	decayLog, decayNow := x.decayClock()
	wantArtifact := note.QueryWantsArtifact(q.Text)
	for _, seed := range seeds {
		cands, err := x.linkedRows(seed.Path, after, before)
		if err != nil {
			return rows, err
		}
		kept := cands[:0]
		for _, c := range cands {
			if !isSeed[c.Path] {
				kept = append(kept, c)
			}
		}
		cands, _ = wallUnserved(kept, q.IncludeArchived)
		if cands, err = x.nearestToQuery(cands, q.Vector, q.EmbedModel, spreadPerSeed); err != nil {
			return rows, err
		}
		for i := range cands {
			cands[i].Score = seed.Score * spreadDecay
		}
		// No lessons function: a consolidated neighbour keeps its demotion
		// whatever the list holds, so activation can never be what lifts one.
		cands = penalizeRankAndDecay(cands, len(cands), decayLog, decayNow,
			wantArtifact, q.Project, nil)
		for _, c := range cands {
			if prev, ok := activated[c.Path]; !ok || c.Score > prev.Score {
				activated[c.Path] = c
			}
		}
	}
	if len(activated) == 0 {
		return rows, nil
	}

	out := make([]Result, 0, len(rows)+len(activated))
	for _, r := range rows {
		if a, ok := activated[r.Path]; ok {
			if a.Score > r.Score {
				r.Score = a.Score
			}
			delete(activated, r.Path)
		}
		out = append(out, r)
	}
	for _, a := range activated {
		out = append(out, a)
	}
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Score != out[j].Score {
			return out[i].Score > out[j].Score
		}
		return out[i].Path < out[j].Path
	})
	return out, nil
}

// linkedRows is every note one typed hop from `seed`, as rows ready for the
// walls and the penalty: its resolved wikilinks, and the entity pages that
// link to it. A note outside the date bounds is left out, as the arms leave it
// out in SQL.
func (x *Index) linkedRows(seed, after, before string) ([]Result, error) {
	x.mu.Lock()
	defer x.mu.Unlock()

	targets := map[string]bool{}
	collect := func(keep func(string) bool, query string) error {
		rows, err := x.db.Query(query, seed)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var p string
			if err := rows.Scan(&p); err != nil {
				return err
			}
			if keep(p) {
				targets[p] = true
			}
		}
		return rows.Err()
	}
	if err := collect(func(string) bool { return true },
		`SELECT DISTINCT l.resolved FROM links l JOIN docmeta d ON d.id = l.source_id
		 WHERE d.path = ? AND l.wiki = 1 AND l.resolved <> ''`); err != nil {
		return nil, err
	}
	if err := collect(isEntityPagePath,
		`SELECT DISTINCT d.path FROM links l JOIN docmeta d ON d.id = l.source_id
		 WHERE l.resolved = ?`); err != nil {
		return nil, err
	}
	delete(targets, seed)

	out := make([]Result, 0, len(targets))
	for p := range targets {
		var r Result
		err := x.db.QueryRow(`
			SELECT m.id, m.path, m.flags, m.captured, m.captured_src, m.updated,
			       m.created, m.project
			FROM docmeta m
			WHERE m.path = ?
			  AND (? = '' OR m.captured >= ?)
			  AND (? = '' OR m.captured <  ?)`,
			p, after, after, before, before).Scan(&r.rowid, &r.Path, &r.Penalty,
			&r.Captured, &r.CapturedSource, &r.Updated, &r.Created, &r.project)
		if err == sql.ErrNoRows {
			// Outside the date bounds, or a link resolved to a note since removed.
			continue
		}
		if err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, nil
}

// nearestToQuery keeps the `limit` rows whose note vector sits closest to the
// query's, then the rows with no vector, by path.
func (x *Index) nearestToQuery(rows []Result, query []float32, model string, limit int) ([]Result, error) {
	if len(rows) == 0 {
		return rows, nil
	}
	vecs, err := x.noteVectors(model, pathsOf(rows))
	if err != nil {
		return rows, err
	}
	q := normalised(query)
	sim := make(map[string]float64, len(rows))
	for _, r := range rows {
		if v, ok := vecs[r.Path]; ok && len(v) == len(q) {
			sim[r.Path] = dot(v, q)
		}
	}
	sort.SliceStable(rows, func(i, j int) bool {
		si, iok := sim[rows[i].Path]
		sj, jok := sim[rows[j].Path]
		if iok != jok {
			return iok
		}
		if iok && si != sj {
			return si > sj
		}
		return rows[i].Path < rows[j].Path
	})
	if len(rows) > limit {
		rows = rows[:limit]
	}
	return rows, nil
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
