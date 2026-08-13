package index

import (
	"fmt"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// Result is one ranked hit.
type Result struct {
	Path string `json:"path"`
	// Score is the penalized score, larger is better. SQLite's own bm25() is
	// negative-is-better, which reads as a bug to anyone comparing two tools side
	// by side, so it is negated here once.
	Score float64 `json:"score"`
	// RawScore is the score before the penalty, present so a demotion is visible
	// in the call log rather than inferred from a number moving.
	RawScore float64 `json:"raw_score"`
	// Penalty lists the classes the note carries, including the classified-but-
	// unpenalized `fragment-promoted`.
	Penalty        string `json:"penalty,omitempty"`
	Captured       string `json:"captured,omitempty"`
	CapturedSource string `json:"captured_source,omitempty"`
	Snippet        string `json:"snippet,omitempty"`

	// rowid keys the snippet pass back to this row. Unexported, so it stays out
	// of the JSON the driver sees — it is an index-internal handle, not a fact
	// about the note.
	rowid int64
}

// The search modes. ModeAnd is what the prompt-submit hook runs and what every
// measurement to date scores. ModeFusion is quarantined behind an explicit flag:
// on the gold set it nearly quadrupled R@5 and took correct rejection from 35% to
// zero, so it must never become the default by omission.
// ModeHybrid adds the dense-vector arm and fuses it with the lexical one by
// reciprocal rank. It requires a warm embedder; without one it degrades to
// ModeFusion rather than failing, because a hybrid search that errors when a
// model is loading would make this daemon less reliable than the lexical-only
// one it replaces.
const (
	ModeAnd    = "and"
	ModeFusion = "fusion"
	ModeHybrid = "hybrid"
)

// rrfK is the reciprocal-rank-fusion constant, at the value the method was
// published with.
//
// RRF is the default because it is calibration-free: it reads ranks, never
// scores, so it cannot be broken by two arms whose scores live on different
// scales. Score fusion was examined and its portability trap measured — AgentKV's
// sigmoid constants, fitted to a 120-note corpus, saturate on ours, mapping every
// match to ~1.0 and collapsing the lexical channel to a presence bit. A
// recalibrated score-fusion arm may run as a comparison; it never ships as the
// default on constants fitted elsewhere.
//
// Note this is a different question from the one the *lexical* arm settled. There,
// fusing fifteen two-term sub-queries by RRF diluted badly and max-score won,
// because a document appearing in many mediocre sub-rankings outranked one placing
// first in a single precise sub-query. Those sub-rankings are fifteen views of one
// channel; these are two genuinely independent channels, which is the case RRF is
// for.
const rrfK = 60

// Query is one search request.
type Query struct {
	Text string
	K    int
	// After and Before bound the capture date. Episodic questions are time
	// questions, and until now the driver had no way to express one — it is the
	// second-weakest stratum at 0.42–0.54 R@5.
	After  string
	Before string
	// Mode selects how the terms are combined. Empty means ModeAnd, so every
	// existing caller keeps the semantics it was measured under.
	Mode string

	// Vector is the query's embedding, supplied by the caller. ModeHybrid needs
	// it; every other mode ignores it.
	//
	// The index does not embed anything itself. Keeping the model on the caller's
	// side is what lets this package stay a pure SQLite consumer with no notion of
	// a child process, and it is what makes the vector arm testable against
	// hand-written vectors instead of against whatever a 600MB model happens to
	// think today.
	Vector []float32
	// EmbedModel names the vectors to compare against. A query embedded by one
	// model and scored against another's rows is not a weak search, it is a
	// meaningless one, so the model is matched in SQL rather than assumed.
	EmbedModel string
}

// SearchOutcome carries the hits plus whatever the driver needs to know about how
// they were produced.
type SearchOutcome struct {
	Results []Result `json:"results"`
	// Note is advice for the driver, and is always set on an empty result set.
	// Silence would leave the agent guessing whether the corpus lacks the fact or
	// the query lacked a word.
	Note string `json:"note,omitempty"`
	// Matched is how many rows the over-fetch window saw before re-ranking.
	Matched int `json:"matched"`
}

var ftsTokenRe = regexp.MustCompile(`[A-Za-z0-9_]+`)

// Search dispatches on the query's mode, after the validation both paths share.
func (x *Index) Search(q Query) (SearchOutcome, error) {
	out := SearchOutcome{Results: []Result{}}

	text := strings.TrimSpace(q.Text)
	if text == "" {
		out.Note = "empty query"
		return out, nil
	}
	k := q.K
	if k <= 0 {
		k = 5
	}

	after, err := normalizeBound(q.After)
	if err != nil {
		return out, fmt.Errorf("after: %w", err)
	}
	before, err := normalizeBound(q.Before)
	if err != nil {
		return out, fmt.Errorf("before: %w", err)
	}

	switch q.Mode {
	case "", ModeAnd:
		return x.searchAnd(text, k, after, before)
	case ModeFusion:
		return x.searchFusion(text, k, after, before)
	case ModeHybrid:
		return x.searchHybrid(text, k, after, before, q)
	default:
		return out, fmt.Errorf("unknown search mode %q (want %q, %q or %q)",
			q.Mode, ModeAnd, ModeFusion, ModeHybrid)
	}
}

// searchAnd runs BM25 over FTS5, applies the measured rank penalty, and returns
// the top k.
//
// The penalty fetches Overfetch rows rather than k, multiplies each score by the
// weight its classes earn, re-sorts, and takes the top k. Rows are re-ordered and
// never dropped: a penalized note that is the best thing the corpus has still
// comes back first, because every other row was multiplied by 1.0 and it was
// multiplied by something greater than zero.
//
// The query goes to FTS5 as written. FTS5's bare MATCH is an implicit AND across
// terms, which is a real defect — 32 of 206 queries in the week-1 run returned
// zero results — and the OR rewrite that looked like the fix does not survive
// replication: +1.25 points at p = 0.46, against a loss of 18.8 points of correct
// rejection, because a query that never returns empty hands the agent five
// plausible notes and it names one. So the semantics stay as measured and the
// driver is *told* what happened instead, which costs nothing and preserves the
// one stratum that tests whether the system knows what it does not know.
//
// Snippets are fetched last, for the k rows that survive, and this ordering is
// the whole reason the search has two queries instead of one. `snippet()` scans
// the document it is called on, so computing it inside the ranking query prices
// every over-fetched candidate — and the penalty needs a 200-row window. On the
// operator's corpus that was 575x: a query matching 43 notes cost 3.1ms ranked
// and 1,784ms ranked-with-snippets, because the matched set included notes of
// 1.0–1.3 MB. Six of 206 benchmark queries cost four to six seconds each. Rank
// first and the scan is priced for the five rows anyone will read.
func (x *Index) searchAnd(text string, k int, after, before string) (SearchOutcome, error) {
	out := SearchOutcome{Results: []Result{}}

	limit := note.Overfetch
	if k > limit {
		limit = k
	}

	rows, matchExpr, note1, err := x.match(text, after, before, limit)
	if err != nil {
		return out, err
	}
	out.Note = note1
	out.Matched = len(rows)

	out.Results = penalizeAndRank(rows, k)

	// matchExpr is the expression FTS5 actually ran, which is the sanitized one
	// when the original was not valid syntax. Snippets have to be produced by the
	// same expression that produced the rows, or they would highlight different
	// phrases than the ranking saw.
	if err := x.fillSnippets(matchExpr, out.Results); err != nil {
		return out, err
	}

	if len(out.Results) == 0 && out.Note == "" {
		out.Note = "0 results. FTS5 requires every term to appear in the same note, " +
			"so a long phrasing often matches nothing — try two or three distinctive " +
			"words, then a different vocabulary for the same idea. Answer \"nothing " +
			"found\" only after distinct vocabularies have failed."
	}
	return out, nil
}

// penalizeAndRank applies the measured class penalty, orders by the adjusted
// score, and truncates to k. Both modes share it, so a note is demoted the same
// way whichever path surfaced it.
//
// Roughly twenty lines, worth +3.75 points of R@5 at p = 0.0195.
func penalizeAndRank(rows []Result, k int) []Result {
	for i := range rows {
		flags := splitFlags(rows[i].Penalty)
		mult := note.Multiplier(flags)
		raw := rows[i].Score
		adjusted := raw * mult
		// A multiplier below 1.0 must only ever demote. BM25 scores are normally
		// positive here, but a term common enough to appear in nearly every
		// matching document gets a negative IDF, and SQLite hands back a score
		// whose negation is negative — at which point multiplying by 0.3 moves it
		// *up* and the penalty silently becomes a promotion. The Python reference
		// has the same latent inversion; on an 8,700-note corpus a query term in
		// every document is effectively impossible, so it never fired there and
		// the measurement is unaffected. It fires immediately on a small corpus,
		// which is where this daemon's own tests live.
		if mult < 1 && adjusted > raw {
			adjusted = raw
		}
		rows[i].RawScore = raw
		rows[i].Score = adjusted
	}
	// Ties broken by path so the ordering is total and a re-run is identical.
	sort.SliceStable(rows, func(i, j int) bool {
		if rows[i].Score != rows[j].Score {
			return rows[i].Score > rows[j].Score
		}
		return rows[i].Path < rows[j].Path
	})
	if len(rows) > k {
		rows = rows[:k]
	}
	return rows
}

// searchFusion issues every two-term subset of the query as its own AND search
// and keeps, for each document, the best score any single subset gave it.
//
// The motivation is measured rather than theoretical. For 45 of 53 gold-set
// misses some subset of the same extracted terms already reaches the top 5, so
// ANDing all six destroys information the query already carries — term selection,
// not vocabulary, is the dominant lexical defect.
//
// Max-score rather than RRF, also measured. RRF dilutes badly here (2-term 28%,
// 3-term 11%) because a document appearing in many mediocre sub-rankings outranks
// one placing first in a single precise sub-query. Keeping the best single piece
// of evidence recovers 23 of those 53 at both n=2 and n=3.
//
// What it costs is why it is not the default: on the same gold set it took
// correct rejection from 35% to 0%, because a query that can always find two
// co-occurring words never returns empty.
func (x *Index) searchFusion(text string, k int, after, before string) (SearchOutcome, error) {
	terms := dedupeTerms(ftsTokenRe.FindAllString(text, -1))
	if len(terms) < 2 {
		// One term has no two-term subset, and the fused ranking for it is just
		// that term's own. Fall back rather than return nothing.
		return x.searchAnd(text, k, after, before)
	}

	out := SearchOutcome{Results: []Result{}}
	limit := note.Overfetch
	if k > limit {
		limit = k
	}

	// Keyed by path: a document is one candidate however many subsets found it.
	// The winning subset rides along so its own expression can highlight the
	// snippet — the evidence that ranked the row is the evidence shown.
	type candidate struct {
		row  Result
		expr string
	}
	best := make(map[string]candidate)
	for i := 0; i < len(terms); i++ {
		for j := i + 1; j < len(terms); j++ {
			expr := `"` + terms[i] + `" "` + terms[j] + `"`
			rows, err := x.runMatch(expr, after, before, limit)
			if err != nil {
				// One subset FTS5 will not accept is not a failed search; the
				// other subsets still carry the query.
				continue
			}
			for _, r := range rows {
				if prev, seen := best[r.Path]; !seen || r.Score > prev.row.Score {
					best[r.Path] = candidate{row: r, expr: expr}
				}
			}
		}
	}
	out.Matched = len(best)

	rows := make([]Result, 0, len(best))
	wonBy := make(map[string]string, len(best))
	for path, c := range best {
		rows = append(rows, c.row)
		wonBy[path] = c.expr
	}
	// The penalty is a per-document constant, so applying it once after the max
	// gives the same ordering as applying it to every sub-query and maxing those.
	out.Results = penalizeAndRank(rows, k)

	if err := x.fillFusedSnippets(out.Results, wonBy); err != nil {
		return out, err
	}

	if len(out.Results) == 0 {
		out.Note = "0 results. No two terms of this query appear together in any one note."
	}
	return out, nil
}

// rrfDepth is how deep each arm is read before fusion.
//
// Fifty rather than the full over-fetch window because reciprocal rank decays: a
// document at rank 50 contributes 1/110 against rank 1's 1/61, so the rows below
// it cannot change an ordering, and reading them costs a cosine over the whole
// table for nothing. Fifty per arm still leaves up to a hundred distinct
// candidates, which is the pool the cross-encoder rerank draws its top twenty
// from at the next rung.
const rrfDepth = 50

// searchHybrid runs the lexical and dense arms independently and fuses them by
// reciprocal rank.
//
// The two arms fail in different directions, which is the whole reason for
// running both. Lexical finds a note that shares a rare word and misses one that
// says the same thing differently; dense crosses the vocabulary gap and is
// indifferent to exactly the distinctive token a lexical query was built around.
// RRF asks each arm where a document placed rather than how confident it was, so
// neither arm's score scale can drown the other.
//
// Degrading rather than failing is deliberate. Without a query vector — no model
// installed, a child still loading, a crash-looping server — this returns the
// lexical arm and says so in the note. A caller that asked for hybrid and got
// hybrid-minus-one-arm has a worse search; a caller that got an error has none.
func (x *Index) searchHybrid(text string, k int, after, before string, q Query) (SearchOutcome, error) {
	lexical, err := x.searchFusion(text, rrfDepth, after, before)
	if err != nil {
		return lexical, err
	}

	if len(q.Vector) == 0 {
		out := lexical
		if len(out.Results) > k {
			out.Results = out.Results[:k]
		}
		out.Note = joinNotes(out.Note,
			"hybrid was requested but no query vector was available; "+
				"this is the lexical arm alone (check `agentmd status` for the embedder)")
		return out, nil
	}

	dense, err := x.VectorSearch(q.Vector, q.EmbedModel, rrfDepth, after, before)
	if err != nil {
		return lexical, err
	}
	// The rank penalty is applied inside each arm rather than after fusion. RRF
	// reads positions, so a demotion that lands after the ranks are taken would
	// have no effect at all — the penalized note would already have contributed
	// its rank-1 reciprocal.
	dense = penalizeAndRank(dense, rrfDepth)

	fused := fuseRRF(lexical.Results, dense)
	out := SearchOutcome{Results: fused, Matched: len(fused)}
	if len(out.Results) > k {
		out.Results = out.Results[:k]
	}

	// Snippets come from the lexical arm's own expressions. A note the dense arm
	// found and the lexical arm did not has no matching phrase to highlight —
	// that is what crossing a vocabulary gap means — so it comes back without one
	// rather than with a misleading extract of its opening line.
	byPath := make(map[string]string, len(lexical.Results))
	for _, r := range lexical.Results {
		byPath[r.Path] = r.Snippet
	}
	for i := range out.Results {
		out.Results[i].Snippet = byPath[out.Results[i].Path]
	}

	if len(out.Results) == 0 {
		out.Note = "0 results. Neither the lexical nor the dense arm matched anything."
	}
	return out, nil
}

// fuseRRF combines ranked lists by reciprocal rank fusion.
//
// Each list contributes 1/(k + rank) per document, ranks being 1-based. A
// document in both lists gets both terms, which is the entire mechanism: agreement
// between two independent retrievers is the signal, and neither list's scores are
// consulted.
//
// The returned rows carry the fused score in Score and the arm's own score in
// RawScore, so a call log shows both what ordered the result and what the
// evidence underneath it was.
func fuseRRF(lists ...[]Result) []Result {
	type acc struct {
		row   Result
		score float64
	}
	byPath := map[string]*acc{}
	order := []string{}
	for _, list := range lists {
		for i, r := range list {
			a, seen := byPath[r.Path]
			if !seen {
				a = &acc{row: r}
				byPath[r.Path] = a
				order = append(order, r.Path)
			}
			a.score += 1 / float64(rrfK+i+1)
		}
	}
	out := make([]Result, 0, len(order))
	for _, p := range order {
		a := byPath[p]
		a.row.RawScore = a.row.Score
		a.row.Score = a.score
		out = append(out, a.row)
	}
	// Ties broken by path so the ordering is total and a re-run is identical —
	// RRF produces exact ties routinely, since two documents at the same rank in
	// the same single list get the same score.
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Score != out[j].Score {
			return out[i].Score > out[j].Score
		}
		return out[i].Path < out[j].Path
	})
	return out
}

