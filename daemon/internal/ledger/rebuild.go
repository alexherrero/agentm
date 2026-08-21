package ledger

import (
	"context"
	"fmt"
	"time"
)

// Losing the ledger, and getting it back.
//
// The bar this file is written against is exact: deleting the ledger costs a
// re-scan and loses nothing. "Nothing" means no data — not no work. Redoing work
// is the price of a cache, and it is the price the design chose knowingly when it
// put the table in a database that discards itself on a schema bump.
//
// What makes the price payable is that every row a stage cares about is
// re-derivable from something durable. For enrichment that is the note itself:
// `enriched_by`, `rules_hash` and `enriched_at` are written into the file the
// judgment was about, which is the one place a record of a judgment survives
// anything that happens to a cache.
//
// # What a rebuild cannot recover, and why that is fine
//
// The input key. By the time a rebuild runs, the stage has overwritten what it
// read, so the content that produced the row is gone. A rebuilt row therefore
// carries only the output key — the content the stage wrote, which is still on
// disk and still hashes to the same thing.
//
// That is enough for the decision, which is the property that actually matters.
// A target whose current content is what the stage wrote still answers "seen" on
// its output key. A target whose content has changed since answers "not seen"
// either way. So `Seen` returns the same answer before and after a rebuild for
// every target the stage finished, which is the equality the durability bar is
// really asking about — row equality is impossible by construction and would be
// the wrong thing to assert.
//
// # Stages a rebuild cannot reach
//
// A stage that writes no durable stamp has no rebuilder, and its rows are simply
// gone after a wipe. Its targets come back as ReasonNever and the work is done
// again. That is the acceptable loss, stated rather than hidden: nothing was
// lost except the knowledge that the work had been done.

// Stamped is one target's durable record, read back out of whatever the stage
// wrote it into.
type Stamped struct {
	Target    string
	Version   string
	RulesHash string
	// OutputKey is what the target's current content hashes to under the version
	// it was stamped with. The scanner computes it, because the key format
	// belongs to the stage rather than to this table.
	OutputKey string
	At        time.Time
}

// Scanner walks whatever holds a stage's durable stamps and emits one Stamped
// per target it finds.
//
// A callback rather than a returned slice: the corpus is fifteen thousand notes
// and a rebuild should not need all of them resident to write the first row.
// An error from emit stops the walk.
type Scanner func(ctx context.Context, emit func(Stamped) error) error

// RebuildReport is what one rebuild did.
type RebuildReport struct {
	Stage Stage `json:"stage"`
	// Dropped is how many rows the wipe removed.
	Dropped int64 `json:"dropped"`
	// Recovered is how many the scan put back.
	Recovered int `json:"recovered"`
	// Elapsed is wall time, because a rebuild over the whole corpus is the kind
	// of operation someone wants a number for before running it again.
	Elapsed time.Duration `json:"elapsed"`
}

// Rebuild replaces one stage's rows with what the corpus can prove.
//
// Wipe-then-scan rather than merge. A merge would keep rows the corpus no longer
// supports — a note whose stamp was removed, or one deleted outright — and those
// are exactly the rows that would make the ledger claim work was done on
// something that is no longer there. After a rebuild the table says only what
// the files say, which is the whole point of files being truth.
func (l *Ledger) Rebuild(ctx context.Context, stage Stage, scan Scanner) (RebuildReport, error) {
	if stage == "" {
		return RebuildReport{}, fmt.Errorf("ledger: a rebuild needs a stage; " +
			"rebuilding every stage at once would wipe the ones that have no " +
			"rebuilder and silently call it a recovery")
	}
	if scan == nil {
		return RebuildReport{}, fmt.Errorf("ledger: %s has no rebuilder, so its "+
			"rows cannot be recovered — its work will simply be done again", stage)
	}

	started := time.Now()
	rep := RebuildReport{Stage: stage}

	dropped, err := l.ForgetStage(ctx, stage)
	if err != nil {
		return rep, err
	}
	rep.Dropped = dropped

	err = scan(ctx, func(s Stamped) error {
		// A stamp with no target is refused by Record, which is the one place
		// that rule belongs; checking it again here would be a second copy of it
		// with a different message and nothing to keep the two in agreement.
		if err := l.Record(ctx, Entry{
			Stage:     stage,
			Target:    s.Target,
			Version:   s.Version,
			RulesHash: s.RulesHash,
			// Deliberately empty. See the file comment: the input is gone, and
			// an invented value here would be a claim about content nobody can
			// check.
			InputKey:  "",
			OutputKey: s.OutputKey,
			Outcome:   Done,
			Reason:    "recovered from the corpus",
			At:        s.At,
		}); err != nil {
			return err
		}
		rep.Recovered++
		return nil
	})
	rep.Elapsed = time.Since(started)
	if err != nil {
		return rep, fmt.Errorf("ledger: rebuilding %s: %w", stage, err)
	}
	return rep, nil
}
