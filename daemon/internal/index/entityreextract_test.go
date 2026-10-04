package index

import (
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

// Task 186: a change to the extractor reaches the notes already indexed on the
// next open, from the bodies the index holds, and nothing else in the index is
// touched — not the vectors, and not the enrichment ledger that
// `reindex -from-scratch` would discard.
func TestANewExtractorReExtractsTheIndexedNotesInPlaceOnce(t *testing.T) {
	vault := t.TempDir()
	writeNote(t, vault, "projects/agentm/project.yaml", "slug: agentm\nrepositories:\n  - alexherrero/agentm\n")
	writeNote(t, vault, "projects/agentm/tasks/001-a/plan.md",
		"---\ntitle: Plan\n---\n\nExcluded per ROADMAP item #15; shipped in PR #553.\n")
	dbPath := filepath.Join(t.TempDir(), "index.db")
	x, err := Open(dbPath, vault, "", false)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}
	var id int64
	if err := x.db.QueryRow(`SELECT id FROM docmeta WHERE path=?`, "projects/agentm/tasks/001-a/plan.md").Scan(&id); err != nil {
		t.Fatal(err)
	}
	// What an index built by the old extractor holds: "#15" read as the
	// project's issue. And a vector, which a rebuild would have thrown away.
	for _, stmt := range []string{
		`DELETE FROM entities WHERE doc_id = ?`,
		`INSERT INTO entities(entity_uri, doc_id) VALUES('issue:alexherrero/agentm#15', ?)`,
		`INSERT INTO entities(entity_uri, doc_id) VALUES('issue:alexherrero/agentm#553', ?)`,
		`INSERT INTO embeddings(doc_id, chunk_idx, model, dim, mtime_ns, vec) VALUES(?, 0, 'm', 1, 1, x'00')`,
	} {
		if _, err := x.db.Exec(stmt, id); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := x.db.Exec(`UPDATE meta SET value='an-older-extractor' WHERE key=?`, entityExtractorKey); err != nil {
		t.Fatal(err)
	}
	x.Close()

	x, err = Open(dbPath, vault, "", false)
	if err != nil {
		t.Fatal(err)
	}
	defer x.Close()
	got := entityURIs(t, x, id)
	if strings.Join(got, ",") != "issue:#15,issue:alexherrero/agentm#553" {
		t.Errorf("entity rows after the open: %v, want the roadmap number bare and the pull request qualified", got)
	}
	var vectors int
	if err := x.db.QueryRow(`SELECT COUNT(*) FROM embeddings WHERE doc_id = ?`, id).Scan(&vectors); err != nil || vectors != 1 {
		t.Errorf("the note's vector: %d (%v), want it kept", vectors, err)
	}
	var version string
	if err := x.db.QueryRow(`SELECT value FROM meta WHERE key=?`, entityExtractorKey).Scan(&version); err != nil || version != EntityExtractorVersion {
		t.Errorf("recorded extractor version %q (%v), want %q", version, err, EntityExtractorVersion)
	}

	// Once is once: an index already at this version is not re-derived, so a
	// row written since stays until its note changes.
	if _, err := x.db.Exec(`INSERT INTO entities(entity_uri, doc_id) VALUES('repo:a/b', ?)`, id); err != nil {
		t.Fatal(err)
	}
	x.Close()
	x, err = Open(dbPath, vault, "", false)
	if err != nil {
		t.Fatal(err)
	}
	if got := entityURIs(t, x, id); len(got) != 3 {
		t.Errorf("a second open re-derived the rows: %v", got)
	}
}

func entityURIs(t *testing.T, x *Index, id int64) []string {
	t.Helper()
	rows, err := x.db.Query(`SELECT entity_uri FROM entities WHERE doc_id = ?`, id)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var u string
		if err := rows.Scan(&u); err != nil {
			t.Fatal(err)
		}
		out = append(out, u)
	}
	sort.Strings(out)
	return out
}