func joinNotes(a, b string) string {
	switch {
	case a == "":
		return b
	case b == "":
		return a
	}
	return a + "; " + b
}

// fillFusedSnippets highlights each surviving row under the subset that won it,
// grouped so the cost is one query per distinct winning subset rather than one
// per row.
func (x *Index) fillFusedSnippets(rows []Result, wonBy map[string]string) error {
	byExpr := make(map[string][]Result)
	for _, r := range rows {
		expr := wonBy[r.Path]
		byExpr[expr] = append(byExpr[expr], r)
	}
	snippets := make(map[string]string, len(rows))
	for expr, group := range byExpr {
		if err := x.fillSnippets(expr, group); err != nil {
			return err
		}
		for _, r := range group {
			if r.Snippet != "" {
				snippets[r.Path] = r.Snippet
			}
		}
	}
	for i := range rows {
		rows[i].Snippet = snippets[rows[i].Path]
	}
	return nil
}

// dedupeTerms drops repeats while keeping first-seen order, so a query saying the
// same word twice does not pay for a subset pairing it with itself.
func dedupeTerms(terms []string) []string {
	seen := make(map[string]bool, len(terms))
	out := make([]string, 0, len(terms))
	for _, t := range terms {
		key := strings.ToLower(t)
		if seen[key] {
			continue
		}
		seen[key] = true
		out = append(out, t)
	}
	return out
}

