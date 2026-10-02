package index

import (
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// A project's band is applied when a query runs, from the current reading
// (task 182 step 4). Stamped at index time it outlived its reading: on
// 2026-09-29 the primos charter still ranked at x0.5 days after the night read
// primos as active, because the charter had not been indexed since.

func TestABandChangeTakesEffectOnTheNextQueryWithoutAReindex(t *testing.T) {
	t.Cleanup(func() { note.SetProjectActivity(nil) })
	idx := openScratch(t)
	for _, f := range []string{"one", "two", "three", "four", "five", "six"} {
		indexOnDisk(t, idx, "agent/memory/semantic/filler-"+f+".md",
			"---\ntitle: "+f+"\ntype: reference\n---\n\nNotes about "+f+".\n")
	}
	note.SetProjectActivity(map[string]float64{"primos": 1.0})
	indexOnDisk(t, idx, "projects/primos/charter.md",
		"---\ntitle: primos charter\nkind: project-index\n---\n\nFiled under external, a nephew's review project.\n")

	score := func() Result {
		t.Helper()
		out, err := idx.Search(Query{Text: "nephew review", K: 5, Mode: ModeAnd})
		if err != nil {
			t.Fatal(err)
		}
		r, i := rowFor(out.Results, "charter.md")
		if i < 0 {
			t.Fatalf("the charter should match: %v", resultPaths(out.Results))
		}
		return r
	}
	active := score()
	if active.Score != active.RawScore {
		t.Fatalf("an active project's record takes no band: %.4f vs raw %.4f", active.Score, active.RawScore)
	}
	note.SetProjectActivity(map[string]float64{"primos": 0.5})
	if quiet := score(); quiet.Score != quiet.RawScore*0.5 {
		t.Errorf("after the reading moved to 0.5 the next query ranks at x0.5: %.4f vs raw %.4f",
			quiet.Score, quiet.RawScore)
	}
	note.SetProjectActivity(map[string]float64{"primos": 1.0})
	if back := score(); back.Score != back.RawScore {
		t.Errorf("back to active, back to full weight: %.4f vs raw %.4f", back.Score, back.RawScore)
	}
}

// A row indexed before this change carries the band it was stamped with. It is
// read now, never twice and never from the stale stamp.
func TestAnOldRowsStampedBandIsIgnored(t *testing.T) {
	t.Cleanup(func() { note.SetProjectActivity(nil) })
	rows := func() []Result {
		return []Result{{Path: "projects/primos/charter.md", Score: 10, Penalty: note.ClassProjectQuieter}}
	}
	note.SetProjectActivity(map[string]float64{"primos": 1.0})
	if got := penalizeRankAndDecay(rows(), 5, nil, time.Time{}, false, "", nil); got[0].Score != 10 {
		t.Errorf("an active project's old row keeps full weight, got %.2f", got[0].Score)
	}
	note.SetProjectActivity(map[string]float64{"primos": 0.5})
	if got := penalizeRankAndDecay(rows(), 5, nil, time.Time{}, false, "", nil); got[0].Score != 5 {
		t.Errorf("a quieter project's old row takes x0.5 once, got %.2f", got[0].Score)
	}
	if !strings.Contains(rows()[0].Penalty, note.ClassProjectQuieter) {
		t.Fatal("fixture")
	}
}
