package index

import (
	"context"
	"fmt"
	"strings"
	"time"
)

// What the nightly entity builder reads (task 179, agentm-vault § Dreaming,
// "Entity pages are built each night"). The builder decides which entities get
// a page and what the page lists; this only answers which notes mention what,
// and how each note is dated and labelled.

// NoteRow is one indexed note as the entity builder groups it.
type NoteRow struct {
	Path  string
	Title string
	// Flags are the index's ranking flags, comma-joined: the builder skips a
	// note flagged `lifecycle-superseded`.
	Flags string
	// Project is the note's `project:` label as written; its place is the
	// caller's to read first.
	Project string
	// Date is the note's own day, YYYY-MM-DD: its `updated`, else its
	// `created`, else when it was captured, else its file's modification day.
	Date string
}

// EntityRow is one (entity, note) pair.
type EntityRow struct {
	URI string
	NoteRow
}

// EntityRows lists every entity reference with the note it sits in, ordered by
// entity and then path, so two builds over one index read the same sequence.
func (x *Index) EntityRows(ctx context.Context) ([]EntityRow, error) {
	x.mu.Lock()
	defer x.mu.Unlock()
	rows, err := x.db.QueryContext(ctx, `
		SELECT e.entity_uri, m.path, d.title, m.flags, m.project, m.updated, m.created, m.captured, m.mtime_ns
		FROM entities e
		JOIN docmeta m ON m.id = e.doc_id
		JOIN docs d ON d.rowid = e.doc_id
		ORDER BY e.entity_uri, m.path`)
	if err != nil {
		return nil, fmt.Errorf("index: listing entity rows: %w", err)
	}
	defer rows.Close()
	var out []EntityRow
	for rows.Next() {
		var r EntityRow
		var updated, created, captured string
		var mtimeNS int64
		if err := rows.Scan(&r.URI, &r.Path, &r.Title, &r.Flags, &r.Project, &updated, &created, &captured, &mtimeNS); err != nil {
			return nil, err
		}
		r.Date = noteDay(updated, created, captured, mtimeNS)
		out = append(out, r)
	}
	return out, rows.Err()
}

// NoteRows lists every indexed note, ordered by path.
func (x *Index) NoteRows(ctx context.Context) ([]NoteRow, error) {
	x.mu.Lock()
	defer x.mu.Unlock()
	rows, err := x.db.QueryContext(ctx, `
		SELECT m.path, d.title, m.flags, m.project, m.updated, m.created, m.captured, m.mtime_ns
		FROM docmeta m JOIN docs d ON d.rowid = m.id
		ORDER BY m.path`)
	if err != nil {
		return nil, fmt.Errorf("index: listing notes: %w", err)
	}
	defer rows.Close()
	var out []NoteRow
	for rows.Next() {
		var r NoteRow
		var updated, created, captured string
		var mtimeNS int64
		if err := rows.Scan(&r.Path, &r.Title, &r.Flags, &r.Project, &updated, &created, &captured, &mtimeNS); err != nil {
			return nil, err
		}
		r.Date = noteDay(updated, created, captured, mtimeNS)
		out = append(out, r)
	}
	return out, rows.Err()
}

// noteDay is the first of the note's own dates that reads as a day.
func noteDay(updated, created, captured string, mtimeNS int64) string {
	for _, v := range []string{updated, created, captured} {
		v = strings.Trim(strings.TrimSpace(v), `"'`)
		if len(v) >= 10 {
			if _, err := time.Parse("2006-01-02", v[:10]); err == nil {
				return v[:10]
			}
		}
	}
	if mtimeNS > 0 {
		return time.Unix(0, mtimeNS).UTC().Format("2006-01-02")
	}
	return ""
}
