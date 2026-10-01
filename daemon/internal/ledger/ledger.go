// Package ledger records what each corpus-walking stage has already done.
//
// Dreaming's stages walk the whole vault. Without a record of what they have
// processed, every stage either re-does everything on every cycle or guesses,
// and both answers are expensive: the token-bearing stages cost real money per
// note, and there is no batch pricing here to soften it. So the ledger holds one
// row per stage and target — the input it processed, the version it processed
// under, and when — and "has this been done?" becomes a lookup instead of a
// judgment.
//
// # Why it lives in a file of its own
//
// It began as a table in the index database, on the reasoning that it is a
// cache: every row is either re-derivable from the corpus or describes work that
// can be done again, so losing it on the index's schema bump would cost a
// re-scan and nothing more. The re-scan was not the price. A row rebuilt from
// the notes' stamps has no input key and names the contract the note was last
// written under, so after every bump the night paid again for judgments whose
// answer had not changed (#783). So the ledger lives in its own file in the
// engine state directory (OpenFile), which a schema bump never reaches, and the
// rebuild from the stamps is the fallback for a lost file.
//
// The stamps stay the record in the note itself: `enriched_by`, `rules_hash`
// and `enriched_at`, written into the file the judgment was about. Rebuild reads
// those back.
//
// # What a key is, and why there are two of them
//
// The ledger stores opaque keys and never computes one. A key folds together
// everything that would make a stage's answer different — its prompt version,
// the filing contract it judged under, and the content it read — so a stage that
// changes any of those gets a different key and its whole population re-enters
// the queue at once. That is the mechanism behind "a voice change re-queues
// work": it is arithmetic, not intent.
//
// Each row carries two keys, because some stages rewrite the thing they read.
// The input key is the content the stage was handed. The output key is the
// content it produced, when it produced any. A stage is done with a target when
// the target's current content matches *either* — unchanged since we read it, or
// exactly what we wrote.
//
// The second half is not decoration. Enrichment rewrites its input, and an
// enrichment scoring below the confidence floor leaves the note `status:
// unfiled`, which is precisely the status the batch queue selects on. With only
// an input key, every such note is offered again on the next cycle, its content
// no longer matches what was read, and it is re-enriched at full price forever.
// On the live corpus that is 23 of the 25 notes the first real batches wrote.
package ledger

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"
)

// Stage names the worker a row belongs to.
//
// A string rather than an enum because the stage list grows in the Python half
// as well, and a closed Go type would make adding a stage a change in two
// repositories' worth of code for no protection the string does not already
// give.
type Stage = string

// The stages that exist today. Declared here so the ones already wired are
// greppable, not to close the set.
const (
	// StageEnrich is the enrichment pass — the only stage that rewrites the
	// note it reads, and therefore the only one that records an output key.
	StageEnrich Stage = "enrich"
)

// Outcome is what happened to one target.
//
// Only Done makes a target seen. A failure must stay un-seen or it would never
// be retried, and a skip cost nothing to reach, so remembering it saves nothing.
// Both are still recorded, because the digest has to be able to say why a target
// is not current — "we tried and it failed" and "we never tried" are different
// reports and the difference is the whole value of writing them down.
//
// Except that a skip never replaces a Done row (see Record). A target the stage
// has finished needs no explanation of why it is not current, and the skip that
// would have replaced the row is usually the fingerprint gate saying exactly
// that the row is right.
type Outcome string

const (
	// Done means the stage finished this target. This is the only outcome Seen
	// answers true for.
	Done Outcome = "done"
	// Skipped means a gate declined the target before any work happened.
	Skipped Outcome = "skipped"
	// Failed means the stage tried and could not finish.
	Failed Outcome = "failed"
)

// Entry is one row: what a stage did to one target.
type Entry struct {
	Stage  Stage  `json:"stage"`
	Target string `json:"target"`

	// Version identifies the stage's code and prompt together.
	Version string `json:"version"`
	// RulesHash is the filing contract the stage judged under.
	RulesHash string `json:"rules_hash,omitempty"`

	// InputKey is the content the stage read. Empty when a rebuild recovered
	// this row, because the input is gone by then — the stage overwrote it.
	InputKey string `json:"input_key,omitempty"`
	// OutputKey is the content the stage wrote, for the stages that write.
	// Empty for every stage that only reads.
	OutputKey string `json:"output_key,omitempty"`

	Outcome Outcome `json:"outcome"`
	// Reason carries a failure or a skip in the words a human reading the digest
	// needs. Empty on success.
	Reason string    `json:"reason,omitempty"`
	At     time.Time `json:"at"`
}

// Ledger is the table.
type Ledger struct {
	db *sql.DB
	// owned is true when OpenFile opened the handle, so Close closes it.
	owned bool
}

// Open prepares the ledger's table on an already-open database handle.
//
// Production opens the ledger's own file with OpenFile, which comes here. A
// handle someone else owns is for tests, and for reading the table the index
// used to hold (CarryFrom).
func Open(db *sql.DB) (*Ledger, error) {
	if db == nil {
		return nil, errors.New("ledger: no database handle")
	}
	l := &Ledger{db: db}
	if err := l.migrate(); err != nil {
		return nil, err
	}
	return l, nil
}

