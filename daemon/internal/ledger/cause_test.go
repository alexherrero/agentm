package ledger

import (
	"context"
	"reflect"
	"testing"
	"time"
)

// The pending set comes back in the night's order: never judged, changed,
// retry, owed the deep pass, owed under an older judgment hash, skipped — and
// within a cause the least recently judged first (task 181 step 4, #784).
func TestPendingIsOrderedByCause(t *testing.T) {
	l := newLedger(t)
	at := func(day int) time.Time { return time.Date(2026, 9, day, 9, 0, 0, 0, time.UTC) }
	for _, e := range []Entry{
		{Target: "changed.md", Version: "v2", RulesHash: "j2", InputKey: "old", Outcome: Done, At: at(28)},
		{Target: "retry.md", Version: "v2", RulesHash: "j2", InputKey: "k", Outcome: Failed, At: at(27)},
		{Target: "pass-new.md", Version: "v1", RulesHash: "j2", OutputKey: "k", Outcome: Done, At: at(26)},
		{Target: "pass-old.md", Version: "v1", RulesHash: "j2", OutputKey: "k", Outcome: Done, At: at(20)},
		{Target: "judgment.md", Version: "v2", RulesHash: "j1", OutputKey: "k", Outcome: Done, At: at(10)},
		{Target: "skipped.md", Version: "v2", RulesHash: "j2", InputKey: "k", Outcome: Skipped, At: at(1)},
	} {
		e.Stage = StageEnrich
		mustRecord(t, l, e)
	}
	var targets []Target
	for _, rel := range []string{"skipped.md", "judgment.md", "pass-old.md", "pass-new.md",
		"retry.md", "changed.md", "never.md"} {
		targets = append(targets, Target{Rel: rel, Key: "new-" + rel})
	}
	rep, err := l.Pending(context.Background(), StageEnrich, Version{Stage: "v2", Rules: "j2"}, targets)
	if err != nil {
		t.Fatal(err)
	}
	var got []string
	for _, it := range rep.Pending {
		got = append(got, it.Target)
	}
	want := []string{"never.md", "changed.md", "retry.md", "pass-old.md", "pass-new.md",
		"judgment.md", "skipped.md"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("order = %v\nwant    %v", got, want)
	}
	wantCauses := map[string]int{"never": 1, "changed": 1, "retry": 1, "deep pass": 2,
		"judgment": 1, "skipped": 1}
	if !reflect.DeepEqual(rep.Causes, wantCauses) {
		t.Errorf("causes = %v, want %v", rep.Causes, wantCauses)
	}
}

// Two contract edits in a row that each move the judgment hash: the second
// night does not re-pick the notes the first night just judged. Taken in the
// row's time order, the least recently judged lead, and a note the first night
// paid for is the newest row there is.
func TestTwoContractEditsInARowDoNotRepickTheSameHead(t *testing.T) {
	l := newLedger(t)
	ctx := context.Background()
	notes := []string{"a.md", "b.md", "c.md", "d.md", "e.md", "f.md"}
	for i, rel := range notes {
		mustRecord(t, l, Entry{Stage: StageEnrich, Target: rel, Version: "v", RulesHash: "j1",
			OutputKey: "j1-" + rel, Outcome: Done,
			At: time.Date(2026, 9, 1+i, 9, 0, 0, 0, time.UTC)})
	}
	night := func(rules string, day int, budget int) []string {
		t.Helper()
		var targets []Target
		for _, rel := range notes {
			targets = append(targets, Target{Rel: rel, Key: rules + "-" + rel})
		}
		rep, err := l.Pending(ctx, StageEnrich, Version{Stage: "v", Rules: rules}, targets)
		if err != nil {
			t.Fatal(err)
		}
		var took []string
		for _, it := range rep.Pending[:budget] {
			took = append(took, it.Target)
			mustRecord(t, l, Entry{Stage: StageEnrich, Target: it.Target, Version: "v",
				RulesHash: rules, OutputKey: rules + "-" + it.Target, Outcome: Done,
				At: time.Date(2026, 10, day, 9, 0, 0, 0, time.UTC)})
		}
		return took
	}
	first := night("j2", 1, 2)
	second := night("j3", 2, 2)
	if !reflect.DeepEqual(first, []string{"a.md", "b.md"}) {
		t.Errorf("the first night took %v, want the two least recently judged", first)
	}
	if !reflect.DeepEqual(second, []string{"c.md", "d.md"}) {
		t.Errorf("the second night took %v; it re-picked the first night's notes or "+
			"skipped the next least recently judged", second)
	}
}
