package index

import (
	"context"
	"fmt"
)

// UnfiledPage returns the next page of notes waiting to be filed, ordered by
// path so a cursor means something.
//
// Ordered by path rather than by capture date, which would be the more obvious
// choice and is the wrong one here. A cursor over a date is ambiguous the moment
// two notes share a timestamp — capture shards by date and a burst writes many
// in the same second — and an ambiguous cursor either repeats work or skips it.
// Paths are unique by construction, because that is what makes them a note's
// identity.
//
// The rows are the index's answer to "what is still unfiled", which is the
// question; reading the files is the caller's job, because the index holds a
// cache of the frontmatter and the enrichment pass needs the bytes.
func (x *Index) UnfiledPage(_ context.Context, after string, limit int) ([]string, error) {
	if limit < 1 {
		limit = 25
	}
	x.mu.Lock()
	defer x.mu.Unlock()

	// A fresh slice rather than appending onto the package-level one: append
	// can write into shared backing storage, and two concurrent callers would
	// then clobber each other's cursor.
	args := make([]any, 0, len(unfiledArgs)+2)
	args = append(args, unfiledArgs...)
	args = append(args, after, limit)
	rows, err := x.db.Query(`
		SELECT path FROM docmeta
		WHERE status IN (`+unfiledPlaceholders+`)
		  AND path > ?
		ORDER BY path
		LIMIT ?`, args...)
	if err != nil {
		return nil, fmt.Errorf("listing unfiled notes: %w", err)
	}
	defer rows.Close()

	var out []string
	for rows.Next() {
		var p string
		if err := rows.Scan(&p); err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	return out, rows.Err()
}
