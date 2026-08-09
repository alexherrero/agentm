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
}

// Query is one search request.
type Query struct {
	Text string
	K    int
	// After and Before bound the capture date. Episodic questions are time
	// questions, and until now the driver had no way to express one — it is the
	// second-weakest stratum at 0.42–0.54 R@5.
	After  string
	Before string
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

// Search runs BM25 over FTS5, applies the measured rank penalty, and returns the
// top k.
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

	limit := note.Overfetch
	if k > limit {
		limit = k
	}

	rows, note1, err := x.match(text, after, before, limit)
	if err != nil {
		return out, err
	}
	out.Note = note1
	out.Matched = len(rows)

	// The penalty. Roughly twenty lines, worth +3.75 points of R@5 at p = 0.0195.
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
	out.Results = rows

	if len(out.Results) == 0 && out.Note == "" {
		out.Note = "0 results. FTS5 requires every term to appear in the same note, " +
			"so a long phrasing often matches nothing — try two or three distinctive " +
			"words, then a different vocabulary for the same idea. Answer \"nothing " +
			"found\" only after distinct vocabularies have failed."
	}
	return out, nil
}

func (x *Index) match(text, after, before string, limit int) ([]Result, string, error) {
	rows, err := x.runMatch(text, after, before, limit)
	if err == nil {
		return rows, "", nil
	}
	// A query carrying punctuation FTS5 reads as an operator is a syntax error,
	// not a miss. Re-issue it with every token quoted and the implicit AND
	// preserved — widening it to OR here would quietly reintroduce the rewrite
	// the measurement rejected, on exactly the queries hardest to reason about.
	sanitized := sanitizeQuery(text)
	if sanitized == "" {
		return nil, "query contained no searchable terms", nil
	}
	rows, retryErr := x.runMatch(sanitized, after, before, limit)
	if retryErr != nil {
		return nil, "", fmt.Errorf("search failed: %w", retryErr)
	}
	return rows, fmt.Sprintf(
		"query was not valid FTS5 syntax; searched for its quoted terms instead (%s)",
		sanitized), nil
}

func (x *Index) runMatch(match, after, before string, limit int) ([]Result, error) {
	x.mu.Lock()
	defer x.mu.Unlock()

	sql := fmt.Sprintf(`
		SELECT m.path,
		       bm25(docs, %v, %v, %v, %v) AS s,
		       snippet(docs, %d, '[', ']', ' … ', 24),
		       m.flags, m.captured, m.captured_src
		FROM docs JOIN docmeta m ON m.id = docs.rowid
		WHERE docs MATCH ?
		  AND (? = '' OR m.captured >= ?)
		  AND (? = '' OR m.captured <  ?)
		ORDER BY s
		LIMIT ?`,
		weightPath, weightTitle, weightMeta, weightBody, bodyColumn)

	rows, err := x.db.Query(sql, match, after, after, before, before, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []Result
	for rows.Next() {
		var r Result
		var bm float64
		var snippet, flags, captured, capturedSrc string
		if err := rows.Scan(&r.Path, &bm, &snippet, &flags, &captured, &capturedSrc); err != nil {
			return nil, err
		}
		r.Score = -bm
		r.Penalty = flags
		r.Captured = captured
		r.CapturedSource = capturedSrc
		r.Snippet = strings.Join(strings.Fields(snippet), " ")
		out = append(out, r)
	}
	return out, rows.Err()
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
