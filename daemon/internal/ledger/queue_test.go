package ledger

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
	"time"

	_ "modernc.org/sqlite"
)

func newQueue(t *testing.T) *Queue {
	t.Helper()
	dsn := "file:" + filepath.Join(t.TempDir(), "index.db") +
		"?_pragma=journal_mode(WAL)&_pragma=busy_timeout(10000)"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		t.Fatalf("opening the test database: %v", err)
	}
	db.SetMaxOpenConns(1)
	t.Cleanup(func() { db.Close() })
	q, err := OpenQueue(db)
	if err != nil {
		t.Fatalf("opening the queue: %v", err)
	}
	return q
}

func enqueueN(t *testing.T, q *Queue, owner Stage, n int) []string {
	t.Helper()
	var targets []string
	for i := 1; i <= n; i++ {
		target := fmt.Sprintf("note-%02d.md", i)
		if err := q.Enqueue(context.Background(), owner, target, "the rules changed"); err != nil {
			t.Fatalf("enqueue %s: %v", target, err)
		}
		targets = append(targets, target)
	}
	return targets
}

// --- the regression test the constraint names ------------------------------

// Finished work leaves the queue, so capped cycles get through it.
//
// This is deliberately *not* the cursor test, though it was written as one. With
// every item succeeding, `Complete` takes each out of the pending set and the
// next page naturally starts after it — so this passes with the cursor removed
// altogether. It proves completion works, which is worth proving, and nothing
// about starvation. The cursor's own test is the one below, where nothing
// completes.
func TestFinishedWorkLeavesTheQueue(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	want := enqueueN(t, q, StageEnrich, 10)

	seen := map[string]int{}
	for cycle := 0; cycle < 4; cycle++ {
		rep, err := q.Drain(ctx, StageEnrich, 3, func(_ context.Context, it WorkItem) error {
			seen[it.Target]++
			return nil
		})
		if err != nil {
			t.Fatalf("cycle %d: %v", cycle, err)
		}
		if rep.Attempted == 0 && rep.Depth > 0 {
			t.Fatalf("cycle %d attempted nothing with %d still owed — the cursor "+
				"has run off the end and the queue is starved", cycle, rep.Depth)
		}
	}

	var missed []string
	for _, target := range want {
		if seen[target] == 0 {
			missed = append(missed, target)
		}
	}
	if len(missed) > 0 {
		sort.Strings(missed)
		t.Errorf("%d of %d items were never attempted across four capped cycles: %v",
			len(missed), len(want), missed)
	}
}

// The starvation regression test the plan's Constraints name.
//
// Every attempt fails, so nothing ever leaves the pending set and completion
// cannot stand in for the cursor. What is left is exactly the bug this codebase
// shipped once: a capped batch over a fixed sort takes the same first three
// items every cycle and starves the other seven forever, silently.
//
// It runs past the end of the queue on purpose. Four cycles of three cover ten
// items, and a cursor that only moves forward would then point past the last
// one — every later cycle finding nothing while all ten sit unattempted. So the
// second lap is the half that tests the wrap, and every item has to be reached
// twice.
func TestACappedDrainNeverStarvesItemsPastTheCap(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	want := enqueueN(t, q, StageEnrich, 10)

	boom := errors.New("this one never works")
	seen := map[string]int{}
	for cycle := 0; cycle < 8; cycle++ {
		rep, err := q.Drain(ctx, StageEnrich, 3,
			func(_ context.Context, it WorkItem) error {
				seen[it.Target]++
				return boom
			})
		if err != nil {
			t.Fatalf("cycle %d: %v", cycle, err)
		}
		if rep.Attempted == 0 {
			t.Fatalf("cycle %d attempted nothing with %d items still owed — the "+
				"cursor has run off the end and every one of them is starved",
				cycle, rep.Depth)
		}
	}

	var starved, once []string
	for _, target := range want {
		switch {
		case seen[target] == 0:
			starved = append(starved, target)
		case seen[target] < 2:
			once = append(once, target)
		}
	}
	if len(starved) > 0 {
		sort.Strings(starved)
		t.Errorf("%d of %d items were never attempted across eight capped cycles "+
			"while every attempt failed: %v", len(starved), len(want), starved)
	}
	if len(once) > 0 {
		sort.Strings(once)
		t.Errorf("%d items were reached only once in eight cycles: %v — the drain "+
			"got to the end of the queue and did not come back round", len(once), once)
	}
}

