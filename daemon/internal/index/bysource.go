package index

import (
	"context"
	"fmt"
)

// BySource lists the memories one unit of source material produced.
//
// This is what makes re-ingestion source-scoped rather than duplicating. When a
// source is deliberately re-mined at a better version, the new distillation has
// to supersede the old one, and "the old one" means exactly the notes carrying
// this source id — not the notes that look similar, and not everything captured
// that day.
//
// Ordered by path so the answer is stable. Two runs over an unchanged corpus
// that returned the same set in different orders would make every diff of a
// supersession noisier than the change it describes.
//
// The match is exact. A source id is a namespaced identity, and a prefix or
// substring match over `url:https://example.com` would sweep in every deeper
// path on the same host — which is the direction of error that supersedes
// memories a re-ingest never touched.
func (x *Index) BySource(ctx context.Context, source string) ([]string, error) {
	if source == "" {
		// An empty source would match every note that has none, which is most of
		// the corpus. Refused rather than returned: a supersession scoped to
		// "everything without a source" is not a scope.
		return nil, fmt.Errorf("index: an empty source matches every note that " +
			"has none, which is not a scope any supersession should have")
	}
	x.mu.Lock()
	defer x.mu.Unlock()

	rows, err := x.db.QueryContext(ctx,
		`SELECT path FROM docmeta WHERE source = ? ORDER BY path`, source)
	if err != nil {
		return nil, fmt.Errorf("index: listing memories from %s: %w", source, err)
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

// SourceProvenance is what the corpus records about one unit of source material.
type SourceProvenance struct {
	Source   string
	Hash     string
	Version  string
	Memories int
}

// Provenance is the rebuild path's input.
//
// Every memory names the unit it came from, the content that unit had when it
// was read, and the pass that read it — so scanning what the index already
// caches recovers the registry without re-reading a single email.
//
// One row per source. The hash and version come from the newest memory the unit
// produced, rather than from any of them: a source re-ingested at a better
// version leaves memories from both passes for as long as the older ones are
// superseded rather than deleted, and what the registry should report is where
// that source stands now.
func (x *Index) Provenance(ctx context.Context) ([]SourceProvenance, error) {
	x.mu.Lock()
	defer x.mu.Unlock()

	// `max(id)` picks the newest row per source, and SQLite's bare-column rule
	// makes the other selected columns come from that same row. Ordered by
	// source so a rebuild over an unchanged corpus produces an identical report.
	rows, err := x.db.QueryContext(ctx, `
		SELECT source, source_hash, source_version, count(*), max(id)
		FROM docmeta
		WHERE source <> ''
		GROUP BY source
		ORDER BY source`)
	if err != nil {
		return nil, fmt.Errorf("index: reading source provenance: %w", err)
	}
	defer rows.Close()

	var out []SourceProvenance
	for rows.Next() {
		var p SourceProvenance
		var newest int64
		if err := rows.Scan(&p.Source, &p.Hash, &p.Version, &p.Memories, &newest); err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	return out, rows.Err()
}

// Sources lists every distinct source the corpus records, with how many
// memories each produced.
func (x *Index) Sources(ctx context.Context) (map[string]int, error) {
	x.mu.Lock()
	defer x.mu.Unlock()

	rows, err := x.db.QueryContext(ctx, `
		SELECT source, count(*) FROM docmeta
		WHERE source <> '' GROUP BY source ORDER BY source`)
	if err != nil {
		return nil, fmt.Errorf("index: listing sources: %w", err)
	}
	defer rows.Close()

	out := map[string]int{}
	for rows.Next() {
		var src string
		var n int
		if err := rows.Scan(&src, &n); err != nil {
			return nil, err
		}
		out[src] = n
	}
	return out, rows.Err()
}
