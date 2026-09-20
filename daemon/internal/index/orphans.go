package index

import (
	"database/sql"
	"fmt"
	"strings"
	"time"
)

// What a document owns, and what is left when it goes.
//
// Every derived table here is keyed by docmeta's id, and nothing in the schema
// ties them to it: there is no foreign key, and SQLite would not enforce one
// unasked. A table is cleared when a note is deleted because `Delete` names it
// and for no other reason. `embeddings` was named. `chunks`, `links` and
// `entities` arrived later, each under a comment saying the docmeta delete
// "takes its rows with it", and were not.
//
// Counted on the operator's index on 2026-09-19: 27,891 of 46,581 chunk rows,
// 11,898 entity rows and 8,145 link rows belonged to 4,209 documents that no
// longer existed — every purged note, every path a migration retired, and every
// note `recall_exempt_areas` had walled. No search could reach them, since
// every ranked read joins docmeta and an AUTOINCREMENT id is never reused. They
// were still the text of notes the contract says are never indexed, sitting in
// the index; and the two reads that do not join — `EntityMentions` and
// `DanglingLinks` — counted them.

// perDocumentTables is every table holding rows that belong to one document,
// with the column carrying docmeta's id.
//
// One list, read by `Delete` and by the sweep, so a table is cleared by both or
// by neither. "Neither" is what TestADeletedDocumentLeavesNoRowInAnyTable is
// for: it reads the schema rather than this list, so a table added to `migrate`
// and not here fails it.
//
// The other tables in this file are not on it and must not be. `ledger`,
// `queue` and `sources` belong to dreaming rather than to the corpus: they are
// keyed by path and by source identity, not by a document id, and they record
// what a pass did rather than what a note contains. A deleted note's ledger row
// is a fact about a night's work, which `agentmd ledger --rebuild` owns.
// `meta` holds the schema version.
// Ordered cheapest-probe-first, because the sweep asks each in turn whether it
// holds an orphan and stops at the first yes. `docs` is last: it is the FTS5
// virtual table, where `rowid NOT IN (…)` has no plan and scans, while the
// other four answer off a covering index in microseconds.
var perDocumentTables = []struct{ table, key string }{
	{"embeddings", "doc_id"},
	{"chunks", "doc_id"},
	{"links", "source_id"},
	{"entities", "doc_id"},
	{"docs", "rowid"},
}

// deleteDocumentRowsTx removes everything one document owns, and un-resolves
// every link that named it, inside the caller's transaction. The docmeta row
// itself is the caller's to delete, last.
func deleteDocumentRowsTx(tx *sql.Tx, id int64, rel string) error {
	for _, t := range perDocumentTables {
		if _, err := tx.Exec(`DELETE FROM `+t.table+` WHERE `+t.key+` = ?`, id); err != nil {
			return fmt.Errorf("clearing %s for %s: %w", t.table, rel, err)
		}
	}
	// `resolved` means "the path in the corpus this link refers to, or empty when
	// nothing matches". Once this note is gone nothing matches. The row is another
	// note's and stays; it goes back to dangling, which is what it now is, and
	// `ResolveDangling` finds the note again if it returns — a sync client moving
	// a file into place deletes and re-adds it a pass apart.
	if _, err := tx.Exec(`UPDATE links SET resolved = '' WHERE resolved = ?`, rel); err != nil {
		return fmt.Errorf("un-resolving links to %s: %w", rel, err)
	}
	return nil
}

// SweepReport is what one orphan sweep removed.
type SweepReport struct {
	// Removed is rows deleted per table, for every table in perDocumentTables —
	// zeros included, so a reader can tell "clean" from "not looked at".
	Removed map[string]int
	// Unresolved is how many live link rows named a path the index no longer
	// holds and were set back to dangling.
	Unresolved int
	Elapsed    time.Duration
}

// Total is every row the sweep deleted. Un-resolved links are not in it: those
// rows were kept.
func (r SweepReport) Total() int {
	n := 0
	for _, removed := range r.Removed {
		n += removed
	}
	return n
}

// String is the one line `agentmd reindex` prints and the daemon logs.
func (r SweepReport) String() string {
	if r.Total() == 0 && r.Unresolved == 0 {
		return "nothing to remove"
	}
	var parts []string
	for _, t := range perDocumentTables {
		if n := r.Removed[t.table]; n > 0 {
			parts = append(parts, fmt.Sprintf("%s %d", t.table, n))
		}
	}
	out := fmt.Sprintf("removed %d row(s) whose document is gone", r.Total())
	if len(parts) > 0 {
		out += " (" + strings.Join(parts, ", ") + ")"
	}
	if r.Unresolved > 0 {
		out += fmt.Sprintf("; un-resolved %d link(s) to a path the index no longer holds", r.Unresolved)
	}
	return out
}

