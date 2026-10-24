package health

import (
	"testing"
	"time"
)

// A card standing refused is reported on its own and never pages: the night
// cannot judge it, so an alert over it could never clear (#793).
func TestARefusedCardIsCountedAndDoesNotPage(t *testing.T) {
	in := baseInput()
	in.Refused, in.OldestRefused = 1, in.Now.Add(-90*time.Hour)
	in.UnfiledSince, in.OldestUnfiledSince = 1, in.Now.Add(-2*time.Hour)
	in.Unfiled = 1
	rep := Evaluate(in)
	if rep.Red() {
		t.Fatalf("red over a refused card: %+v", rep.Alerts)
	}
	if rep.Queue.Refused != 1 || rep.Queue.RefusedOldestAge != Duration(90*time.Hour) {
		t.Errorf("queue = %+v, want 1 refused, 90h old", rep.Queue)
	}
	if rep.Queue.OldestAge != Duration(2*time.Hour) || rep.Queue.Inherited != 0 {
		t.Errorf("queue = %+v, want the waiting card's 2h and nothing inherited", rep.Queue)
	}
}
