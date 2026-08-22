package index

import (
	"context"
	"fmt"

	"github.com/alexherrero/agentm/daemon/internal/extract"
)

// Re-resolving the links that pointed at nothing when they were written.
//
// Links resolve at index time against the paths the index knew then, which
// makes resolution a point-in-time answer. The intent was always that a link
// written before its target existed would find it on the next reconcile — and
// that is not what happened, because Reconcile skips a file whose mtime and
// size are unchanged. A link only re-resolved when its *source* was edited.
//
// The order that exposes it is the ordinary one. You link to something because
// you are about to write it, so the source almost always predates the target,
// and nothing edits the source again afterwards. The result was systematic
// rather than occasional: `Backlinks` returned nothing for those pairs, and the
// stub-synthesis stage proposed creating notes that already existed.
//
// # What this does not fix
//
// A link already resolved stays resolved, even if a better candidate appears
// later. Resolution runs against the paths indexed before the linking note, so
// with two equally-specific candidates the answer depends on which the walk
// reached first — and the sibling tiebreak that is supposed to separate them
// only sees the candidates that existed at the time.
//
// Left alone deliberately. Re-resolving settled links would make a note's
// backlinks change under it on an unrelated write, and a resolution that moves
// is worse than one that is merely not the nearest: the first is churn nobody
// asked for, the second is a link that points at a real note.
//
// # Why this re-resolves rows rather than re-reading notes
//
// Resolution is a function of the target, the note it came from, and the set of
// known paths — all three of which are already in the database. Re-reading
// fifteen thousand files to recompute something the rows already contain would
// cost a full corpus walk to answer a question one query answers.

// ResolveDangling re-resolves every link that resolved to nothing, against the
// paths the index knows now.
//
// Returns how many found a target. Zero is the ordinary steady-state answer:
// most dangling links point at something nobody has written yet, and they stay
// recorded, because a dangling link is a fact about the corpus rather than an
// error in it.
func (x *Index) ResolveDangling(ctx context.Context) (int, error) {
	x.mu.Lock()
	defer x.mu.Unlock()

	tx, err := x.db.Begin()
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()

	known, err := x.pathsLocked(tx)
	if err != nil {
		return 0, err
	}
	if len(known) == 0 {
		return 0, nil
	}

	rows, err := tx.QueryContext(ctx, `
		SELECT l.rowid, l.target, d.path
		FROM links l JOIN docmeta d ON d.id = l.source_id
		WHERE l.resolved = ''`)
	if err != nil {
		return 0, fmt.Errorf("index: reading unresolved links: %w", err)
	}

	type fix struct {
		rowid    int64
		resolved string
	}
	var fixes []fix
	for rows.Next() {
		var rowid int64
		var target, from string
		if err := rows.Scan(&rowid, &target, &from); err != nil {
			rows.Close()
			return 0, err
		}
		// The same resolver the write path uses, rather than a second one. Two
		// implementations of "which note does this name mean" is the drift
		// surface every seam in this design exists to close.
		if resolved := extract.ResolveTarget(target, from, known); resolved != "" {
			fixes = append(fixes, fix{rowid, resolved})
		}
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return 0, err
	}
	if len(fixes) == 0 {
		return 0, tx.Commit()
	}

	stmt, err := tx.Prepare(`UPDATE links SET resolved = ? WHERE rowid = ?`)
	if err != nil {
		return 0, err
	}
	defer stmt.Close()
	for _, f := range fixes {
		if _, err := stmt.Exec(f.resolved, f.rowid); err != nil {
			return 0, fmt.Errorf("index: resolving link %d: %w", f.rowid, err)
		}
	}
	return len(fixes), tx.Commit()
}