// match runs the query, retrying a syntax error with its terms quoted. It
// returns the rows, the match expression FTS5 accepted, and any note for the
// driver. The expression comes back because the snippet pass has to re-issue it.
func (x *Index) match(text, after, before string, limit int) ([]Result, string, string, error) {
	rows, err := x.runMatch(text, after, before, limit)
	if err == nil {
		return rows, text, "", nil
	}
	// A query carrying punctuation FTS5 reads as an operator is a syntax error,
	// not a miss. Re-issue it with every token quoted and the implicit AND
	// preserved — widening it to OR here would quietly reintroduce the rewrite
	// the measurement rejected, on exactly the queries hardest to reason about.
	sanitized := sanitizeQuery(text)
	if sanitized == "" {
		return nil, "", "query contained no searchable terms", nil
	}
	rows, retryErr := x.runMatch(sanitized, after, before, limit)
	if retryErr != nil {
		return nil, "", "", fmt.Errorf("search failed: %w", retryErr)
	}
	return rows, sanitized, fmt.Sprintf(
		"query was not valid FTS5 syntax; searched for its quoted terms instead (%s)",
		sanitized), nil
}

func (x *Index) runMatch(match, after, before string, limit int) ([]Result, error) {
	x.mu.Lock()
	defer x.mu.Unlock()

	// No snippet() here. It belongs to the k rows that survive the re-rank, not
	// to the whole over-fetch window — see Search.
	sql := fmt.Sprintf(`
		SELECT m.id, m.path,
		       bm25(docs, %v, %v, %v, %v) AS s,
		       m.flags, m.captured, m.captured_src
		FROM docs JOIN docmeta m ON m.id = docs.rowid
		WHERE docs MATCH ?
		  AND (? = '' OR m.captured >= ?)
		  AND (? = '' OR m.captured <  ?)
		ORDER BY s
		LIMIT ?`,
		weightPath, weightTitle, weightMeta, weightBody)

	rows, err := x.db.Query(sql, match, after, after, before, before, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []Result
	for rows.Next() {
		var r Result
		var bm float64
		var flags, captured, capturedSrc string
		if err := rows.Scan(&r.rowid, &r.Path, &bm, &flags, &captured, &capturedSrc); err != nil {
			return nil, err
		}
		r.Score = -bm
		r.Penalty = flags
		r.Captured = captured
		r.CapturedSource = capturedSrc
		out = append(out, r)
	}
	return out, rows.Err()
}

// fillSnippets computes the highlighted extract for exactly the rows handed to
// it, keyed by rowid, and writes it into them.
//
// The rowid list is what keeps this cheap: FTS5 seeks straight to those
// documents inside the doclist the match expression already defines, so the
// scan is priced for k documents rather than for the over-fetch window. The
// expression must be the one that produced the rows — snippet() highlights the
// phrases of the query it is run under.
//
// A row whose note was deleted or re-indexed between the two queries comes back
// without a snippet rather than failing the search. The window is microseconds
// wide, the field is decorative, and a note that just changed has no snippet
// worth insisting on.
func (x *Index) fillSnippets(match string, rows []Result) error {
	if match == "" || len(rows) == 0 {
		return nil
	}

	x.mu.Lock()
	defer x.mu.Unlock()

	args := make([]any, 0, len(rows)+1)
	args = append(args, match)
	holders := make([]string, len(rows))
	for i := range rows {
		holders[i] = "?"
		args = append(args, rows[i].rowid)
	}
	sql := fmt.Sprintf(
		`SELECT docs.rowid, snippet(docs, %d, '[', ']', ' … ', 24)
		 FROM docs WHERE docs MATCH ? AND docs.rowid IN (%s)`,
		bodyColumn, strings.Join(holders, ","))

	found, err := x.db.Query(sql, args...)
	if err != nil {
		return err
	}
	defer found.Close()

	byID := make(map[int64]string, len(rows))
	for found.Next() {
		var id int64
		var snippet string
		if err := found.Scan(&id, &snippet); err != nil {
			return err
		}
		byID[id] = snippet
	}
	if err := found.Err(); err != nil {
		return err
	}
	for i := range rows {
		rows[i].Snippet = strings.Join(strings.Fields(byID[rows[i].rowid]), " ")
	}
	x.snippetedDocs += int64(len(rows))
	return nil
}

func splitFlags(s string) []string {
	if s == "" {
		return nil
	}
	return strings.Split(s, ",")
}

// sanitizeQuery quotes every token so nothing in it can be read as an operator,
// and joins with spaces, which is FTS5's implicit AND. Same semantics, no syntax
// error.
func sanitizeQuery(q string) string {
	tokens := ftsTokenRe.FindAllString(q, -1)
	if len(tokens) == 0 {
		return ""
	}
	quoted := make([]string, len(tokens))
	for i, t := range tokens {
		quoted[i] = `"` + t + `"`
	}
	return strings.Join(quoted, " ")
}

var boundLayouts = []string{
	time.RFC3339,
	"2006-01-02T15:04:05",
	"2006-01-02 15:04:05",
	"2006-01-02T15:04",
	"2006-01-02",
}

// normalizeBound turns a caller's date into the stored format, so the comparison
// is a plain indexed string range. A bare date means midnight UTC: `after` is
// inclusive of that day, `before` is exclusive of it.
func normalizeBound(s string) (string, error) {
	s = strings.TrimSpace(s)
	if s == "" {
		return "", nil
	}
	for _, layout := range boundLayouts {
		if t, err := time.Parse(layout, s); err == nil {
			return t.UTC().Format(capturedFormat), nil
		}
	}
	return "", fmt.Errorf("%q is not a date I can read; use YYYY-MM-DD or RFC3339", s)
}
