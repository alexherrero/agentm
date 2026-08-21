package ledger

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"
)

// The pending-work queues, and the cursor that keeps them honest.
//
// Discovery is decoupled from repair. A stage that finds a gap another stage
// owns — an entity mentioned in forty notes with no entity file, a wikilink
// pointing at nothing, a rollup whose input set changed — enqueues a work item
// naming the owner and the reason, and moves on. Owners drain their own queues
// under their own caps.
//
// # Why a cursor, and why it wraps
//
// This codebase has shipped the bug this file exists to prevent. A capped batch
// drawn from a fixed sort with no persisted cursor takes the same first N items
// every cycle, and everything past N is starved forever — silently, because a run
// that processed N items looks identical to a run that processed the right N.
//
// A cursor alone is not enough either, and the second half is the part that is
// easy to get wrong. A cursor that only moves forward eventually points past the
// last item, and then every cycle finds nothing while items behind it sit
// unattempted. So the cursor wraps: a drain works forward from where the last one
// stopped, and when it runs out it starts again from the beginning and continues
// until it reaches its own starting point. Over enough cycles every item is
// reached, whatever the cap is and however many items there are.
//
// The third half, which is not a half but is the one that actually bit: the
// cursor advances past every item a run *finished*, whatever the outcome, and it
// is persisted per item rather than at the end of the run. Advancing only past
// successes makes one reliably-failing item the permanent head of the queue, and
// every later run spends its whole cap on it. Persisting only at the end loses
// the advance to any crash and produces the same thing.
//
// # What this is not
//
// It is not the coverage ledger. The ledger records what finished; the queue
// records what is owed. They are separate tables because an attempt count
// belongs to a piece of owed work rather than to a completed judgment, and
// folding them together would mean a row that has to mean both.

// State is where a work item stands.
type State string

const (
	// StatePending means the item is owed. Only pending items are drained.
	StatePending State = "pending"
	// StateDone means an owner finished it.
	StateDone State = "done"
	// StateDead means it failed too many times and is parked. Parked rather
	// than deleted: the item is the record of what went wrong, and the whole
	// point is that somebody can see it.
	StateDead State = "dead"
)

// MaxAttempts is how many failures park an item.
//
// Three, from the design. The number matters less than the fact that there is
// one — an item retried forever costs a model call per cycle for as long as
// nobody notices, and nobody notices because a queue that is quietly failing
// looks exactly like a queue that is quietly working.
const MaxAttempts = 3

// WorkItem is one piece of owed work.
type WorkItem struct {
	// ID is the arrival order and the cursor's unit. Monotonic and unique,
	// which is what makes a cursor over it unable to repeat or skip — the two
	// failures a cursor over a timestamp has, because two items enqueued in the
	// same second are indistinguishable to it.
	ID int64 `json:"id"`
	// Owner is the stage that has to do the work.
	Owner Stage `json:"owner"`
	// Target is what to do it to.
	Target string `json:"target"`
	// Reason says why it was enqueued, in the words the owner and a human
	// reading the digest both need.
	Reason string `json:"reason"`
	// Enqueued is when the work was first discovered. Preserved across
	// re-discovery, because how long something has been owed is the number the
	// queue thresholds are set on.
	Enqueued time.Time `json:"enqueued"`
	// Attempts counts how many times an owner has tried and failed. The
	// retry cap that turns this into dead-lettering is task 4; the count is
	// kept and reported now, because "this item has failed six times" is the
	// thing a digest has to be able to say before anything can act on it.
	Attempts int    `json:"attempts"`
	State    State  `json:"state"`
	LastErr  string `json:"last_error,omitempty"`
}

// Queue is the work table plus its per-owner cursors.
type Queue struct {
	db *sql.DB

	// now is the clock, injectable because one property cannot be checked
	// without it. Enqueue preserves an item's original age across re-discovery,
	// and timestamps here are second-resolution — so two enqueues in the same
	// test land in the same second, and an implementation that deliberately
	// refreshed the clock would be indistinguishable from one that did not.
	now func() time.Time

	// maxAttempts is the retry cap. Zero means MaxAttempts.
	//
	// Settable so a test can hold parking still while it checks something else.
	// The cursor's own tests need a queue where nothing ever leaves the pending
	// set, and a cap of three quietly empties it for a reason that has nothing
	// to do with the cursor — the test would pass while proving less than it
	// says.
	maxAttempts int
}

