package index

import (
	"context"
	"fmt"
)

// The review queue is a query, not a directory.
//
// The design's ruling, and it is a reversal of what this vault used to do: a
// `personal/_inbox/` directory collected every low-confidence extraction and
// nothing ever moved anything out of it again. It reached 9,860 notes. The
// mechanism failed in a specific way — a note in an inbox is *somewhere else*,
// so it is not in search results, so nobody encounters it, so nobody triages it,
// so the queue only grows.
//
// A note filed in its class folder with `status: unfiled` and a confidence score
// has the opposite property. It is fully indexed and fully searchable, carrying
// only a rank penalty, so it turns up while somebody is looking for something
// adjacent — which is when a person is actually equipped to judge it. And
// "what needs review" stops being a place that has to be emptied and becomes a
// question that can be asked.

// ReviewItem is one note waiting for a judgment.
type ReviewItem struct {
	Path string `json:"path"`
	// Confidence is what the enrichment pass reported. Zero means the note
	// carries no score — an unattended capture that enrichment has not reached
	// yet, which is a different thing from a note enrichment judged and doubted.
	Confidence float64 `json:"confidence"`
	// Scored distinguishes those two: a note with `confidence: 0.0` in its
	// frontmatter and a note with no such key both read as zero otherwise.
	Scored bool `json:"scored"`
}

// ReviewQueue returns the notes waiting for a judgment, least confident first.
//
// Ordered by confidence rather than by date because the queue is a work list
// rather than a log: the note the system was least sure about is the one a
// person adds the most by looking at. Notes with no score sort last — they have
// not been judged badly, they have not been judged at all, and the batch pass
// will reach them without anybody's help.
func (x *Index) ReviewQueue(_ context.Context, limit int) ([]ReviewItem, error) {
	if limit < 1 {
		limit = 50
	}
	x.mu.Lock()
	defer x.mu.Unlock()

	args := make([]any, 0, len(unfiledArgs)+1)
	args = append(args, unfiledArgs...)
	args = append(args, limit)

	rows, err := x.db.Query(`
		SELECT path, confidence, confidence_set FROM docmeta
		WHERE status IN (`+unfiledPlaceholders+`)
		ORDER BY confidence_set DESC, confidence ASC, path
		LIMIT ?`, args...)
	if err != nil {
		return nil, fmt.Errorf("reading the review queue: %w", err)
	}
	defer rows.Close()

	var out []ReviewItem
	for rows.Next() {
		var it ReviewItem
		var scored int
		if err := rows.Scan(&it.Path, &it.Confidence, &scored); err != nil {
			return nil, err
		}
		it.Scored = scored == 1
		out = append(out, it)
	}
	return out, rows.Err()
}
