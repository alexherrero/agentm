package ledger

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

// The ledger's own file, and the move out of the index (#783).
//
// The package comment says why it left. The cost it ended was measured: the
// schema bump of 2026-09-30 left 159 rows, every one recovered from the corpus
// with no input key, and the night after paid to re-judge notes whose answer
// had not changed.

// FileName is the ledger's file in the engine state directory.
const FileName = "ledger.db"

// OpenFile opens the ledger in a file of its own, creating the file and its
// table when they do not exist.
//
// A rollback journal rather than the index's write-ahead log. The engine state
// directory is a git repository the runner commits on its cadence, and a
// write-ahead log keeps two sidecar files beside the database for as long as a
// connection is open, which the commit would pick up as churn. The ledger takes
// a few hundred writes a night, so the concurrency the log buys is worth
// nothing here; the journal exists only during a write.
func OpenFile(path string) (*Ledger, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, fmt.Errorf("ledger directory: %w", err)
	}
	dsn := "file:" + path +
		"?_pragma=journal_mode(DELETE)&_pragma=busy_timeout(10000)&_pragma=synchronous(FULL)"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("opening the ledger %s: %w", path, err)
	}
	db.SetMaxOpenConns(1)
	if err := db.Ping(); err != nil {
		db.Close()
		return nil, fmt.Errorf("opening the ledger %s: %w", path, err)
	}
	l, err := Open(db)
	if err != nil {
		db.Close()
		return nil, err
	}
	l.owned = true
	return l, nil
}

// Close releases a ledger opened with OpenFile. A ledger opened on a handle
// somebody else owns leaves the handle open.
func (l *Ledger) Close() error {
	if l == nil || !l.owned {
		return nil
	}
	return l.db.Close()
}

// CarryFrom copies every row of a ledger table in another database into this
// one: the move out of the index, run once when the file is first made.
//
// It reports how many rows it copied. A database with no ledger table copies
// none, which is how a machine whose index never held one reads.
func (l *Ledger) CarryFrom(ctx context.Context, src *sql.DB) (int, error) {
	var name string
	err := src.QueryRowContext(ctx,
		`SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'ledger'`).Scan(&name)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, nil
	}
	if err != nil {
		return 0, fmt.Errorf("ledger: looking for the index's table: %w", err)
	}
	rows, err := src.QueryContext(ctx, `
		SELECT stage, target, version, rules_hash, input_key, output_key,
		       outcome, reason, at
		FROM ledger`)
	if err != nil {
		return 0, fmt.Errorf("ledger: reading the index's table: %w", err)
	}
	var entries []rawRow
	for rows.Next() {
		var r rawRow
		if err := rows.Scan(&r.stage, &r.target, &r.version, &r.rules, &r.input,
			&r.output, &r.outcome, &r.reason, &r.at); err != nil {
			rows.Close()
			return 0, err
		}
		entries = append(entries, r)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return 0, err
	}
	tx, err := l.db.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	for _, r := range entries {
		// Verbatim, the timestamp included: Record would re-format a row and
		// stamp a missing time with now, and a carried row must say exactly
		// what it said in the index.
		if _, err := tx.ExecContext(ctx, `
			INSERT OR REPLACE INTO ledger(stage, target, version, rules_hash,
			                              input_key, output_key, outcome, reason, at)
			VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)`,
			r.stage, r.target, r.version, r.rules, r.input, r.output, r.outcome,
			r.reason, r.at); err != nil {
			return 0, fmt.Errorf("ledger: carrying %s/%s: %w", r.stage, r.target, err)
		}
	}
	if err := tx.Commit(); err != nil {
		return 0, err
	}
	return len(entries), nil
}

type rawRow struct {
	stage, target, version, rules, input, output, outcome, reason, at string
}

// DropFrom removes the ledger table from another database, once its rows are
// safe in the ledger's own file. Left in place, the index's copy would go
// stale beside the real one, and a later loss of the file would carry the stale
// rows back instead of rebuilding from the notes.
func DropFrom(ctx context.Context, db *sql.DB) error {
	if _, err := db.ExecContext(ctx, `DROP TABLE IF EXISTS ledger`); err != nil {
		return fmt.Errorf("ledger: dropping the index's table: %w", err)
	}
	return nil
}
