package index

import (
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// The ranking-side rung's two stages (task 184). Each test builds the case
// where the stage and the plain fused list actually differ, and checks the
// guard the RULE file registered before any of this code existed.

func pathsIn(rows []Result) []string { return pathsOf(rows) }

func indexOf(rows []Result, path string) int {
	for i, r := range rows {
		if r.Path == path {
			return i
		}
	}
	return -1
}

func putVectors(t *testing.T, x *Index, vecs map[string][]float32) {
	t.Helper()
	rows := make([]VectorRow, 0, len(vecs))
	for rel, v := range vecs {
		rows = append(rows, VectorRow{DocID: docID(t, x, rel), MtimeNS: 1, Vec: v})
	}
	if _, err := x.PutVectors("m", rows); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}
}

// mmrPool is a fused pool whose three leaders are near-duplicates of each
// other and whose next two each cover something else.
func mmrPool(t *testing.T) (*Index, []Result) {
	t.Helper()
	x := newTestIndex(t)
	vecs := map[string][]float32{
		"memory/dup-a.md":    unit(1, 0.02, 0, 0),
		"memory/dup-b.md":    unit(1, 0, 0.02, 0),
		"memory/dup-c.md":    unit(1, 0.01, 0.01, 0),
		"memory/distinct.md": unit(0, 1, 0, 0),
		"memory/another.md":  unit(0, 0, 1, 0),
		"memory/tail.md":     unit(0, 0, 0, 1),
	}
	for rel := range vecs {
		addNote(t, x, rel, rel, "body")
	}
	putVectors(t, x, vecs)
	// Fused scores on RRF's own scale, in fused order.
	pool := []Result{
		{Path: "memory/dup-a.md", Score: 0.033},
		{Path: "memory/dup-b.md", Score: 0.032},
		{Path: "memory/dup-c.md", Score: 0.031},
		{Path: "memory/distinct.md", Score: 0.030},
		{Path: "memory/another.md", Score: 0.029},
		{Path: "memory/tail.md", Score: 0.010},
	}
	return x, pool
}

func TestMMRKeepsOneOfThreeNearDuplicatesInTheTopThree(t *testing.T) {
	x, pool := mmrPool(t)
	got, err := x.mmrRerank(pool, "m")
	if err != nil {
		t.Fatalf("mmrRerank: %v", err)
	}
	dups := 0
	for _, r := range got[:3] {
		if r.Path == "memory/dup-a.md" || r.Path == "memory/dup-b.md" || r.Path == "memory/dup-c.md" {
			dups++
		}
	}
	if dups != 1 {
		t.Fatalf("top three = %v, want exactly one of the three near-duplicates", pathsIn(got[:3]))
	}
	// The leader is still the leader: the first pick is relevance alone.
	if got[0].Path != "memory/dup-a.md" {
		t.Errorf("first = %s, want the fused leader", got[0].Path)
	}
	if len(got) != len(pool) {
		t.Errorf("returned %d rows from a pool of %d; MMR reorders, it never drops", len(got), len(pool))
	}
}

// The control for the test above: with no vectors there is no redundancy to
// see, and the order must be fusion's exactly.
func TestMMRWithoutVectorsIsTheFusedOrder(t *testing.T) {
	x := newTestIndex(t)
	pool := []Result{{Path: "a", Score: 0.03}, {Path: "b", Score: 0.02}, {Path: "c", Score: 0.01}}
	got, err := x.mmrRerank(pool, "m")
	if err != nil {
		t.Fatalf("mmrRerank: %v", err)
	}
	if want := []string{"a", "b", "c"}; !equalPaths(pathsIn(got), want) {
		t.Fatalf("order = %v, want %v", pathsIn(got), want)
	}
}

func TestMMRLeavesASingleCandidateUnchanged(t *testing.T) {
	x := newTestIndex(t)
	pool := []Result{{Path: "only.md", Score: 0.02, RawScore: 1.5, Penalty: "durable"}}
	got, err := x.mmrRerank(pool, "m")
	if err != nil {
		t.Fatalf("mmrRerank: %v", err)
	}
	if len(got) != 1 || got[0] != pool[0] {
		t.Fatalf("got %+v, want the one row untouched", got)
	}
}

// A demoted note may be pushed down by diversity, never lifted past a note
// that outranked it. The control half proves the fixture is one where MMR
// would lift it: the same pool with the flag removed puts it second.
func TestMMRNeverLiftsADemotedNotePastWhatOutrankedIt(t *testing.T) {
	x, pool := mmrPool(t)
	for _, class := range []string{note.ClassConsolidated, note.ClassSuperseded, note.ClassArchived} {
		demotedPool := append([]Result(nil), pool...)
		demotedPool[3].Penalty = class // memory/distinct.md
		got, err := x.mmrRerank(demotedPool, "m")
		if err != nil {
			t.Fatalf("mmrRerank: %v", err)
		}
		at := indexOf(got, "memory/distinct.md")
		for _, above := range []string{"memory/dup-a.md", "memory/dup-b.md", "memory/dup-c.md"} {
			if indexOf(got, above) > at {
				t.Errorf("%s: the demoted note rose above %s, which outranked it: %v",
					class, above, pathsIn(got))
			}
		}
	}
	got, _ := x.mmrRerank(pool, "m")
	if got[1].Path != "memory/distinct.md" {
		t.Fatalf("control: undemoted, distinct.md is at %d (%v); the fixture no longer "+
			"tests the guard", indexOf(got, "memory/distinct.md"), pathsIn(got))
	}
}

func equalPaths(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// hybridQuery is a hybrid query whose dense arm reads model "m".
func hybridQuery(text string, vec []float32) Query {
	return Query{Text: text, K: 10, Mode: ModeHybrid, Vector: vec, EmbedModel: "m"}
}

// Through Search: the flag reaches the stage, and the stage never serves a
// note from a walled area, because it only reorders what fusion was handed.
func TestMMRThroughSearchServesNoWalledNote(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/a.md", "homelab server", "the homelab server in the closet")
	addNote(t, x, "memory/b.md", "homelab server", "the homelab server again, nearly")
	addNote(t, x, "private/walled.md", "homelab server", "the homelab server, walled")
	putVectors(t, x, map[string][]float32{
		"memory/a.md":       unit(1, 0, 0),
		"memory/b.md":       unit(1, 0.05, 0),
		"private/walled.md": unit(0, 0, 1),
	})
	withWall(t, []string{"private"})
	q := hybridQuery(probeQuery, unit(1, 0, 0))
	q.MMR = true
	out, err := x.Search(q)
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if indexOf(out.Results, "private/walled.md") >= 0 {
		t.Fatalf("MMR served a walled note: %v", pathsIn(out.Results))
	}
	if len(out.Results) != 2 {
		t.Fatalf("results = %v, want the two unwalled notes", pathsIn(out.Results))
	}
}
