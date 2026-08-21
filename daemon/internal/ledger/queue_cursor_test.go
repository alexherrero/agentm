package ledger

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"testing"
)

// The properties the first round of queue tests could not distinguish.
//
// Each of these exists because a mutation of the code passed the tests already
// written: they were true of the implementation and also true of a broken one.
// A test that cannot tell two designs apart is not testing the design.

// Per-item persistence, distinguished from persisting once at the end.
//
// A drain that saved its cursor only on the way out loses everything to a crash,
// and the next run starts where the last one *started* — which is the starvation
// shape again, arrived at from a different direction. The only way to tell the
// two apart is to not let the drain finish.
func TestTheCursorSurvivesADrainThatNeverReturns(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	enqueueN(t, q, StageEnrich, 5)

	var handled int
	func() {
		defer func() { _ = recover() }()
		_, _ = q.Drain(ctx, StageEnrich, 5, func(_ context.Context, it WorkItem) error {
			handled++
			if handled == 3 {
				panic("the process died here")
			}
			return nil
		})
	}()
	if handled != 3 {
		t.Fatalf("the fixture handled %d items before dying, want 3", handled)
	}

	pos, err := q.Cursor(ctx, StageEnrich)
	if err != nil {
		t.Fatal(err)
	}
	if pos < 2 {
		t.Errorf("the cursor is at %d after a drain died on its third item; the "+
			"two it finished were not persisted, so the next run repeats them and "+
			"the tail is never reached", pos)
	}
}

// Arrival order, and not the order of anything else.
//
// "Oldest first" is the order within a pass; the cursor is what makes successive
// passes cover everything. Neither means anything if the pass itself is ordered
// by something that is not arrival — the starvation tests use a set and would
// pass over any order at all.
func TestADrainWorksInArrivalOrder(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	// Deliberately not alphabetical, and not reverse-alphabetical either, so no
	// incidental sort reproduces it.
	arrival := []string{"m.md", "a.md", "z.md", "c.md"}
	for _, target := range arrival {
		if err := q.Enqueue(ctx, StageEnrich, target, "why"); err != nil {
			t.Fatal(err)
		}
	}

	var got []string
	if _, err := q.Drain(ctx, StageEnrich, 4, func(_ context.Context, it WorkItem) error {
		got = append(got, it.Target)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, arrival) {
		t.Errorf("drain order %v, want arrival order %v", got, arrival)
	}
}

// The cap is exact. A drain that overran it would be the unattended run the
// budget exists to bound, and "roughly N" is not a budget.
func TestTheCapIsExact(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	enqueueN(t, q, StageEnrich, 20)

	for _, limit := range []int{1, 3, 7} {
		var handled int
		rep, err := q.Drain(ctx, StageEnrich, limit, func(context.Context, WorkItem) error {
			handled++
			return nil
		})
		if err != nil {
			t.Fatal(err)
		}
		if handled != limit {
			t.Errorf("a cap of %d handled %d items", limit, handled)
		}
		if rep.Attempted != limit {
			t.Errorf("a cap of %d reported %d attempted", limit, rep.Attempted)
		}
	}
}

// The wrap stops at its own starting point rather than going round again.
//
// Without that check a drain with a generous cap over a short queue would keep
// handing the same items back — work done twice in one run, reported as
// progress. Three items and a cap of ten: exactly three calls.
func TestTheWrapStopsWhereTheDrainBegan(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	enqueueN(t, q, StageEnrich, 3)

	counts := map[string]int{}
	rep, err := q.Drain(ctx, StageEnrich, 10, func(_ context.Context, it WorkItem) error {
		counts[it.Target]++
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Attempted != 3 {
		t.Errorf("Attempted = %d over a queue of 3 with a cap of 10", rep.Attempted)
	}
	for target, n := range counts {
		if n != 1 {
			t.Errorf("%s was handled %d times in one drain", target, n)
		}
	}
}

// The same, over a queue where everything fails — the case where nothing leaves
// the pending set, so a wrap that did not stop would spin until it hit the cap.
func TestTheWrapStopsEvenWhenNothingSucceeds(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	enqueueN(t, q, StageEnrich, 3)

	counts := map[string]int{}
	rep, err := q.Drain(ctx, StageEnrich, 10, func(_ context.Context, it WorkItem) error {
		counts[it.Target]++
		return errors.New("no")
	})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Attempted != 3 {
		t.Errorf("Attempted = %d over 3 failing items with a cap of 10 — the wrap "+
			"went round again and re-tried work this run had already tried",
			rep.Attempted)
	}
	for target, n := range counts {
		if n != 1 {
			t.Errorf("%s was tried %d times in one drain", target, n)
		}
	}
}

// A completed item leaves the drain set. Without the state filter a finished
// queue would be drained forever, and every cycle would report work it had
// already done.
func TestCompletedWorkIsNotDrainedAgain(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	enqueueN(t, q, StageEnrich, 3)

	if _, err := q.Drain(ctx, StageEnrich, 3,
		func(context.Context, WorkItem) error { return nil }); err != nil {
		t.Fatal(err)
	}
	rep, err := q.Drain(ctx, StageEnrich, 3,
		func(context.Context, WorkItem) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	if rep.Attempted != 0 {
		t.Errorf("a second drain over a finished queue attempted %d items",
			rep.Attempted)
	}
}

// A failure counts once per attempt, which is what the retry cap will read.
func TestEachFailureCountsOnce(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	if err := q.Enqueue(ctx, StageEnrich, "a.md", "why"); err != nil {
		t.Fatal(err)
	}
	for i := 1; i <= 3; i++ {
		if _, err := q.Drain(ctx, StageEnrich, 1, func(context.Context, WorkItem) error {
			return fmt.Errorf("attempt %d", i)
		}); err != nil {
			t.Fatal(err)
		}
		items, _ := q.Pending(ctx, StageEnrich, 0)
		if len(items) != 1 {
			t.Fatalf("after %d failures the item is gone", i)
		}
		if items[0].Attempts != i {
			t.Errorf("Attempts = %d after %d failures", items[0].Attempts, i)
		}
	}
}

// Finishing clears the recorded cause, so a digest stops reporting a problem
// that has been fixed.
func TestCompletingClearsTheLastError(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	if err := q.Enqueue(ctx, StageEnrich, "a.md", "why"); err != nil {
		t.Fatal(err)
	}
	if _, err := q.Drain(ctx, StageEnrich, 1, func(context.Context, WorkItem) error {
		return errors.New("transient")
	}); err != nil {
		t.Fatal(err)
	}
	items, _ := q.Pending(ctx, StageEnrich, 0)
	if items[0].LastErr == "" {
		t.Fatal("the failure was not recorded, so this cannot test clearing it")
	}
	if err := q.Complete(ctx, items[0].ID); err != nil {
		t.Fatal(err)
	}

	var got string
	if err := q.db.QueryRowContext(ctx,
		`SELECT last_error FROM queue WHERE id = ?`, items[0].ID).Scan(&got); err != nil {
		t.Fatal(err)
	}
	if got != "" {
		t.Errorf("last_error is still %q after the work succeeded", got)
	}
}