// One item that always fails among nine that succeed. The nine must still all
// get through, and the failure must not be able to hold the queue.
func TestOnePoisonItemDoesNotHoldTheQueue(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	want := enqueueN(t, q, StageEnrich, 10)
	poison := want[0]

	seen := map[string]int{}
	for cycle := 0; cycle < 8; cycle++ {
		// Counted per cycle as well as in total. A cursor that sticks on the
		// failing item hands it back twice inside one drain, which looks like
		// healthy retrying in the totals and is really the same item eating the
		// cap. One drain hands each item out at most once.
		thisCycle := map[string]int{}
		if _, err := q.Drain(ctx, StageEnrich, 2,
			func(_ context.Context, it WorkItem) error {
				seen[it.Target]++
				thisCycle[it.Target]++
				if it.Target == poison {
					return errors.New("poison")
				}
				return nil
			}); err != nil {
			t.Fatalf("cycle %d: %v", cycle, err)
		}
		for target, n := range thisCycle {
			if n > 1 {
				t.Fatalf("cycle %d handed %s out %d times in one drain", cycle, target, n)
			}
		}
	}

	for _, target := range want[1:] {
		if seen[target] == 0 {
			t.Errorf("%s was never attempted; the poison item %s starved it",
				target, poison)
		}
	}
	// And the poison item is retried rather than abandoned. A cursor that
	// advanced only past successes would leave it behind after its first
	// failure and never come back — the queue would look healthy while quietly
	// dropping work. Unbounded retry is right here; the cap that turns this into
	// dead-lettering is task 4.
	if seen[poison] < 2 {
		t.Errorf("the failing item %s was attempted %d times across %d cycles — "+
			"it was abandoned rather than retried", poison, seen[poison], 8)
	}
}

// The cursor survives the process, not just the run. Persisted per item rather
// than once at the end, because a crash between the two loses the advance and
// arrives at exactly the starvation the cursor exists to prevent.
func TestTheCursorIsPersistedPerItem(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	enqueueN(t, q, StageEnrich, 5)

	// A handler that gives up part way, standing in for a process that died.
	stop := errors.New("stop here")
	var handled int
	_, err := q.Drain(ctx, StageEnrich, 5, func(_ context.Context, it WorkItem) error {
		handled++
		if handled == 2 {
			return stop
		}
		return nil
	})
	if err != nil {
		t.Fatalf("Drain: %v", err)
	}

	pos, err := q.Cursor(ctx, StageEnrich)
	if err != nil {
		t.Fatal(err)
	}
	if pos < 2 {
		t.Errorf("the cursor is at %d after handling %d items; it did not advance "+
			"past the failure", pos, handled)
	}
}