// SweepOrphans removes every row, in every per-document table, whose document
// no longer exists, and sets every link naming a vanished path back to
// dangling.
//
// With `Delete` clearing what it should, this finds nothing. It exists for the
// rows an index already carries, and as the backstop for whatever writer leaks
// next: the index is a cache that outlives the binary that built it, so a fix
// to `Delete` alone leaves every existing machine exactly as it was.
//
// It is a backstop and not the mechanism. A note's rows have to go with its
// docmeta row, in that transaction, because the id is the join key and a note
// re-added between two sweeps takes a fresh one — so rows left for a sweep are
// unreachable from the moment the docmeta row goes, and if the sweep never runs
// they are unreachable forever, which is the state this found the index in.
//
// In place, by design. The enrichment ledger and the work queue are tables in
// this same file, so "delete the index and rebuild" is not the cheap answer it
// is for a schema bump — it costs a ledger rebuild and a full re-embed to remove
// rows that five statements remove.
//
// A clean index costs one existence check per table and no write lock. The
// looking is done outside any transaction; only when it finds something does a
// transaction open, and its first statement is a write, so it queues for the
// lock rather than upgrading a read snapshot another process has since written
// past. The probes stop at the first dirty table, and `docs` is probed last
// because it is the one that is not cheap: it is an FTS5 virtual table, and
// `rowid NOT IN (…)` has no plan over one, so it scans. Measured at 182ms cold
// and 10ms warm over 8,000 notes, against microseconds for the five that have a
// covering index. That cost is paid only on a clean index, where a resident
// daemon's pages are warm, and skipped entirely as soon as a cheap probe
// answers yes.
//
// Called from `Reconcile` — the daemon's startup pass, its five-minute pass and
// `agentmd reindex` — and not from `Open`. The check is cheap enough for Open;
// the work behind it is not, and every one-shot read opens the index too. A
// `agentmd search` on the prompt-submit hook's 300ms budget would have taken the
// write lock and deleted forty thousand rows on its way to answering a query,
// the first time anyone searched after an upgrade.
func (x *Index) SweepOrphans() (SweepReport, error) {
	started := time.Now()
	rep := SweepReport{Removed: make(map[string]int, len(perDocumentTables))}
	for _, t := range perDocumentTables {
		rep.Removed[t.table] = 0
	}

	x.mu.Lock()
	defer x.mu.Unlock()

	const gone = ` NOT IN (SELECT id FROM docmeta)`
	const staleLink = `resolved != '' AND resolved NOT IN (SELECT path FROM docmeta)`

	dirty := false
	for _, t := range perDocumentTables {
		if err := x.db.QueryRow(
			`SELECT EXISTS(SELECT 1 FROM ` + t.table + ` WHERE ` + t.key + gone + `)`,
		).Scan(&dirty); err != nil {
			return rep, fmt.Errorf("looking for orphans in %s: %w", t.table, err)
		}
		if dirty {
			// One dirty table sweeps them all, so asking the rest — `docs`
			// especially — buys nothing.
			break
		}
	}
	if !dirty {
		if err := x.db.QueryRow(
			`SELECT EXISTS(SELECT 1 FROM links WHERE ` + staleLink + `)`).Scan(&dirty); err != nil {
			return rep, fmt.Errorf("looking for links to vanished paths: %w", err)
		}
	}
	if !dirty {
		rep.Elapsed = time.Since(started)
		return rep, nil
	}

	tx, err := x.db.Begin()
	if err != nil {
		return rep, err
	}
	defer tx.Rollback()

	// Counted into a local, and moved into the report only once the commit
	// returns. `defer tx.Rollback()` undoes every delete on any error below,
	// including a failing commit — so a report filled in as the statements ran
	// would credit the sweep with rows still sitting in their tables, and the
	// daemon would log that same false number every five minutes forever while
	// nothing was ever removed.
	removed := make(map[string]int, len(perDocumentTables))
	for _, t := range perDocumentTables {
		res, err := tx.Exec(`DELETE FROM ` + t.table + ` WHERE ` + t.key + gone)
		if err != nil {
			return rep, fmt.Errorf("sweeping %s: %w", t.table, err)
		}
		n, err := res.RowsAffected()
		if err != nil {
			return rep, err
		}
		removed[t.table] = int(n)
	}
	// After the deletes, so every row this touches belongs to a live note.
	res, err := tx.Exec(`UPDATE links SET resolved = '' WHERE ` + staleLink)
	if err != nil {
		return rep, fmt.Errorf("un-resolving links to vanished paths: %w", err)
	}
	unresolved, err := res.RowsAffected()
	if err != nil {
		return rep, err
	}

	if err := tx.Commit(); err != nil {
		return rep, fmt.Errorf("committing the sweep: %w", err)
	}
	for table, n := range removed {
		rep.Removed[table] = n
	}
	rep.Unresolved = int(unresolved)
	rep.Elapsed = time.Since(started)
	return rep, nil
}
