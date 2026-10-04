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

// spreadFixture: a seed the query finds, linking to an answer neither arm can
// reach — no shared term, and no vector for the dense arm to score.
func spreadFixture(t *testing.T) *Index {
	t.Helper()
	x := newTestIndex(t)
	// Targets first: a wikilink resolves against the paths already indexed.
	addNote(t, x, "memory/answer.md", "closet machine notes", "lorem ipsum dolor")
	addNote(t, x, "memory/seed.md", "homelab server", "the homelab server, see [[answer]]")
	putVectors(t, x, map[string][]float32{"memory/seed.md": unit(1, 0, 0)})
	return x
}

func TestSpreadSurfacesTheAnswerASeedLinksTo(t *testing.T) {
	x := spreadFixture(t)
	off, err := x.Search(hybridQuery(probeQuery, unit(1, 0, 0)))
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if indexOf(off.Results, "memory/answer.md") >= 0 {
		t.Fatalf("control: the answer is reachable without activation (%v); the "+
			"fixture no longer tests the stage", pathsIn(off.Results))
	}
	q := hybridQuery(probeQuery, unit(1, 0, 0))
	q.Spread = true
	on, err := x.Search(q)
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	at := indexOf(on.Results, "memory/answer.md")
	if at < 0 {
		t.Fatalf("results = %v, want the linked answer admitted", pathsIn(on.Results))
	}
	// The registered decay, written as the literal the RULE file fixed rather
	// than read back from the constant under test.
	seed := on.Results[indexOf(on.Results, "memory/seed.md")]
	if got, want := on.Results[at].Score, seed.Score*0.5; got > want+1e-12 {
		t.Errorf("the neighbour scored %.6f, above half its seed's %.6f", got, seed.Score)
	}
}

func TestSpreadNeverAdmitsAWalledNeighbour(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "private/secret.md", "closet machine notes", "lorem ipsum")
	addPenalizedNote(t, x, "memory/old.md", "closet machine notes", "lorem ipsum", note.ClassArchived)
	addNote(t, x, "memory/seed.md", "homelab server", "the homelab server, see [[secret]] and [[old]]")
	putVectors(t, x, map[string][]float32{"memory/seed.md": unit(1, 0, 0)})
	withWall(t, []string{"private"})

	q := hybridQuery(probeQuery, unit(1, 0, 0))
	q.Spread = true
	out, err := x.Search(q)
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	for _, walled := range []string{"private/secret.md", "memory/old.md"} {
		if indexOf(out.Results, walled) >= 0 {
			t.Errorf("activation admitted %s: %v", walled, pathsIn(out.Results))
		}
	}
	// The explicit archive query lifts the archive wall for activation as it
	// does for the arms, and never the recall wall.
	q.IncludeArchived = true
	out, err = x.Search(q)
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if indexOf(out.Results, "memory/old.md") < 0 {
		t.Errorf("an archive query did not get the archived neighbour back: %v", pathsIn(out.Results))
	}
	if indexOf(out.Results, "private/secret.md") >= 0 {
		t.Errorf("an archive query reached through the recall wall: %v", pathsIn(out.Results))
	}
}

func TestSpreadTerminatesOnALinkCycle(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/ping.md", "closet machine notes", "lorem ipsum")
	addNote(t, x, "memory/pong.md", "homelab server", "the homelab server, see [[ping]]")
	// Re-index ping now that pong exists, so each links the other.
	addNote(t, x, "memory/ping.md", "closet machine notes", "lorem ipsum, see [[pong]]")
	putVectors(t, x, map[string][]float32{"memory/pong.md": unit(1, 0, 0)})

	q := hybridQuery(probeQuery, unit(1, 0, 0))
	q.Spread = true
	out, err := x.Search(q)
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	seen := map[string]int{}
	for _, r := range out.Results {
		seen[r.Path]++
	}
	if seen["memory/ping.md"] != 1 || seen["memory/pong.md"] != 1 || len(out.Results) != 2 {
		t.Fatalf("results = %v, want each note of the cycle exactly once", pathsIn(out.Results))
	}
}