// SetMaxAttempts overrides the retry cap. Zero restores the default.
func (q *Queue) SetMaxAttempts(n int) { q.maxAttempts = n }

func (q *Queue) retryCap() int {
	if q.maxAttempts > 0 {
		return q.maxAttempts
	}
	return MaxAttempts
}

// SetClock replaces the queue's clock. For tests that need two enqueues to be
// provably different moments without waiting a real second for it.
func (q *Queue) SetClock(f func() time.Time) { q.now = f }

func (q *Queue) stamp() time.Time {
	if q.now != nil {
		return q.now()
	}
	return time.Now()
}

// OpenQueue prepares the queue's tables on an already-open index database.
func OpenQueue(db *sql.DB) (*Queue, error) {
	if db == nil {
		return nil, errors.New("queue: no database handle")
	}
	q := &Queue{db: db}
	if err := q.migrate(); err != nil {
		return nil, err
	}
	return q, nil
}

func (q *Queue) migrate() error {
	stmts := []string{
		// UNIQUE(owner, target): one item per piece of work. The reconcile scan
		// runs every cycle and re-discovers the same gaps, and without this a
		// month of nightly runs would enqueue the same job thirty times.
		`CREATE TABLE IF NOT EXISTS queue (
			id         INTEGER PRIMARY KEY AUTOINCREMENT,
			owner      TEXT NOT NULL,
			target     TEXT NOT NULL,
			reason     TEXT NOT NULL DEFAULT '',
			enqueued   TEXT NOT NULL DEFAULT '',
			attempts   INTEGER NOT NULL DEFAULT 0,
			state      TEXT NOT NULL DEFAULT 'pending',
			last_error TEXT NOT NULL DEFAULT '',
			UNIQUE(owner, target))`,
		`CREATE INDEX IF NOT EXISTS queue_owner_state ON queue(owner, state, id)`,
		// One cursor per owner. A table rather than a value in `meta` because
		// owners drain independently and a shared position would make one
		// stage's progress skip another's work.
		`CREATE TABLE IF NOT EXISTS queue_cursor (
			owner    TEXT PRIMARY KEY,
			position INTEGER NOT NULL DEFAULT 0)`,
	}
	for _, s := range stmts {
		if _, err := q.db.Exec(s); err != nil {
			return fmt.Errorf("queue schema: %w", err)
		}
	}
	return nil
}

// Enqueue records that `owner` owes work on `target`.
//
// Idempotent by (owner, target), and deliberately conservative about what a
// re-discovery may change. The reason is refreshed, because the newest
// explanation is the useful one. The enqueued time is not, because how long the
// work has been owed is exactly what the age threshold reads, and refreshing it
// on every nightly scan would make a permanently-stalled item look permanently
// fresh — the alarm would never fire.
//
// An item already finished is re-opened, with its attempt count cleared. Being
// discovered again after being done means the world changed, which is new work
// rather than a continuation of the old failure.
//
// A parked item stays parked, and this is the load-bearing half. The reconcile
// scan runs every cycle and re-discovers the same gaps; if that revived a
// dead-lettered item, its attempt count would reset nightly, the cap would never
// be reached twice, and "nothing retries forever" would be false in exactly the
// way that is hardest to see — the queue would look healthy and the same broken
// item would be paid for every night. Reviving is Revive, and nothing calls it
// on a schedule.
func (q *Queue) Enqueue(ctx context.Context, owner Stage, target, reason string) error {
	if owner == "" || target == "" {
		return fmt.Errorf("queue: a work item needs both an owner and a target, "+
			"got %q/%q", owner, target)
	}
	_, err := q.db.ExecContext(ctx, `
		INSERT INTO queue(owner, target, reason, enqueued, attempts, state)
		VALUES(?, ?, ?, ?, 0, ?)
		ON CONFLICT(owner, target) DO UPDATE SET
			reason = excluded.reason,
			attempts = CASE WHEN queue.state = ? THEN 0 ELSE queue.attempts END,
			state = CASE WHEN queue.state = ? THEN ? ELSE ? END`,
		owner, target, reason, q.stamp().UTC().Format(stampFormat), string(StatePending),
		string(StateDone),
		string(StateDead), string(StateDead), string(StatePending))
	if err != nil {
		return fmt.Errorf("queue: enqueueing %s/%s: %w", owner, target, err)
	}
	return nil
}