func (l *Ledger) migrate() error {
	stmts := []string{
		// Keyed (stage, target): one row per target per stage, the latest state
		// rather than a history. A history would answer questions nobody asks
		// and would grow without bound over a corpus of fifteen thousand notes
		// times a dozen stages.
		`CREATE TABLE IF NOT EXISTS ledger (
			stage      TEXT NOT NULL,
			target     TEXT NOT NULL,
			version    TEXT NOT NULL DEFAULT '',
			rules_hash TEXT NOT NULL DEFAULT '',
			input_key  TEXT NOT NULL DEFAULT '',
			output_key TEXT NOT NULL DEFAULT '',
			outcome    TEXT NOT NULL DEFAULT '',
			reason     TEXT NOT NULL DEFAULT '',
			at         TEXT NOT NULL DEFAULT '',
			PRIMARY KEY (stage, target))`,
		// The coverage query counts rows at a version within a stage, which is
		// the one query that runs over the whole table rather than one row.
		`CREATE INDEX IF NOT EXISTS ledger_stage_version ON ledger(stage, version)`,
		// A one-time repair, idempotent, of rows written before Record refused to
		// let a skip replace a finished row (#785). The enrichment pass's
		// observer recorded every fingerprint skip as a skipped row over the
		// Done row the gate had just matched, so the next night owed the note
		// again and bought it a second judgment. The fingerprint skips only when
		// a Done row's key matches the note as it stands, and the skipped row
		// carries that key at the same version and contract, so turning it back
		// into a Done row restores exactly what the gate saw. Nothing writes
		// such a row any more, so after one open this matches nothing.
		`UPDATE ledger SET outcome = 'done', reason = 'restored: a fingerprint skip had replaced this row'
		 WHERE outcome = 'skipped' AND reason LIKE 'fingerprint: already enriched%'`,
	}
	for _, s := range stmts {
		if _, err := l.db.Exec(s); err != nil {
			return fmt.Errorf("ledger schema: %w", err)
		}
	}
	return nil
}

// stampFormat stores timestamps so a lexicographic compare is also a
// chronological one — the same reason the index stores `captured` this way.
const stampFormat = "2006-01-02T15:04:05Z"

// Record writes what a stage did to one target, replacing any earlier row.
//
// Replacing rather than appending: the question this table answers is "where do
// things stand", and an earlier attempt at an older version is not an answer to
// it. The attempt count that dead-lettering needs lives on the work queue, which
// is a different table for exactly this reason — the ledger records what
// finished, the queue records what is owed.
//
// One exception: a skip never replaces a Done row. A skip spent nothing and
// says only that a gate declined the target this time, while the Done row is
// the record of a judgment that was paid for. Replacing it threw that record
// away: the fingerprint gate's own skip — "already enriched" — overwrote the
// very row it had matched, the target stopped answering Seen, and the next run
// bought the same judgment again (#785). A stale Done row the budget deferred
// keeps its version and its time too, which is what lets the pending order
// say how long it has been waiting. The skip is not lost: the run's report
// counts it.
func (l *Ledger) Record(ctx context.Context, e Entry) error {
	if e.Stage == "" || e.Target == "" {
		return fmt.Errorf("ledger: a row needs both a stage and a target, got %q/%q",
			e.Stage, e.Target)
	}
	if e.Outcome == "" {
		return fmt.Errorf("ledger: %s/%s has no outcome; a row that does not say "+
			"what happened is worse than no row", e.Stage, e.Target)
	}
	at := e.At
	if at.IsZero() {
		at = time.Now()
	}
	_, err := l.db.ExecContext(ctx, `
		INSERT INTO ledger(stage, target, version, rules_hash, input_key,
		                   output_key, outcome, reason, at)
		VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(stage, target) DO UPDATE SET
			version=excluded.version, rules_hash=excluded.rules_hash,
			input_key=excluded.input_key, output_key=excluded.output_key,
			outcome=excluded.outcome, reason=excluded.reason, at=excluded.at
		WHERE NOT (ledger.outcome = 'done' AND excluded.outcome = 'skipped')`,
		e.Stage, e.Target, e.Version, e.RulesHash, e.InputKey, e.OutputKey,
		string(e.Outcome), e.Reason, at.UTC().Format(stampFormat))
	if err != nil {
		return fmt.Errorf("ledger: recording %s/%s: %w", e.Stage, e.Target, err)
	}
	return nil
}