func TestSpreadFollowsAnEntityPageBacklink(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/seed.md", "homelab server", "the homelab server")
	addNote(t, x, "memory/entities/repos/acme-widget.md", "acme/widget", "Mentioned in [[seed]].")
	addNote(t, x, "memory/other-backlink.md", "elsewhere", "an ordinary note citing [[seed]]")
	putVectors(t, x, map[string][]float32{"memory/seed.md": unit(1, 0, 0)})

	q := hybridQuery(probeQuery, unit(1, 0, 0))
	q.Spread = true
	out, err := x.Search(q)
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if indexOf(out.Results, "memory/entities/repos/acme-widget.md") < 0 {
		t.Errorf("the entity page linking the seed was not admitted: %v", pathsIn(out.Results))
	}
	if indexOf(out.Results, "memory/other-backlink.md") >= 0 {
		t.Errorf("an ordinary backlink was followed; only entity pages are: %v", pathsIn(out.Results))
	}
}

// A hub seed admits three, and the three nearest the query.
func TestSpreadAdmitsThreeNeighboursPerSeedNearestTheQuery(t *testing.T) {
	x := newTestIndex(t)
	// Nearest the query is the reverse of path order, so the two cannot be
	// mistaken for each other.
	vecs := map[string][]float32{
		"memory/n5.md": unit(0.9, 0.1, 0),
		"memory/n4.md": unit(0.8, 0.2, 0),
		"memory/n3.md": unit(0.7, 0.3, 0),
		"memory/n2.md": unit(0.1, 0.9, 0),
		"memory/n1.md": unit(0, 0.1, 0.9),
	}
	for rel := range vecs {
		addNote(t, x, rel, "closet machine notes", "lorem ipsum")
	}
	addNote(t, x, "memory/hub.md", "homelab server",
		"the homelab server: [[n5]] [[n4]] [[n3]] [[n2]] [[n1]]")
	got, err := x.spreadActivation([]Result{{Path: "memory/hub.md", Score: 0.03}},
		hybridQuery(probeQuery, unit(1, 0, 0)), "", "")
	if err != nil {
		t.Fatalf("spreadActivation: %v", err)
	}
	// Three, the registered cap, as a literal: a test reading the constant
	// would follow it wherever it moved.
	if len(got) != 1+3 {
		t.Fatalf("admitted %d neighbours, want 3: %v", len(got)-1, pathsIn(got))
	}
	// No vectors stored yet: the cap falls back to path order.
	for _, want := range []string{"memory/n1.md", "memory/n2.md", "memory/n3.md"} {
		if indexOf(got, want) < 0 {
			t.Errorf("with no vectors, %s (path order) was not admitted: %v", want, pathsIn(got))
		}
	}
	putVectors(t, x, vecs)
	got, err = x.spreadActivation([]Result{{Path: "memory/hub.md", Score: 0.03}},
		hybridQuery(probeQuery, unit(1, 0, 0)), "", "")
	if err != nil {
		t.Fatalf("spreadActivation: %v", err)
	}
	for _, want := range []string{"memory/n5.md", "memory/n4.md", "memory/n3.md"} {
		if indexOf(got, want) < 0 {
			t.Errorf("%s, among the three nearest the query, was not admitted: %v", want, pathsIn(got))
		}
	}
}

// A consolidated neighbour keeps its demotion, so activation cannot be what
// lifts a card past the lesson that absorbed it.
func TestSpreadDemotesAConsolidatedNeighbour(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/plain.md", "closet machine notes", "lorem ipsum")
	addPenalizedNote(t, x, "memory/card.md", "closet machine notes", "lorem ipsum", note.ClassConsolidated)
	addNote(t, x, "memory/seed.md", "homelab server", "the homelab server: [[plain]] [[card]]")
	got, err := x.spreadActivation([]Result{{Path: "memory/seed.md", Score: 0.03}},
		hybridQuery(probeQuery, unit(1, 0, 0)), "", "")
	if err != nil {
		t.Fatalf("spreadActivation: %v", err)
	}
	plain, card := got[indexOf(got, "memory/plain.md")], got[indexOf(got, "memory/card.md")]
	if !(card.Score < plain.Score) {
		t.Fatalf("consolidated neighbour scored %.6f against a plain one's %.6f; the demotion was lost",
			card.Score, plain.Score)
	}
}

