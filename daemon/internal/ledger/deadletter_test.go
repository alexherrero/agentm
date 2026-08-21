package ledger

import (
	"context"
	"errors"
	"testing"
)

// Dead-lettering, which is the second lesson this codebase has already paid for.
//
// The first was starvation — work never reached. This is its mirror: work
// attempted forever and never reported. A queue quietly failing the same item
// every night looks exactly like a queue quietly working, and costs a model call
// per cycle for as long as nobody notices.

// drainUntil runs cycles until the handler stops being called or n cycles pass.
func drainUntil(t *testing.T, q *Queue, owner Stage, cycles int,
	do Handler) []DrainReport {
	t.Helper()
	var reports []DrainReport
	for i := 0; i < cycles; i++ {
		rep, err := q.Drain(context.Background(), owner, 5, do)
		if err != nil {
			t.Fatalf("cycle %d: %v", i, err)
		}
		reports = append(reports, rep)
	}
	return reports
}

// The bar, exactly as pre-registered: an item failing three times parks and is
// visible.
func TestAnItemFailingThreeTimesParks(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	if err := q.Enqueue(ctx, StageEnrich, "poison.md", "the rules changed"); err != nil {
		t.Fatal(err)
	}

	var attempts int
	reports := drainUntil(t, q, StageEnrich, 6, func(context.Context, WorkItem) error {
		attempts++
		return errors.New("this never works")
	})

	if attempts != MaxAttempts {
		t.Errorf("the item was attempted %d times, want exactly %d — it is either "+
			"still being retried or it parked early", attempts, MaxAttempts)
	}

	// Visible, in a reader that exists in this change rather than one part 6
	// will add.
	dead, err := q.Dead(ctx, StageEnrich)
	if err != nil {
		t.Fatal(err)
	}
	if len(dead) != 1 {
		t.Fatalf("%d items are parked, want 1", len(dead))
	}
	if dead[0].Target != "poison.md" {
		t.Errorf("the parked item is %s", dead[0].Target)
	}
	if dead[0].LastErr != "this never works" {
		t.Errorf("the parked item does not carry what went wrong: %q", dead[0].LastErr)
	}
	if dead[0].Attempts != MaxAttempts {
		t.Errorf("the parked item records %d attempts, want %d",
			dead[0].Attempts, MaxAttempts)
	}

	// And the run that killed it is the run that says so, by name.
	var named bool
	for _, rep := range reports {
		for _, target := range rep.DeadLettered {
			if target == "poison.md" {
				named = true
			}
		}
	}
	if !named {
		t.Error("no drain report named the item it parked; the digest would " +
			"report work stopping without saying what stopped")
	}
}

// Named exactly once. The run that reaches the cap announces it; later runs
// have nothing new to say, and a report that re-announced every parked item on
// every cycle would be noise nobody reads.
func TestAnItemIsAnnouncedParkedOnlyOnce(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	if err := q.Enqueue(ctx, StageEnrich, "poison.md", "why"); err != nil {
		t.Fatal(err)
	}
	reports := drainUntil(t, q, StageEnrich, 6, func(context.Context, WorkItem) error {
		return errors.New("no")
	})

	var announced int
	for _, rep := range reports {
		announced += len(rep.DeadLettered)
	}
	if announced != 1 {
		t.Errorf("the item was announced parked %d times across six cycles, want 1",
			announced)
	}
}

// A parked item is not handed out again. That is the whole claim: nothing
// retries silently forever.
func TestAParkedItemIsNotDrained(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	if err := q.Enqueue(ctx, StageEnrich, "poison.md", "why"); err != nil {
		t.Fatal(err)
	}
	var attempts int
	drainUntil(t, q, StageEnrich, 3, func(context.Context, WorkItem) error {
		attempts++
		return errors.New("no")
	})
	before := attempts

	drainUntil(t, q, StageEnrich, 5, func(context.Context, WorkItem) error {
		attempts++
		return errors.New("no")
	})
	if attempts != before {
		t.Errorf("a parked item was attempted %d more times over five further "+
			"cycles", attempts-before)
	}
}

// Parking takes an item out of the owed count, so the depth can fall and the age
// threshold can clear. A parked item counted as owed would keep a red number on
// the dashboard forever with nothing anyone could do to clear it.
func TestParkingTakesAnItemOutOfTheOwedCount(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	if err := q.Enqueue(ctx, StageEnrich, "poison.md", "why"); err != nil {
		t.Fatal(err)
	}
	if err := q.Enqueue(ctx, StageEnrich, "fine.md", "why"); err != nil {
		t.Fatal(err)
	}

	drainUntil(t, q, StageEnrich, 4, func(_ context.Context, it WorkItem) error {
		if it.Target == "poison.md" {
			return errors.New("no")
		}
		return nil
	})

	depth, age, err := q.Depth(ctx, StageEnrich)
	if err != nil {
		t.Fatal(err)
	}
	if depth != 0 {
		t.Errorf("Depth = %d with one item finished and one parked, want 0", depth)
	}
	if age != 0 {
		t.Errorf("age = %s with nothing owed, want 0", age)
	}

	// But it is still counted as parked, so the queue does not simply look empty.
	parked, err := q.Parked(ctx, StageEnrich)
	if err != nil {
		t.Fatal(err)
	}
	if parked != 1 {
		t.Errorf("Parked = %d, want 1 — a queue with a parked item that reports "+
			"nothing at all is a queue that lost work silently", parked)
	}
}