// Seen answers the question the money depends on: has this stage already
// finished this exact content?
//
// True only for a Done row whose input or output key matches. A failed row
// answers false so the target is retried; a skipped row answers false because a
// skip spent nothing and remembering it would save nothing.
//
// An empty key never matches, even against a row whose stored key is also empty.
// A rebuilt row has no input key, and treating "we do not know what was read"
// as "it matched" would turn a cache rebuild into a silent, corpus-wide claim
// that everything is finished.
func (l *Ledger) Seen(ctx context.Context, stage Stage, target, key string) (bool, error) {
	if key == "" {
		return false, nil
	}
	var n int
	err := l.db.QueryRowContext(ctx, `
		SELECT count(*) FROM ledger
		WHERE stage = ? AND target = ? AND outcome = ?
		  AND (input_key = ? OR output_key = ?)`,
		stage, target, string(Done), key, key).Scan(&n)
	if err != nil {
		return false, fmt.Errorf("ledger: looking up %s/%s: %w", stage, target, err)
	}
	return n > 0, nil
}

// Lookup returns one row, and whether there was one.
func (l *Ledger) Lookup(ctx context.Context, stage Stage, target string) (Entry, bool, error) {
	row := l.db.QueryRowContext(ctx, `
		SELECT stage, target, version, rules_hash, input_key, output_key,
		       outcome, reason, at
		FROM ledger WHERE stage = ? AND target = ?`, stage, target)
	e, err := scanEntry(row)
	if errors.Is(err, sql.ErrNoRows) {
		return Entry{}, false, nil
	}
	if err != nil {
		return Entry{}, false, fmt.Errorf("ledger: reading %s/%s: %w", stage, target, err)
	}
	return e, true, nil
}

type scannable interface{ Scan(dest ...any) error }

func scanEntry(row scannable) (Entry, error) {
	var e Entry
	var outcome, at string
	if err := row.Scan(&e.Stage, &e.Target, &e.Version, &e.RulesHash,
		&e.InputKey, &e.OutputKey, &outcome, &e.Reason, &at); err != nil {
		return Entry{}, err
	}
	e.Outcome = Outcome(outcome)
	// A row whose timestamp will not parse keeps every other field. The time is
	// for reporting; refusing the row over it would lose the part that decides
	// whether work happens.
	if t, err := time.Parse(stampFormat, at); err == nil {
		e.At = t.UTC()
	}
	return e, nil
}

// Forget drops one target's row, putting it back in the pending set.
//
// The deliberate-re-run door. Re-running a stage over a target should be
// possible without deleting the whole table, and it should take an explicit act
// rather than happening because something drifted.
func (l *Ledger) Forget(ctx context.Context, stage Stage, target string) error {
	_, err := l.db.ExecContext(ctx,
		`DELETE FROM ledger WHERE stage = ? AND target = ?`, stage, target)
	return err
}

// ForgetStage drops every row for one stage, or for all of them when stage is
// empty. This is the cache loss the durability bar is written against.
func (l *Ledger) ForgetStage(ctx context.Context, stage Stage) (int64, error) {
	q := `DELETE FROM ledger`
	var args []any
	if stage != "" {
		q += ` WHERE stage = ?`
		args = append(args, stage)
	}
	res, err := l.db.ExecContext(ctx, q, args...)
	if err != nil {
		return 0, fmt.Errorf("ledger: forgetting %s: %w", stageLabel(stage), err)
	}
	return res.RowsAffected()
}

func stageLabel(stage Stage) string {
	if stage == "" {
		return "every stage"
	}
	return stage
}

// StageStat is one stage's row counts, for the digest and the status surface.
type StageStat struct {
	Stage   Stage  `json:"stage"`
	Version string `json:"version"`
	Done    int    `json:"done"`
	Skipped int    `json:"skipped"`
	Failed  int    `json:"failed"`
	Oldest  string `json:"oldest,omitempty"`
	Newest  string `json:"newest,omitempty"`
}

// Stages reports what the ledger holds, one line per stage and version.
//
// Split by version rather than folded, because a stage sitting at two versions
// is the shape of a backfill in progress and folding them would hide exactly
// the state someone is checking for.
func (l *Ledger) Stages(ctx context.Context) ([]StageStat, error) {
	rows, err := l.db.QueryContext(ctx, `
		SELECT stage, version,
		       sum(outcome = 'done'),
		       sum(outcome = 'skipped'),
		       sum(outcome = 'failed'),
		       min(at), max(at)
		FROM ledger GROUP BY stage, version ORDER BY stage, version`)
	if err != nil {
		return nil, fmt.Errorf("ledger: summarizing: %w", err)
	}
	defer rows.Close()

	var out []StageStat
	for rows.Next() {
		var s StageStat
		var oldest, newest sql.NullString
		if err := rows.Scan(&s.Stage, &s.Version, &s.Done, &s.Skipped, &s.Failed,
			&oldest, &newest); err != nil {
			return nil, err
		}
		s.Oldest, s.Newest = oldest.String, newest.String
		out = append(out, s)
	}
	return out, rows.Err()
}

// Count is how many rows a stage holds at a version, for the coverage meter.
// An empty version counts every version.
func (l *Ledger) Count(ctx context.Context, stage Stage, version string) (int, error) {
	q := `SELECT count(*) FROM ledger WHERE stage = ? AND outcome = 'done'`
	args := []any{stage}
	if version != "" {
		q += ` AND version = ?`
		args = append(args, version)
	}
	var n int
	err := l.db.QueryRowContext(ctx, q, args...).Scan(&n)
	return n, err
}