// Off by default: a query that names neither stage is served exactly as before.
func TestStagesAreOffByDefault(t *testing.T) {
	x := spreadFixture(t)
	addNote(t, x, "memory/twin.md", "homelab server", "the homelab server, see [[answer]] too")
	putVectors(t, x, map[string][]float32{"memory/twin.md": unit(1, 0.01, 0)})
	base, err := x.Search(hybridQuery(probeQuery, unit(1, 0, 0)))
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(base.Results) != 2 {
		t.Fatalf("a default search returned %v, want the two notes the arms found", pathsIn(base.Results))
	}
	if indexOf(base.Results, "memory/answer.md") >= 0 {
		t.Fatalf("a default search activated a link: %v", pathsIn(base.Results))
	}
}

// A neighbour already in the list appears once, with the better of its two
// scores: activation can raise a fused row, never duplicate or lower it.
func TestSpreadKeepsTheBetterScoreForANoteAlreadyListed(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/low.md", "closet machine notes", "lorem ipsum")
	addNote(t, x, "memory/high.md", "closet machine notes", "lorem ipsum")
	addNote(t, x, "memory/seed.md", "homelab server", "the homelab server: [[low]] [[high]]")
	// Both targets sit below the five seeds; a seed is never activated.
	fused := []Result{
		{Path: "memory/seed.md", Score: 0.030},
		{Path: "memory/f1.md", Score: 0.029},
		{Path: "memory/f2.md", Score: 0.028},
		{Path: "memory/f3.md", Score: 0.027},
		{Path: "memory/f4.md", Score: 0.026},
		{Path: "memory/high.md", Score: 0.025},
		{Path: "memory/low.md", Score: 0.001},
	}
	got, err := x.spreadActivation(fused, hybridQuery(probeQuery, unit(1, 0, 0)), "", "")
	if err != nil {
		t.Fatalf("spreadActivation: %v", err)
	}
	if len(got) != len(fused) {
		t.Fatalf("results = %v, want the seven notes once each", pathsIn(got))
	}
	// 0.030 × 0.5 = 0.015: above low's fused 0.001, below high's 0.025.
	if s := got[indexOf(got, "memory/low.md")].Score; s < 0.015-1e-9 || s > 0.015+1e-9 {
		t.Errorf("low.md scored %.6f, want its activated 0.015", s)
	}
	if s := got[indexOf(got, "memory/high.md")].Score; s != 0.025 {
		t.Errorf("high.md scored %.6f, want its own fused 0.025 kept", s)
	}
}

// A seed is never activated, even by a stronger seed linking it: the hop
// reaches past the leaders, never reshuffles them.
func TestSpreadNeverActivatesASeed(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/weak-seed.md", "closet machine notes", "lorem ipsum")
	addNote(t, x, "memory/strong-seed.md", "homelab server", "the homelab server: [[weak-seed]]")
	fused := []Result{
		{Path: "memory/strong-seed.md", Score: 0.033},
		{Path: "memory/weak-seed.md", Score: 0.010},
	}
	got, err := x.spreadActivation(fused, hybridQuery(probeQuery, unit(1, 0, 0)), "", "")
	if err != nil {
		t.Fatalf("spreadActivation: %v", err)
	}
	if s := got[indexOf(got, "memory/weak-seed.md")].Score; s != 0.010 {
		t.Fatalf("a seed was activated by another: it scored %.6f, want its own 0.010", s)
	}
}