// The half that is easy to miss.
//
// The reconcile scan re-discovers the same gaps every cycle. If re-discovery
// revived a parked item, its attempt count would reset nightly, the cap would
// never be reached twice, and "nothing retries forever" would be false in the
// way that is hardest to see: the queue looks healthy and the same broken item
// is paid for every night.
func TestReDiscoveryDoesNotReviveAParkedItem(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	if err := q.Enqueue(ctx, StageEnrich, "poison.md", "why"); err != nil {
		t.Fatal(err)
	}
	drainUntil(t, q, StageEnrich, 4, func(context.Context, WorkItem) error {
		return errors.New("no")
	})

	// The next cycle's scan finds the same gap and says so again.
	if err := q.Enqueue(ctx, StageEnrich, "poison.md", "still the same gap"); err != nil {
		t.Fatal(err)
	}

	depth, _, err := q.Depth(ctx, StageEnrich)
	if err != nil {
		t.Fatal(err)
	}
	if depth != 0 {
		t.Fatalf("re-discovery put a parked item back in the queue; the retry cap " +
			"resets on every scan and nothing ever stays parked")
	}
	dead, err := q.Dead(ctx, StageEnrich)
	if err != nil {
		t.Fatal(err)
	}
	if len(dead) != 1 || dead[0].Attempts != MaxAttempts {
		t.Errorf("after re-discovery the parked item is %+v", dead)
	}
}