// A drain that spends its cap with work left says so. Every capped drain in this
// design has to report what it deferred; one that stopped early and one that
// finished look identical otherwise.
func TestDeferredMeansTheCapStoppedItWithWorkLeft(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	enqueueN(t, q, StageEnrich, 5)

	rep, err := q.Drain(ctx, StageEnrich, 2, func(context.Context, WorkItem) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	if !rep.Deferred {
		t.Errorf("a drain that spent its cap of 2 on a queue of 5 did not report "+
			"deferring: %+v", rep)
	}
	if rep.Depth != 3 {
		t.Errorf("Depth = %d, want 3", rep.Depth)
	}

	// And the drain that finishes the queue does not claim to have deferred.
	last, err := q.Drain(ctx, StageEnrich, 10, func(context.Context, WorkItem) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	if last.Deferred {
		t.Errorf("a drain that emptied the queue reported deferring: %+v", last)
	}
	if last.Depth != 0 {
		t.Errorf("Depth = %d after emptying the queue, want 0", last.Depth)
	}
}

// A drain that spends its whole cap and empties the queue has deferred nothing.
// Reporting otherwise leaves a "work outstanding" line nobody can ever clear.
func TestSpendingTheWholeCapOnTheLastItemsIsNotDeferring(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	enqueueN(t, q, StageEnrich, 3)

	rep, err := q.Drain(ctx, StageEnrich, 3, func(context.Context, WorkItem) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	if rep.Attempted != 3 {
		t.Fatalf("Attempted = %d, want 3", rep.Attempted)
	}
	if rep.Deferred {
		t.Error("a drain that used its whole cap to empty the queue reported deferring")
	}
}

// --- enqueueing ------------------------------------------------------------

// The reconcile scan runs every cycle and re-discovers the same gaps. Without
// idempotency a month of nightly runs enqueues the same job thirty times.
func TestEnqueueIsIdempotentPerOwnerAndTarget(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	for i := 0; i < 5; i++ {
		if err := q.Enqueue(ctx, StageEnrich, "a.md", "the rules changed"); err != nil {
			t.Fatal(err)
		}
	}
	items, err := q.Pending(ctx, StageEnrich, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 1 {
		t.Errorf("five discoveries of one gap left %d items, want 1", len(items))
	}
}

// Re-discovery refreshes the reason and leaves the clock alone. The age is what
// the threshold reads, and refreshing it every night would make a permanently
// stalled item look permanently fresh — the alarm would never fire.
func TestReDiscoveryKeepsTheOriginalAge(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	// Two provably different moments. Timestamps here are second-resolution, so
	// without an injected clock both enqueues land in the same second and this
	// test cannot tell a preserved timestamp from a refreshed one.
	clock := time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC)
	discovered := clock
	q.SetClock(func() time.Time { return clock })

	if err := q.Enqueue(ctx, StageEnrich, "a.md", "first reason"); err != nil {
		t.Fatal(err)
	}
	first, err := q.Pending(ctx, StageEnrich, 0)
	if err != nil {
		t.Fatal(err)
	}

	clock = clock.Add(72 * time.Hour)
	if err := q.Enqueue(ctx, StageEnrich, "a.md", "a better reason"); err != nil {
		t.Fatal(err)
	}
	again, err := q.Pending(ctx, StageEnrich, 0)
	if err != nil {
		t.Fatal(err)
	}
	// The queue stamps with its own clock. Without this the assertion below
	// holds for the wrong reason — a queue reading the wall clock directly would
	// also store one timestamp and keep it, and nothing here would notice.
	if !first[0].Enqueued.Equal(discovered) {
		t.Fatalf("the item is stamped %s, not the queue's clock at %s",
			first[0].Enqueued, discovered)
	}

	if !again[0].Enqueued.Equal(first[0].Enqueued) {
		t.Errorf("re-discovery moved the clock from %s to %s; a stalled item would "+
			"read as fresh forever", first[0].Enqueued, again[0].Enqueued)
	}
	if again[0].Reason != "a better reason" {
		t.Errorf("the reason was not refreshed: %q", again[0].Reason)
	}
}

// Work discovered again after being finished is new work, and its failure
// history does not carry over.
func TestReDiscoveryAfterCompletionResetsTheAttemptCount(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	if err := q.Enqueue(ctx, StageEnrich, "a.md", "first"); err != nil {
		t.Fatal(err)
	}
	if _, err := q.Drain(ctx, StageEnrich, 1, func(context.Context, WorkItem) error {
		return errors.New("failed once")
	}); err != nil {
		t.Fatal(err)
	}
	items, _ := q.Pending(ctx, StageEnrich, 0)
	if items[0].Attempts != 1 {
		t.Fatalf("Attempts = %d after one failure, want 1", items[0].Attempts)
	}

	// Finish it, then discover it again.
	if err := q.Complete(ctx, items[0].ID); err != nil {
		t.Fatal(err)
	}
	if err := q.Enqueue(ctx, StageEnrich, "a.md", "found again"); err != nil {
		t.Fatal(err)
	}
	items, _ = q.Pending(ctx, StageEnrich, 0)
	if len(items) != 1 {
		t.Fatalf("got %d pending items, want 1", len(items))
	}
	if items[0].Attempts != 0 {
		t.Errorf("Attempts = %d after re-discovery of finished work, want 0 — the "+
			"old failure history is not this work's", items[0].Attempts)
	}
}

// Re-discovering work that is still owed keeps its failure history. It is the
// same work, and forgetting the attempts would make the retry cap unreachable.
func TestReDiscoveryOfOwedWorkKeepsItsAttempts(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	if err := q.Enqueue(ctx, StageEnrich, "a.md", "first"); err != nil {
		t.Fatal(err)
	}
	if _, err := q.Drain(ctx, StageEnrich, 1, func(context.Context, WorkItem) error {
		return errors.New("failed once")
	}); err != nil {
		t.Fatal(err)
	}
	if err := q.Enqueue(ctx, StageEnrich, "a.md", "found again"); err != nil {
		t.Fatal(err)
	}
	items, _ := q.Pending(ctx, StageEnrich, 0)
	if items[0].Attempts != 1 {
		t.Errorf("Attempts = %d, want 1 — re-discovering owed work reset its "+
			"history, which puts the retry cap out of reach", items[0].Attempts)
	}
}

func TestEnqueueRefusesAnIncompleteItem(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	if err := q.Enqueue(ctx, "", "a.md", "why"); err == nil {
		t.Error("an item with no owner was accepted")
	}
	if err := q.Enqueue(ctx, StageEnrich, "", "why"); err == nil {
		t.Error("an item with no target was accepted")
	}
}

// --- ownership -------------------------------------------------------------

// Owners drain their own queues. One stage's cursor must not move another's,
// or a busy queue would skip a quiet one's work.
func TestOwnersDrainIndependently(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	// Interleaved, and with different counts. Enqueued in blocks, the first four
	// ids would all belong to enrich — so a drain that ignored the owner
	// entirely would still hand back four enrich items and pass. Interleaving
	// puts another owner's work inside the range the drain reaches.
	for i := 1; i <= 4; i++ {
		if err := q.Enqueue(ctx, StageEnrich, fmt.Sprintf("e-%d.md", i), "why"); err != nil {
			t.Fatal(err)
		}
		if i <= 3 {
			if err := q.Enqueue(ctx, "rollup", fmt.Sprintf("r-%d.md", i), "why"); err != nil {
				t.Fatal(err)
			}
		}
	}

	var handled []string
	if _, err := q.Drain(ctx, StageEnrich, 4, func(_ context.Context, it WorkItem) error {
		handled = append(handled, it.Target)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	for _, target := range handled {
		if target[0] != 'e' {
			t.Errorf("enrich's drain handled %s, which belongs to another owner", target)
		}
	}

	// Enrich's own cursor moved. Without this the next assertion passes for the
	// wrong reason — a drain writing every owner's cursor to one shared row
	// leaves rollup's at zero too.
	mine, err := q.Cursor(ctx, StageEnrich)
	if err != nil {
		t.Fatal(err)
	}
	if mine == 0 {
		t.Error("enrich drained four items and its own cursor is still at zero")
	}

	pos, err := q.Cursor(ctx, "rollup")
	if err != nil {
		t.Fatal(err)
	}
	if pos != 0 {
		t.Errorf("rollup's cursor moved to %d while enrich drained; one owner's "+
			"progress is skipping another's work", pos)
	}
	// Three rather than four, so a depth query that ignored the owner would
	// report seven and not coincidentally match.
	depth, _, err := q.Depth(ctx, "rollup")
	if err != nil {
		t.Fatal(err)
	}
	if depth != 3 {
		t.Errorf("rollup's depth is %d after enrich drained, want 3", depth)
	}
}

func TestOwnersListsEveryQueueWithWork(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	for _, owner := range []Stage{"rollup", StageEnrich, "footer"} {
		// Two items each. With one apiece a listing that forgot DISTINCT would
		// return the same three names and pass.
		for _, target := range []string{"a.md", "b.md"} {
			if err := q.Enqueue(ctx, owner, target, "why"); err != nil {
				t.Fatal(err)
			}
		}
	}
	got, err := q.Owners(ctx)
	if err != nil {
		t.Fatal(err)
	}
	want := []Stage{StageEnrich, "footer", "rollup"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("Owners = %v, want %v", got, want)
	}
}

// --- the dashboard numbers -------------------------------------------------

// Depth counts what is owed, and age is measured from the oldest owed item.
// The threshold is on age, because fifty fresh items on a Tuesday is a Tuesday
// and one item three days old means the drain has stalled.
func TestDepthAndAgeReportTheOwedWork(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	enqueueN(t, q, StageEnrich, 3)
	// A second owner with work of its own, so a depth query that ignored the
	// owner would report six and could not be mistaken for a correct three.
	for i := 1; i <= 3; i++ {
		if err := q.Enqueue(ctx, "rollup", fmt.Sprintf("r-%d.md", i), "why"); err != nil {
			t.Fatal(err)
		}
	}

	depth, age, err := q.Depth(ctx, StageEnrich)
	if err != nil {
		t.Fatal(err)
	}
	if depth != 3 {
		t.Errorf("Depth = %d, want 3", depth)
	}
	// Measured against a bound rather than a wall-clock number: this asserts the
	// age is of something just enqueued, not that the machine is fast.
	if age > time.Minute {
		t.Errorf("age of a just-enqueued queue is %s", age)
	}

	// Finishing work takes it out of the depth. An owed count that included
	// finished work would never fall.
	items, _ := q.Pending(ctx, StageEnrich, 0)
	if err := q.Complete(ctx, items[0].ID); err != nil {
		t.Fatal(err)
	}
	if depth, _, _ = q.Depth(ctx, StageEnrich); depth != 2 {
		t.Errorf("Depth = %d after completing one, want 2", depth)
	}
}

// The reported age is the real distance between the oldest owed item and now.
//
// A differential against the queue's own clock rather than a wall-clock bound:
// both ends come from the same injected source, so this asserts the arithmetic
// rather than how fast the machine happens to be.
func TestTheAgeIsTheDistanceFromTheOldestItem(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	clock := time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC)
	q.SetClock(func() time.Time { return clock })

	if err := q.Enqueue(ctx, StageEnrich, "old.md", "why"); err != nil {
		t.Fatal(err)
	}
	clock = clock.Add(72 * time.Hour)
	if err := q.Enqueue(ctx, StageEnrich, "new.md", "why"); err != nil {
		t.Fatal(err)
	}

	_, age, err := q.Depth(ctx, StageEnrich)
	if err != nil {
		t.Fatal(err)
	}
	if age != 72*time.Hour {
		t.Errorf("age = %s, want 72h — measured from the newest item rather than "+
			"the oldest, or against a different clock", age)
	}
}

// An empty queue has no age. Reporting one would put a number on the dashboard
// for a queue with nothing to be behind on.
func TestAnEmptyQueueHasNoAge(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	depth, age, err := q.Depth(ctx, StageEnrich)
	if err != nil {
		t.Fatal(err)
	}
	if depth != 0 || age != 0 {
		t.Errorf("an empty queue reports depth %d age %s, want 0 and 0", depth, age)
	}
}

// A drain over an empty queue terminates and reports nothing, rather than
// spinning looking for a wrap that never comes.
func TestDrainingAnEmptyQueueTerminates(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	done := make(chan DrainReport, 1)
	go func() {
		rep, err := q.Drain(ctx, StageEnrich, 5, func(context.Context, WorkItem) error {
			return nil
		})
		if err != nil {
			t.Errorf("Drain: %v", err)
		}
		done <- rep
	}()
	select {
	case rep := <-done:
		if rep.Attempted != 0 || rep.Deferred {
			t.Errorf("draining an empty queue reported %+v", rep)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("draining an empty queue did not terminate; the wrap is spinning")
	}
}

// An uncapped drain is refused. Over this corpus it is the unattended run that
// spends everything in one stretch, which is the thing the budget exists to
// stop.
func TestDrainRefusesAnUncappedRun(t *testing.T) {
	q := newQueue(t)
	for _, limit := range []int{0, -1} {
		if _, err := q.Drain(context.Background(), StageEnrich, limit,
			func(context.Context, WorkItem) error { return nil }); err == nil {
			t.Errorf("a drain with a cap of %d was accepted", limit)
		}
	}
}

// The handler is given the whole item, because an owner deciding what to do
// needs the reason it was enqueued and how many times it has already failed.
func TestTheHandlerReceivesTheWholeItem(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	if err := q.Enqueue(ctx, StageEnrich, "a.md", "rules_hash went stale"); err != nil {
		t.Fatal(err)
	}
	var got WorkItem
	if _, err := q.Drain(ctx, StageEnrich, 1, func(_ context.Context, it WorkItem) error {
		got = it
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if got.Target != "a.md" || got.Reason != "rules_hash went stale" {
		t.Errorf("the handler received %+v", got)
	}
	if got.Owner != StageEnrich {
		t.Errorf("Owner = %q, want %q", got.Owner, StageEnrich)
	}
	if got.Enqueued.IsZero() {
		t.Error("the handler received an item with no enqueue time")
	}
}

// A failure records what went wrong. "It failed six times" is not actionable
// without "and here is what it said".
func TestAFailureRecordsItsCause(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	if err := q.Enqueue(ctx, StageEnrich, "a.md", "why"); err != nil {
		t.Fatal(err)
	}
	rep, err := q.Drain(ctx, StageEnrich, 1, func(context.Context, WorkItem) error {
		return errors.New("the model refused")
	})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Failed != 1 {
		t.Errorf("Failed = %d, want 1", rep.Failed)
	}
	if len(rep.Errors) != 1 {
		t.Fatalf("the report carries %d errors, want 1", len(rep.Errors))
	}
	items, _ := q.Pending(ctx, StageEnrich, 0)
	if items[0].LastErr != "the model refused" {
		t.Errorf("LastErr = %q", items[0].LastErr)
	}
}

// A drain where everything fails says so once rather than once per item.
func TestTheErrorListIsCapped(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	enqueueN(t, q, StageEnrich, maxDrainErrors+10)

	rep, err := q.Drain(ctx, StageEnrich, maxDrainErrors+10,
		func(context.Context, WorkItem) error { return errors.New("no") })
	if err != nil {
		t.Fatal(err)
	}
	if rep.Failed != maxDrainErrors+10 {
		t.Errorf("Failed = %d, want %d", rep.Failed, maxDrainErrors+10)
	}
	if len(rep.Errors) != maxDrainErrors {
		t.Errorf("the report carries %d errors, want the cap of %d",
			len(rep.Errors), maxDrainErrors)
	}
}

// A successful retry clears the recorded failure, so the digest stops reporting
// a problem that has been fixed.
func TestCompletingClearsTheRecordedFailure(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	if err := q.Enqueue(ctx, StageEnrich, "a.md", "why"); err != nil {
		t.Fatal(err)
	}
	fail := true
	for i := 0; i < 2; i++ {
		if _, err := q.Drain(ctx, StageEnrich, 1, func(context.Context, WorkItem) error {
			if fail {
				fail = false
				return errors.New("transient")
			}
			return nil
		}); err != nil {
			t.Fatal(err)
		}
	}
	depth, _, err := q.Depth(ctx, StageEnrich)
	if err != nil {
		t.Fatal(err)
	}
	if depth != 0 {
		t.Errorf("Depth = %d after the retry succeeded, want 0", depth)
	}
}

func TestOpenQueueRefusesANilHandle(t *testing.T) {
	if _, err := OpenQueue(nil); err == nil {
		t.Error("OpenQueue(nil) returned a queue")
	}
}