// Complete marks one item finished.
func (q *Queue) Complete(ctx context.Context, id int64) error {
	_, err := q.db.ExecContext(ctx,
		`UPDATE queue SET state = ?, last_error = '' WHERE id = ?`, string(StateDone), id)
	return err
}

// Fail records an attempt that did not work, and parks the item once it has
// failed MaxAttempts times.
//
// The parking decision lives here rather than in the drain, so there is one
// place the cap is applied and no way for a second caller of Fail to bypass it.
// It returns whether this failure was the one that parked the item, because the
// run that killed something should be the run that says so.
func (q *Queue) Fail(ctx context.Context, id int64, cause string) (bool, error) {
	_, err := q.db.ExecContext(ctx, `
		UPDATE queue
		SET attempts = attempts + 1,
		    last_error = ?,
		    state = CASE WHEN attempts + 1 >= ? THEN ? ELSE state END
		WHERE id = ?`, cause, q.retryCap(), string(StateDead), id)
	if err != nil {
		return false, err
	}
	var state string
	var attempts int
	if err := q.db.QueryRowContext(ctx,
		`SELECT state, attempts FROM queue WHERE id = ?`, id).Scan(&state, &attempts); err != nil {
		return false, err
	}
	return State(state) == StateDead && attempts == q.retryCap(), nil
}

// Revive puts a parked item back in the queue with its attempt count cleared.
//
// Deliberate by construction: nothing calls this on a schedule, and re-discovery
// explicitly does not. An item parks because something about it is broken, and a
// revival that happened automatically would be the retry loop the cap exists to
// stop, wearing a different name.
func (q *Queue) Revive(ctx context.Context, id int64) error {
	res, err := q.db.ExecContext(ctx,
		`UPDATE queue SET state = ?, attempts = 0, last_error = '' WHERE id = ? AND state = ?`,
		string(StatePending), id, string(StateDead))
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return fmt.Errorf("queue: item %d is not parked; only a dead-lettered "+
			"item can be revived, and reviving a live one would reset a retry "+
			"count that is still counting", id)
	}
	return nil
}

// Dead lists the parked items for one owner, which is what makes "nothing
// retries silently forever" observable rather than merely true.
func (q *Queue) Dead(ctx context.Context, owner Stage) ([]WorkItem, error) {
	return q.byState(ctx, owner, StateDead)
}

// Cursor is where this owner's last drain stopped.
func (q *Queue) Cursor(ctx context.Context, owner Stage) (int64, error) {
	var pos int64
	err := q.db.QueryRowContext(ctx,
		`SELECT position FROM queue_cursor WHERE owner = ?`, owner).Scan(&pos)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, nil
	}
	return pos, err
}

// SetCursor persists where a drain has got to.
func (q *Queue) SetCursor(ctx context.Context, owner Stage, pos int64) error {
	_, err := q.db.ExecContext(ctx, `
		INSERT INTO queue_cursor(owner, position) VALUES(?, ?)
		ON CONFLICT(owner) DO UPDATE SET position = excluded.position`, owner, pos)
	return err
}

// DrainReport is what one drain did, in the numbers a digest asks for.
type DrainReport struct {
	Owner Stage `json:"owner"`
	// Attempted is how many items the owner was handed.
	Attempted int `json:"attempted"`
	Done      int `json:"done"`
	Failed    int `json:"failed"`
	// DeadLettered names the items this drain parked — the ones whose failure
	// took them to MaxAttempts. Named rather than counted, because the digest
	// has to be able to say which work stopped.
	DeadLettered []string `json:"dead_lettered,omitempty"`
	// Deferred says the cap stopped this drain before the queue ran out. It is
	// reported rather than logged, because a drain that quietly stopped early
	// and one that quietly finished look identical from outside — and every
	// capped drain in this design has to say what it deferred.
	Deferred bool `json:"deferred"`
	// Cursor is where the next drain starts.
	Cursor int64 `json:"cursor"`
	// Depth and OldestAge are the two numbers that ride the dashboard. The
	// threshold is on age rather than depth, because fifty fresh items on a
	// Tuesday is a Tuesday and one item three days old means the drain stalled.
	Depth     int           `json:"depth"`
	OldestAge time.Duration `json:"oldest_age"`
	// Parked is how many of this owner's items are dead-lettered in total, not
	// just this run's. A run that parked nothing over a queue with forty parked
	// items is not a healthy run, and a report that showed only the delta would
	// read as one.
	Parked  int           `json:"parked"`
	Elapsed time.Duration `json:"elapsed"`
	// Errors carries the failures verbatim, capped, so a drain where everything
	// failed says so once rather than once per item.
	Errors []string `json:"errors,omitempty"`
}

