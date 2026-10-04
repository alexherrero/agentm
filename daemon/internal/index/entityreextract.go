package index

import (
	"database/sql"
	"errors"
	"fmt"
	"sort"
	"strings"

	"github.com/alexherrero/agentm/daemon/internal/extract"
	"github.com/alexherrero/agentm/daemon/internal/projectbind"
)

// Re-extracting entity rows in place (task 186).
//
// A change to the extractor used to reach the notes already indexed only
// through `reindex -from-scratch`, which also discards every vector, and the
// vectors take hours to rebuild. (The enrichment ledger lives apart, in the
// engine state directory's ledger.db since task 181, and survives a reindex.)
// The entity rows need neither. Every note's body and `project:` label are already in the
// index, so when the extractor's rules change, the next open re-derives the
// rows from them, once, and leaves everything else as it was.

// EntityExtractorVersion names the extractor's rules. Bump it with any change
// to `extract.EntitiesIn` that should reach notes already indexed.
const EntityExtractorVersion = "2026-10-04-bare-issue-floor"

const entityExtractorKey = "entity_extractor_version"

// extractorVersion is the version recorded in the index: the rules, and the
// projects' bare-issue floors when any is set (task 187). The floors are data,
// in each project's `project.yaml`, and changing one changes what the notes
// already indexed mean, so a new floor re-derives them on the next open just as
// a new rule does. With no floor set it is the rules' version alone.
func extractorVersion(vault string) string {
	floors := projectbind.IssueFloors(vault)
	if len(floors) == 0 {
		return EntityExtractorVersion
	}
	repos := make([]string, 0, len(floors))
	for r := range floors {
		repos = append(repos, r)
	}
	sort.Strings(repos)
	var b strings.Builder
	b.WriteString(EntityExtractorVersion + " floors")
	for _, r := range repos {
		fmt.Fprintf(&b, " %s=%d", r, floors[r])
	}
	return b.String()
}

// reextractEntities re-derives every note's entity rows when the index was
// built by another version of the extractor, or under other floors, and
// records this one. An index already at this version is left alone.
func (x *Index) reextractEntities() error {
	want := extractorVersion(x.vault)
	var have string
	err := x.db.QueryRow(`SELECT value FROM meta WHERE key=?`, entityExtractorKey).Scan(&have)
	switch {
	case err == nil && have == want:
		return nil
	case err != nil && !errors.Is(err, sql.ErrNoRows):
		return fmt.Errorf("reading the extractor version: %w", err)
	}

	type noteRow struct {
		id                 int64
		rel, project, body string
	}
	rows, err := x.db.Query(`SELECT m.id, m.path, m.project, d.body FROM docmeta m JOIN docs d ON d.rowid = m.id`)
	if err != nil {
		return fmt.Errorf("reading notes to re-extract: %w", err)
	}
	var notes []noteRow
	for rows.Next() {
		var n noteRow
		var body sql.NullString
		if err := rows.Scan(&n.id, &n.rel, &n.project, &body); err != nil {
			rows.Close()
			return err
		}
		n.body = body.String
		notes = append(notes, n)
	}
	if err := rows.Close(); err != nil {
		return err
	}

	x.mu.Lock()
	defer x.mu.Unlock()
	tx, err := x.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	for _, n := range notes {
		if err := replaceEntitiesTx(tx, n.id, extract.EntitiesIn(n.body, x.entityContextLocked(n.rel, n.project))); err != nil {
			return fmt.Errorf("re-extracting %s: %w", n.rel, err)
		}
	}
	if _, err := tx.Exec(`INSERT INTO meta(key, value) VALUES(?, ?)
		ON CONFLICT(key) DO UPDATE SET value=excluded.value`, entityExtractorKey, want); err != nil {
		return err
	}
	return tx.Commit()
}
