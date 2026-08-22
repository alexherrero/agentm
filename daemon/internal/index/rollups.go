package index

import (
	"context"
	"fmt"
	"strings"
)

// What the entity-rollup stage needs to know, and what it must not be told.
//
// A rollup is an entity file built from the facts that mention it. Deciding
// which entities deserve one is a judgment about the corpus; finding out which
// entities are mentioned, how often, and whether a file already exists is a
// query. This is the query, and it deliberately stops there — the threshold, the
// class the file belongs in and the shape of the file itself all come from the
// filing contract, and answering them here would be a second place the contract
// lives.

// EntityMention is one entity the corpus refers to.
type EntityMention struct {
	// URI is the entity's namespaced reference, as the extractor recorded it.
	URI string `json:"uri"`
	// Mentions is how many distinct notes refer to it. The entity index is keyed
	// (entity_uri, doc_id), so a note mentioning something three times is one
	// mention — which is the number that means "how much of the corpus is about
	// this", rather than "how wordy one note was".
	Mentions int `json:"mentions"`
	// File is the entity's own note, when the corpus has one. Empty means the
	// rollup for this entity does not exist yet.
	File string `json:"file,omitempty"`
}

// HasFile reports whether this entity already has a note of its own.
func (e EntityMention) HasFile() bool { return e.File != "" }

// EntityMentions lists what the corpus refers to, most-mentioned first.
//
// `min` filters by mention count, because the interesting question is always
// "what is mentioned enough to deserve a file", and pulling every one-off
// reference across a fifteen-thousand-note corpus to filter it in the caller
// would move a lot of rows to answer a question the query can answer.
//
// The file match is a path convention rather than a lookup: an entity's note
// lives under an `entities/` segment with a stem matching the URI's last
// segment. That is a heuristic, and it is deliberately the loose direction — a
// missed match proposes a rollup for an entity that has one, which a human sees
// and dismisses, while a false match silently skips an entity that has none.
func (x *Index) EntityMentions(ctx context.Context, min int) ([]EntityMention, error) {
	if min < 1 {
		min = 1
	}
	x.mu.Lock()
	defer x.mu.Unlock()

	rows, err := x.db.QueryContext(ctx, `
		SELECT entity_uri, count(*) AS n
		FROM entities
		GROUP BY entity_uri
		HAVING n >= ?
		ORDER BY n DESC, entity_uri`, min)
	if err != nil {
		return nil, fmt.Errorf("index: listing entity mentions: %w", err)
	}
	defer rows.Close()

	var out []EntityMention
	for rows.Next() {
		var e EntityMention
		if err := rows.Scan(&e.URI, &e.Mentions); err != nil {
			return nil, err
		}
		out = append(out, e)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}

	for i := range out {
		file, err := x.entityFileLocked(ctx, out[i].URI)
		if err != nil {
			return nil, err
		}
		out[i].File = file
	}
	return out, nil
}

// entityFileLocked finds an entity's own note, if the corpus has one.
//
// Matched on the path's last segment rather than on the whole URI, because the
// URI carries a namespace the filesystem does not — `person:ada-lovelace` lives
// at `.../entities/ada-lovelace.md`, and nothing writes the namespace into the
// filename.
func (x *Index) entityFileLocked(ctx context.Context, uri string) (string, error) {
	stem := uri
	if i := strings.LastIndexAny(stem, ":/"); i >= 0 {
		stem = stem[i+1:]
	}
	if stem == "" {
		return "", nil
	}
	var path string
	err := x.db.QueryRowContext(ctx, `
		SELECT path FROM docmeta
		WHERE path LIKE '%/entities/%' AND lower(path) LIKE '%/' || lower(?) || '.md'
		ORDER BY path LIMIT 1`, stem).Scan(&path)
	if err != nil {
		// No row is the ordinary answer: most mentioned entities have no file,
		// which is the whole reason the rollup stage exists.
		return "", nil
	}
	return path, nil
}

// DanglingTarget is a wikilink pointing at nothing, and who points at it.
type DanglingTarget struct {
	Target string `json:"target"`
	// Sources are the notes that link to it, so a stub can say where the
	// expectation came from rather than appearing from nowhere.
	Sources []string `json:"sources"`
	// Contexts are the sentences the links sit in, capped. A stub written from
	// the link text alone says only its own title back; the surrounding sentence
	// is what makes it worth synthesizing at all.
	Contexts []string `json:"contexts,omitempty"`
}

// maxStubContexts bounds how much surrounding text one dangling target carries.
const maxStubContexts = 5

// DanglingTargets lists what the corpus expects to exist and does not.
//
// The stub-synthesis stage's input. A dangling link is a fact about the corpus —
// somebody wrote `[[x]]` and meant it — which is why the link index keeps
// unresolved rows rather than dropping them.
func (x *Index) DanglingTargets(ctx context.Context, min int) ([]DanglingTarget, error) {
	if min < 1 {
		min = 1
	}
	x.mu.Lock()
	defer x.mu.Unlock()

	rows, err := x.db.QueryContext(ctx, `
		SELECT l.target, d.path, l.context
		FROM links l JOIN docmeta d ON d.id = l.source_id
		WHERE l.resolved = ''
		ORDER BY l.target, d.path`)
	if err != nil {
		return nil, fmt.Errorf("index: listing dangling links: %w", err)
	}
	defer rows.Close()

	byTarget := map[string]*DanglingTarget{}
	var order []string
	for rows.Next() {
		var target, source, context string
		if err := rows.Scan(&target, &source, &context); err != nil {
			return nil, err
		}
		t, ok := byTarget[target]
		if !ok {
			t = &DanglingTarget{Target: target}
			byTarget[target] = t
			order = append(order, target)
		}
		// One entry per source note, not per link. A note that links to the same
		// missing target three times expects it once.
		if len(t.Sources) == 0 || t.Sources[len(t.Sources)-1] != source {
			t.Sources = append(t.Sources, source)
		}
		if c := strings.TrimSpace(context); c != "" && len(t.Contexts) < maxStubContexts {
			t.Contexts = append(t.Contexts, c)
		}
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}

	var out []DanglingTarget
	for _, target := range order {
		t := byTarget[target]
		if len(t.Sources) >= min {
			out = append(out, *t)
		}
	}
	return out, nil
}