// maxDrainErrors bounds the failures a drain report carries.
const maxDrainErrors = 20

// Handler does one item's work. Returning an error records a failed attempt and
// leaves the item pending; the drain continues either way.
type Handler func(context.Context, WorkItem) error

// Drain works this owner's queue from where the last drain stopped, up to `cap`
// items, wrapping when it reaches the end.
//
// The cursor is the whole point, and it is managed here rather than exposed to
// callers on purpose. Every way this goes wrong is a caller doing the
// bookkeeping slightly differently: advancing only past successes, persisting
// once at the end, or forgetting to wrap. One implementation means one thing to
// get right.
func (q *Queue) Drain(ctx context.Context, owner Stage, limit int,
	do Handler) (DrainReport, error) {
	started := time.Now()
	rep := DrainReport{Owner: owner}
	if limit < 1 {
		return rep, fmt.Errorf("queue: a drain needs a positive cap, got %d — an "+
			"uncapped drain over this corpus is the unattended run nobody wants", limit)
	}

	start, err := q.Cursor(ctx, owner)
	if err != nil {
		return rep, err
	}
	rep.Cursor = start

	wrapped := false
	for rep.Attempted < limit {
		page, err := q.pending(ctx, owner, rep.Cursor, limit-rep.Attempted)
		if err != nil {
			return rep, err
		}
		if len(page) == 0 {
			if wrapped {
				// All the way round with nothing left: the queue is empty.
				break
			}
			// Past the end. Start again from the beginning and keep going until
			// we reach where this drain began.
			wrapped, rep.Cursor = true, 0
			continue
		}

		stop := false
		for _, item := range page {
			if wrapped && item.ID > start {
				// Caught up to our own starting point. Everything past it was
				// covered before the wrap.
				stop = true
				break
			}
			// No cap check here. The page above is fetched with exactly the
			// remaining budget, so the loop cannot overrun it — a second guard
			// would be a branch no input can reach.
			rep.Attempted++
			runErr := do(ctx, item)
			if runErr != nil {
				rep.Failed++
				if len(rep.Errors) < maxDrainErrors {
					rep.Errors = append(rep.Errors,
						fmt.Sprintf("%s: %v", item.Target, runErr))
				}
				parked, err := q.Fail(ctx, item.ID, runErr.Error())
				if err != nil {
					return rep, err
				}
				if parked {
					// Named, not just counted. A number says work stopped; the
					// name says what stopped, which is the only form anybody can
					// act on.
					rep.DeadLettered = append(rep.DeadLettered, item.Target)
				}
			} else {
				rep.Done++
				if err := q.Complete(ctx, item.ID); err != nil {
					return rep, err
				}
			}

			// Past every item this drain finished, whatever the outcome, and
			// persisted now rather than at the end. Both halves are the bug:
			// advancing only past successes parks the queue behind its first
			// poison item, and persisting late loses the advance to a crash and
			// arrives at the same place.
			rep.Cursor = item.ID
			if err := q.SetCursor(ctx, owner, item.ID); err != nil {
				return rep, err
			}
		}
		if stop {
			break
		}
	}

	if rep.Depth, rep.OldestAge, err = q.stats(ctx, owner, started); err != nil {
		return rep, err
	}
	if rep.Parked, err = q.Parked(ctx, owner); err != nil {
		return rep, err
	}
	// Deferred means one thing: the cap stopped this drain and work is still
	// owed. A drain that spent its whole cap and emptied the queue has deferred
	// nothing, and saying otherwise would leave a permanent "work outstanding"
	// line in the digest that nobody could ever clear.
	rep.Deferred = rep.Attempted >= limit && rep.Depth > 0
	rep.Elapsed = time.Since(started)
	return rep, nil
}

