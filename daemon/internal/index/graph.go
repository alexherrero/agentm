package index

import (
	"database/sql"
	"fmt"

	"github.com/alexherrero/agentm/daemon/internal/extract"
)

// The link and entity indexes: the two derived tables that make the corpus
// navigable in directions the lexical index cannot answer.
//
// **Links** buy one-hop graph expansion in both directions at lookup cost. The
// forward direction is free from the file; the backward one — "what points *at*
// this note" — is the reason a table exists at all, since answering it from the
// files means reading every file.
//
// **Entities** make a timeline addressable before any `person` type exists.
// Every note mentioning a given issue or repository is one lookup away, and the
// rollup that eventually summarizes it is built from that set rather than from a
// directory scan. No new type is registered, so the taxonomy's growth rule is
// untouched.
//
// Both are caches. Both rebuild from the markdown, and deleting either costs a
// reconcile pass rather than data.

// Link is one row of the link index.
type Link struct {
	// Target is the reference as written, before resolution.
	Target string
	// Resolved is the vault path the target refers to, or "" when nothing in the
	// corpus matches.
	//
	// An unresolved link is recorded rather than dropped. A dangling link is a
	// fact about the corpus — it is what the stub synthesis in a later part reads,
	// and a table that silently discarded them would make that pass blind.
	Resolved string
	Text     string
	Context  string
	Wiki     bool
}

// BuildLinks extracts and resolves one note's outbound references.
func BuildLinks(fromPath, body string, known []string) []Link {
	found := extract.Links(body)
	out := make([]Link, 0, len(found))
	for _, l := range found {
		out = append(out, Link{
			Target:   l.Target,
			Resolved: extract.ResolveTarget(l.Target, fromPath, known),
			Text:     l.Text,
			Context:  l.Context,
			Wiki:     l.Wiki,
		})
	}
	return out
}

func replaceLinksTx(tx *sql.Tx, docID int64, links []Link) error {
	if _, err := tx.Exec(`DELETE FROM links WHERE source_id = ?`, docID); err != nil {
		return fmt.Errorf("clearing links for doc %d: %w", docID, err)
	}
	if len(links) == 0 {
		return nil
	}
	stmt, err := tx.Prepare(`INSERT INTO links
		(source_id, target, resolved, text, context, wiki) VALUES (?, ?, ?, ?, ?, ?)`)
	if err != nil {
		return err
	}
	defer stmt.Close()

	for _, l := range links {
		if _, err := stmt.Exec(docID, l.Target, l.Resolved, l.Text, l.Context, l.Wiki); err != nil {
			return fmt.Errorf("inserting link %q for doc %d: %w", l.Target, docID, err)
		}
	}
	return nil
}

func replaceEntitiesTx(tx *sql.Tx, docID int64, uris []string) error {
	if _, err := tx.Exec(`DELETE FROM entities WHERE doc_id = ?`, docID); err != nil {
		return fmt.Errorf("clearing entities for doc %d: %w", docID, err)
	}
	if len(uris) == 0 {
		return nil
	}
	stmt, err := tx.Prepare(`INSERT INTO entities (entity_uri, doc_id) VALUES (?, ?)`)
	if err != nil {
		return err
	}
	defer stmt.Close()

	for _, uri := range uris {
		if _, err := stmt.Exec(uri, docID); err != nil {
			return fmt.Errorf("inserting entity %q for doc %d: %w", uri, docID, err)
		}
	}
	return nil
}

// LinksFrom returns one note's outbound references.
func (x *Index) LinksFrom(docID int64) ([]Link, error) {
	rows, err := x.db.Query(
		`SELECT target, resolved, text, context, wiki FROM links
		 WHERE source_id = ? ORDER BY target, text`, docID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanLinks(rows)
}

// Backlinks returns every note pointing at `path`, with the text and context of
// the reference — which is most of what makes a backlink worth having, since a
// bare edge tells you two notes are connected and nothing about how.
func (x *Index) Backlinks(path string) ([]Link, error) {
	rows, err := x.db.Query(
		`SELECT l.target, d.path, l.text, l.context, l.wiki
		 FROM links l JOIN docmeta d ON d.id = l.source_id
		 WHERE l.resolved = ? ORDER BY d.path, l.text`, path)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	// `Resolved` carries the *source* path here: from the target's point of view
	// the interesting path is where the link came from.
	return scanLinks(rows)
}

func scanLinks(rows *sql.Rows) ([]Link, error) {
	var out []Link
	for rows.Next() {
		var l Link
		if err := rows.Scan(&l.Target, &l.Resolved, &l.Text, &l.Context, &l.Wiki); err != nil {
			return nil, err
		}
		out = append(out, l)
	}
	return out, rows.Err()
}

// DanglingLinks returns every reference that resolved to nothing, newest doc
// first. This is the queue the stub synthesis reads.
func (x *Index) DanglingLinks() ([]string, error) {
	rows, err := x.db.Query(
		`SELECT DISTINCT target FROM links WHERE resolved = '' ORDER BY target`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []string
	for rows.Next() {
		var s string
		if err := rows.Scan(&s); err != nil {
			return nil, err
		}
		out = append(out, s)
	}
	return out, rows.Err()
}

// NotesMentioning returns every note carrying a reference to `uri`.
func (x *Index) NotesMentioning(uri string) ([]string, error) {
	rows, err := x.db.Query(
		`SELECT d.path FROM entities e JOIN docmeta d ON d.id = e.doc_id
		 WHERE e.entity_uri = ? ORDER BY d.path`, uri)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []string
	for rows.Next() {
		var s string
		if err := rows.Scan(&s); err != nil {
			return nil, err
		}
		out = append(out, s)
	}
	return out, rows.Err()
}

// docID resolves a vault-relative path to its index id.
func (x *Index) docID(rel string) (int64, error) {
	var id int64
	err := x.db.QueryRow(`SELECT id FROM docmeta WHERE path = ?`, rel).Scan(&id)
	return id, err
}

// CountLinks and CountEntities are what the rebuild gate and the status surface
// ask.
func (x *Index) CountLinks() (int, error)    { return x.countRows("links") }
func (x *Index) CountEntities() (int, error) { return x.countRows("entities") }

func (x *Index) countRows(table string) (int, error) {
	var n int
	// The table name is a compile-time constant from this file, never caller
	// input — the two call sites above are the only ones.
	err := x.db.QueryRow(`SELECT COUNT(*) FROM ` + table).Scan(&n)
	if err == sql.ErrNoRows {
		return 0, nil
	}
	return n, err
}
