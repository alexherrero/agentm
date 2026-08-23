package index

import (
	"context"
	"fmt"
)

// What the diversity meters read: the most recent memories, with their bodies
// and — where the dense arm has got to them — their vectors.
//
// Recent rather than random, because the meters answer "is what we are writing
// *now* converging". A uniform sample across five years of corpus would mix a
// month of drift into sixty months of history and report almost nothing had
// changed, which is exactly the dilution that lets slow convergence go unnoticed.

// MeterSample is one note as the meters see it.
//
// No body. The index's copy of it lives in an FTS5 virtual table whose path
// column is UNINDEXED, so fetching it by path scans that table once per row —
// measured at 4m1s for five hundred notes against 49ms without. The caller reads
// the bytes from disk, which is both faster and the more truthful source for a
// meter about what the corpus currently says.
type MeterSample struct {
	Rel string
	// Captured is when the note entered the corpus, so the caller can report
	// which window the numbers describe. Four decimals over an unnamed period
	// is a number nobody can act on.
	Captured string
	// Vec is the note's whole-note embedding, or nil if the dense arm has not
	// reached it. Nil rather than a zero vector: the dense meters refuse when
	// they have nothing, and a zero vector would be something.
	Vec []float32
}

// RecentForMeters returns up to n recent notes from the given spaces.
//
// `scope` is the same space list the vector arm uses, so the meters measure the
// part of the corpus the embedder actually covers rather than a wider set whose
// vectors would be missing for a reason that has nothing to do with drift.
//
// Only chunk 0, which is the whole note for everything that fits the embedder's
// window. A long note split into chunks would otherwise contribute several
// points to a distribution about notes, and the notes that split are the long
// ones — so the meters would quietly become a statement about long notes.
// # Why "with vectors" rather than simply "recent"
//
// Measured on the live corpus: of the 500 most recently captured notes, none
// carried a vector; of the newest 2,000, 1,184 did; the oldest 500 were complete.
// The embedder trails capture by one to two thousand notes, so recency alone
// selects almost exactly the notes the dense arm has not reached — which made the
// two embedding meters unable to run at all, every night, while reporting it as a
// missing embedder.
//
// `withVectors` therefore restricts the window to notes that have a current
// chunk-0 vector. The caller passes false when there is no dense arm to wait
// for, so the two lexical meters still run on a corpus that has never been
// embedded.
func (x *Index) RecentForMeters(ctx context.Context, n int, model string,
	scope []string, withVectors bool) ([]MeterSample, error) {
	if n < 1 {
		return nil, nil
	}
	where, args := scopeClause(scope)
	// An inner join rather than a filter in WHERE, so the window is the most
	// recent *embedded* notes rather than the most recent notes of which few
	// happen to be embedded.
	join := "LEFT JOIN"
	if withVectors {
		join = "JOIN"
	}

	x.mu.Lock()
	defer x.mu.Unlock()

	// Ordered by capture date and then by path. The date alone is ambiguous —
	// capture shards by date and a burst writes many in the same second — and an
	// ambiguous order makes the sample, and so every number, depend on which row
	// SQLite happened to return first.
	q := `SELECT m.path, m.captured, e.vec
	        FROM docmeta m
	        ` + join + ` embeddings e
	               ON e.doc_id = m.id AND e.chunk_idx = 0 AND e.model = ?
	                  AND e.mtime_ns = m.mtime_ns
	       WHERE ` + where + `
	    ORDER BY m.captured DESC, m.path DESC
	       LIMIT ?`

	rows, err := x.db.QueryContext(ctx, q, append(append([]any{model}, args...), n)...)
	if err != nil {
		return nil, fmt.Errorf("sampling the corpus for the meters: %w", err)
	}
	defer rows.Close()

	var out []MeterSample
	for rows.Next() {
		var s MeterSample
		var blob []byte
		if err := rows.Scan(&s.Rel, &s.Captured, &blob); err != nil {
			return nil, err
		}
		if len(blob) > 0 {
			s.Vec = decodeVec(blob, nil)
		}
		out = append(out, s)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}

	// Reversed into ascending order, so the sample reads oldest-first the way a
	// person would expect a window of recent notes to. The set is the same; the
	// order is not, and the lexical meters slide a window along it.
	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	return out, nil
}
