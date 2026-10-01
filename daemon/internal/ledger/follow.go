package ledger

import (
	"context"
	"fmt"
)

// Following a note through a move.
//
// A row is keyed by the note's path, and the night moves notes: the projects
// axis files a record, the case-only rename moved `Agent/` to `agent/`, a
// reopened task comes back by hand. A moved note kept its bytes and, since task
// 180, its file — but its row stayed at the old path, so the note read as never
// judged and was bought a first judgment it had already had. Task 180's
// measurement found 36 such re-judgments in nine nights, 25 of them the casing
// rename.
//
// The note's body is what survives a move, and the body is what the keys hash.
// So a row whose path no longer exists, and whose key matches exactly one note
// that has no row, is that note under its new name, and the row moves to it.
// The same rule the axis reconcile follows a move by (`PlanReconcile`), with the
// same refusal: a body at two paths is a copy rather than a move, and pairing it
// would pick one at random.

// KeyAt hashes a note's current content under a given pass version and
// contract — the version and contract a row was written under, so a row from an
// older pass is matched by the key it actually stores.
type KeyAt func(rel, version, rules string) (string, bool)

// Follow moves each row whose target no longer exists to the one target in the
// population that carries its content and has no row of its own. It reports
// how many rows it moved.
func (l *Ledger) Follow(ctx context.Context, stage Stage, population []string,
	exists func(rel string) bool, keyAt KeyAt) (int, error) {
	rows, err := l.db.QueryContext(ctx, `
		SELECT stage, target, version, rules_hash, input_key, output_key,
		       outcome, reason, at
		FROM ledger WHERE stage = ?`, stage)
	if err != nil {
		return 0, fmt.Errorf("ledger: reading %s: %w", stage, err)
	}
	// A target counts as having its own row only when that row is finished.
	// A skip or a failure at the new path is the night meeting the moved note
	// as a stranger, and the finished row it is missing outranks it.
	finished := map[string]bool{}
	var gone []Entry
	for rows.Next() {
		e, err := scanEntry(rows)
		if err != nil {
			rows.Close()
			return 0, err
		}
		if e.Outcome == Done {
			finished[e.Target] = true
		}
		// Only a finished row is worth following. A failure or a skip at an
		// old path says nothing the new path needs.
		if e.Outcome == Done && !exists(e.Target) {
			gone = append(gone, e)
		}
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return 0, err
	}
	if len(gone) == 0 {
		return 0, nil
	}
	var rowless []string
	for _, rel := range population {
		if !finished[rel] {
			rowless = append(rowless, rel)
		}
	}
	if len(rowless) == 0 {
		return 0, nil
	}

	// Each vanished row against each row-less note, under the row's own
	// version and contract. Pairs are taken only when both sides are unique.
	matches := map[string][]string{} // old target -> new targets
	claimed := map[string][]string{} // new target -> old targets
	for _, e := range gone {
		for _, rel := range rowless {
			k, ok := keyAt(rel, e.Version, e.RulesHash)
			if !ok || k == "" {
				continue
			}
			if k == e.InputKey || k == e.OutputKey {
				matches[e.Target] = append(matches[e.Target], rel)
				claimed[rel] = append(claimed[rel], e.Target)
			}
		}
	}
	moved := 0
	for _, e := range gone {
		to := matches[e.Target]
		if len(to) != 1 || len(claimed[to[0]]) != 1 {
			continue
		}
		if err := l.move(ctx, stage, e.Target, to[0]); err != nil {
			return moved, fmt.Errorf("ledger: following %s to %s: %w", e.Target, to[0], err)
		}
		moved++
	}
	return moved, nil
}

// move re-keys one row, replacing whatever unfinished row the new path holds.
func (l *Ledger) move(ctx context.Context, stage Stage, from, to string) error {
	tx, err := l.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx,
		`DELETE FROM ledger WHERE stage = ? AND target = ? AND outcome != 'done'`,
		stage, to); err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx,
		`UPDATE ledger SET target = ? WHERE stage = ? AND target = ?`,
		to, stage, from); err != nil {
		return err
	}
	return tx.Commit()
}