// Reviving is deliberate, and it works.
func TestReviveReturnsAParkedItemToTheQueue(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	if err := q.Enqueue(ctx, StageEnrich, "poison.md", "why"); err != nil {
		t.Fatal(err)
	}
	drainUntil(t, q, StageEnrich, 4, func(context.Context, WorkItem) error {
		return errors.New("no")
	})
	dead, err := q.Dead(ctx, StageEnrich)
	if err != nil || len(dead) != 1 {
		t.Fatalf("the fixture did not park: %v %v", dead, err)
	}

	if err := q.Revive(ctx, dead[0].ID); err != nil {
		t.Fatal(err)
	}

	// Back in the queue, with a clean slate — a revival that kept the old count
	// would park again on its first failure.
	items, err := q.Pending(ctx, StageEnrich, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 1 {
		t.Fatalf("%d items are owed after a revive, want 1", len(items))
	}
	if items[0].Attempts != 0 {
		t.Errorf("the revived item carries %d attempts, want 0", items[0].Attempts)
	}
	if items[0].LastErr != "" {
		t.Errorf("the revived item still carries %q", items[0].LastErr)
	}

	var handled int
	drainUntil(t, q, StageEnrich, 1, func(context.Context, WorkItem) error {
		handled++
		return nil
	})
	if handled != 1 {
		t.Error("the revived item was not handed out")
	}
}

// Reviving something that is not parked is refused. A live item's retry count is
// still counting, and clearing it would silently extend the cap.
func TestReviveRefusesALiveItem(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	if err := q.Enqueue(ctx, StageEnrich, "a.md", "why"); err != nil {
		t.Fatal(err)
	}
	items, err := q.Pending(ctx, StageEnrich, 0)
	if err != nil {
		t.Fatal(err)
	}
	if err := q.Revive(ctx, items[0].ID); err == nil {
		t.Error("a live item was revived, which resets a retry count that is " +
			"still counting")
	}
}

// The cap is three unless something says otherwise. The number is the design's;
// this is what stops it drifting.
func TestTheDefaultRetryCapIsThree(t *testing.T) {
	if MaxAttempts != 3 {
		t.Errorf("MaxAttempts = %d, want the design's 3", MaxAttempts)
	}
	ctx := context.Background()
	q := newQueue(t)
	if err := q.Enqueue(ctx, StageEnrich, "a.md", "why"); err != nil {
		t.Fatal(err)
	}
	var attempts int
	drainUntil(t, q, StageEnrich, 10, func(context.Context, WorkItem) error {
		attempts++
		return errors.New("no")
	})
	if attempts != 3 {
		t.Errorf("a default queue attempted a failing item %d times, want 3", attempts)
	}
}

// The override moves the cap, which is what lets the cursor's own tests hold
// parking still while they check something else.
func TestTheRetryCapIsSettable(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	q.SetMaxAttempts(5)
	if err := q.Enqueue(ctx, StageEnrich, "a.md", "why"); err != nil {
		t.Fatal(err)
	}
	var attempts int
	drainUntil(t, q, StageEnrich, 10, func(context.Context, WorkItem) error {
		attempts++
		return errors.New("no")
	})
	if attempts != 5 {
		t.Errorf("a queue capped at 5 attempted %d times", attempts)
	}
}

// Parking is per item. One poison item does not take its neighbours with it.
func TestParkingOneItemDoesNotParkAnother(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	for _, target := range []string{"poison.md", "slow.md"} {
		if err := q.Enqueue(ctx, StageEnrich, target, "why"); err != nil {
			t.Fatal(err)
		}
	}
	// One always fails; the other fails once and then works.
	slowFailures := 0
	drainUntil(t, q, StageEnrich, 4, func(_ context.Context, it WorkItem) error {
		if it.Target == "poison.md" {
			return errors.New("no")
		}
		if slowFailures == 0 {
			slowFailures++
			return errors.New("transient")
		}
		return nil
	})

	dead, err := q.Dead(ctx, StageEnrich)
	if err != nil {
		t.Fatal(err)
	}
	if len(dead) != 1 || dead[0].Target != "poison.md" {
		t.Errorf("parked items are %+v, want only poison.md", dead)
	}
}

// Parked items belong to their owner, like everything else in this table.
func TestParkedItemsAreScopedToTheirOwner(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	if err := q.Enqueue(ctx, StageEnrich, "a.md", "why"); err != nil {
		t.Fatal(err)
	}
	if err := q.Enqueue(ctx, "rollup", "b.md", "why"); err != nil {
		t.Fatal(err)
	}
	drainUntil(t, q, StageEnrich, 4, func(context.Context, WorkItem) error {
		return errors.New("no")
	})

	if n, _ := q.Parked(ctx, "rollup"); n != 0 {
		t.Errorf("rollup has %d parked items after enrich parked one", n)
	}
	dead, err := q.Dead(ctx, "rollup")
	if err != nil {
		t.Fatal(err)
	}
	if len(dead) != 0 {
		t.Errorf("rollup's parked list carries %d of another owner's items", len(dead))
	}
}

// --- Fail's own contract ----------------------------------------------------
//
// Both of these are reached through Drain in ordinary use, and neither can be
// distinguished there: the drain never hands out a parked item, and a completed
// neighbour masks a Fail that reached too far. Fail is exported, so its contract
// is testable directly — and that is the only place these two properties show.

// Failing an already-parked item does not announce it again. A digest that
// re-announced every parked item on every cycle would be noise nobody reads.
func TestFailingAnAlreadyParkedItemDoesNotAnnounceItAgain(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	if err := q.Enqueue(ctx, StageEnrich, "a.md", "why"); err != nil {
		t.Fatal(err)
	}
	items, err := q.Pending(ctx, StageEnrich, 0)
	if err != nil {
		t.Fatal(err)
	}
	id := items[0].ID

	var announced int
	for i := 0; i < MaxAttempts+3; i++ {
		parked, err := q.Fail(ctx, id, "no")
		if err != nil {
			t.Fatal(err)
		}
		if parked {
			announced++
		}
	}
	if announced != 1 {
		t.Errorf("Fail announced the parking %d times over %d failures, want 1",
			announced, MaxAttempts+3)
	}
}

// Failing one item touches one item. A Fail whose WHERE clause reached further
// would park a whole owner's queue on one bad note, and through a drain it is
// invisible — a neighbour completed straight afterwards overwrites the damage.
func TestFailingOneItemLeavesItsNeighbourAlone(t *testing.T) {
	ctx := context.Background()
	q := newQueue(t)
	for _, target := range []string{"poison.md", "bystander.md"} {
		if err := q.Enqueue(ctx, StageEnrich, target, "why"); err != nil {
			t.Fatal(err)
		}
	}
	items, err := q.Pending(ctx, StageEnrich, 0)
	if err != nil {
		t.Fatal(err)
	}
	poison := items[0]

	for i := 0; i < MaxAttempts; i++ {
		if _, err := q.Fail(ctx, poison.ID, "no"); err != nil {
			t.Fatal(err)
		}
	}

	parked, err := q.Parked(ctx, StageEnrich)
	if err != nil {
		t.Fatal(err)
	}
	if parked != 1 {
		t.Errorf("Parked = %d after failing one item to its cap, want 1", parked)
	}
	still, err := q.Pending(ctx, StageEnrich, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(still) != 1 || still[0].Target != "bystander.md" {
		t.Fatalf("the owed queue is %+v, want only bystander.md", still)
	}
	if still[0].Attempts != 0 {
		t.Errorf("the bystander carries %d attempts from its neighbour's failures",
			still[0].Attempts)
	}
}