// pending reads the next page of owed work after `after`, in arrival order.
func (q *Queue) pending(ctx context.Context, owner Stage, after int64, limit int) ([]WorkItem, error) {
	if limit < 1 {
		return nil, nil
	}
	rows, err := q.db.QueryContext(ctx, `
		SELECT id, owner, target, reason, enqueued, attempts, state, last_error
		FROM queue
		WHERE owner = ? AND state = ? AND id > ?
		ORDER BY id
		LIMIT ?`, owner, string(StatePending), after, limit)
	if err != nil {
		return nil, fmt.Errorf("queue: reading %s's queue: %w", owner, err)
	}
	defer rows.Close()
	return scanItems(rows)
}

// scanItems reads work-item rows. Shared by every listing so the column order
// and the timestamp parse live in one place — two readers of the same seven
// columns is two things to keep in agreement.
func scanItems(rows *sql.Rows) ([]WorkItem, error) {
	var out []WorkItem
	for rows.Next() {
		var it WorkItem
		var state, enqueued string
		if err := rows.Scan(&it.ID, &it.Owner, &it.Target, &it.Reason, &enqueued,
			&it.Attempts, &state, &it.LastErr); err != nil {
			return nil, err
		}
		it.State = State(state)
		if t, err := time.Parse(stampFormat, enqueued); err == nil {
			it.Enqueued = t.UTC()
		}
		out = append(out, it)
	}
	return out, rows.Err()
}

// Pending lists what an owner still owes, oldest first.
func (q *Queue) Pending(ctx context.Context, owner Stage, limit int) ([]WorkItem, error) {
	if limit < 1 {
		limit = 1 << 30
	}
	return q.pending(ctx, owner, 0, limit)
}

// byState lists one owner's items in a given state, oldest first.
func (q *Queue) byState(ctx context.Context, owner Stage, state State) ([]WorkItem, error) {
	rows, err := q.db.QueryContext(ctx, `
		SELECT id, owner, target, reason, enqueued, attempts, state, last_error
		FROM queue
		WHERE owner = ? AND state = ?
		ORDER BY id`, owner, string(state))
	if err != nil {
		return nil, fmt.Errorf("queue: reading %s's %s items: %w", owner, state, err)
	}
	defer rows.Close()
	return scanItems(rows)
}

// Parked is how many of an owner's items are dead-lettered.
func (q *Queue) Parked(ctx context.Context, owner Stage) (int, error) {
	var n int
	err := q.db.QueryRowContext(ctx,
		`SELECT count(*) FROM queue WHERE owner = ? AND state = ?`,
		owner, string(StateDead)).Scan(&n)
	return n, err
}

// stats is depth and the age of the oldest owed item.
func (q *Queue) stats(ctx context.Context, owner Stage, now time.Time) (int, time.Duration, error) {
	var depth int
	var oldest sql.NullString
	err := q.db.QueryRowContext(ctx, `
		SELECT count(*), min(enqueued) FROM queue WHERE owner = ? AND state = ?`,
		owner, string(StatePending)).Scan(&depth, &oldest)
	if err != nil {
		return 0, 0, fmt.Errorf("queue: measuring %s's queue: %w", owner, err)
	}
	if !oldest.Valid || oldest.String == "" {
		return depth, 0, nil
	}
	t, perr := time.Parse(stampFormat, oldest.String)
	if perr != nil {
		return depth, 0, nil
	}
	age := now.Sub(t)
	if age < 0 {
		age = 0
	}
	return depth, age, nil
}

// Depth and OldestAge report one owner's queue without draining it, for the
// dashboard and for a human looking before deciding to run anything.
func (q *Queue) Depth(ctx context.Context, owner Stage) (int, time.Duration, error) {
	return q.stats(ctx, owner, q.stamp())
}

// Owners lists every owner with work in the table, for a report that has to name
// queues nobody remembered existed.
func (q *Queue) Owners(ctx context.Context) ([]Stage, error) {
	rows, err := q.db.QueryContext(ctx,
		`SELECT DISTINCT owner FROM queue ORDER BY owner`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Stage
	for rows.Next() {
		var s Stage
		if err := rows.Scan(&s); err != nil {
			return nil, err
		}
		out = append(out, s)
	}
	return out, rows.Err()
}
