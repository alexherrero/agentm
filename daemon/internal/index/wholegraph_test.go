package index

import (
	"context"
	"testing"
)

// The whole link graph, read from real files through the real reconcile, so the
// edges are the ones the link extractor actually produces rather than rows
// hand-inserted into the table the query reads. A test that wrote its own links
// row could not catch the query and the extractor disagreeing about what a
// resolved link is.

func md(body string) string { return "---\ntitle: t\n---\n\n" + body + "\n" }

// graphFixture builds a corpus with a hub, two leaves, a mutual pair, a
// self-link, a repeated link and a dangling one — every shape the query has an
// opinion about, in one vault.
// Two details are deliberate and were both learned from a battery run.
//
// The hub is `zebra.md`, which sorts *last*. An earlier hub named `hub.md`
// sorted first, so dropping the cap's degree ordering and falling back to plain
// path order kept the same nodes and the mutation changed nothing.
//
// And the repeated citation is two different spellings — a wikilink and a
// markdown link — rather than the same wikilink twice. Measured: the extractor
// already collapses identical citations into one row, so a fixture repeating one
// spelling never reached the query's own dedup. Two spellings do produce two
// rows resolving to one note, which is what that dedup is for.
//
// Eight linked notes rather than four, so "the nodes came out sorted" is not
// something a map iteration can manage by luck.
func graphFixture(t *testing.T) *Index {
	t.Helper()
	x, vault := newVaultIndex(t)
	writeVaultNote(t, vault, "memory/zebra.md",
		md("Points at [[alpha]], [[beta]] and [[gamma]], and again at "+
			"[the alpha note](memory/alpha.md)."))
	writeVaultNote(t, vault, "memory/alpha.md", md("Points back at [[zebra]]."))
	writeVaultNote(t, vault, "memory/beta.md", md("Points at [[nobody-wrote-this]]."))
	writeVaultNote(t, vault, "memory/gamma.md", md("Points at [[delta]]."))
	writeVaultNote(t, vault, "memory/delta.md", md("Points at [[epsilon]]."))
	writeVaultNote(t, vault, "memory/epsilon.md", md("Points at [[omega]]."))
	writeVaultNote(t, vault, "memory/omega.md", md("Points at [[sigma]]."))
	writeVaultNote(t, vault, "memory/sigma.md", md("The end of the chain."))
	writeVaultNote(t, vault, "memory/lonely.md", md("Points at [[lonely]]."))
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}
	return x
}

func edgeSet(g LinkGraph) map[GraphEdge]bool {
	out := map[GraphEdge]bool{}
	for _, e := range g.Edges {
		out[e] = true
	}
	return out
}

func degreeOf(g LinkGraph, rel string) int {
	for _, n := range g.Nodes {
		if n.Rel == rel {
			return n.Degree
		}
	}
	return -1
}

// One test over the fixture, because these properties are all statements about
// the same query and splitting them would rebuild the corpus five times.
func TestLinkGraphShape(t *testing.T) {
	g, err := graphFixture(t).LinkGraph(context.Background(), 0)
	if err != nil {
		t.Fatal(err)
	}
	edges := edgeSet(g)

	// A link that resolves is an edge.
	if !edges[GraphEdge{"memory/zebra.md", "memory/alpha.md"}] {
		t.Errorf("the hub's link to alpha is not an edge: %+v", g.Edges)
	}
	// Both directions of a mutual pair are their own edges — the graph is
	// directed, and collapsing them would lose which note cited which.
	if !edges[GraphEdge{"memory/alpha.md", "memory/zebra.md"}] {
		t.Error("the return link from alpha is not an edge")
	}

	// A link to a note nobody wrote is not an edge. It is a real fact about the
	// corpus — it is what stub synthesis exists for — but it has no second
	// endpoint, and drawing it would invent a node for a file that is not there.
	for e := range edges {
		if e.Target == "memory/nobody-wrote-this.md" || e.Source == "" || e.Target == "" {
			t.Errorf("an unresolved link became an edge: %+v", e)
		}
	}

	// A note linking to itself is not an edge. It draws as a dot on top of
	// itself and inflates its own degree for saying nothing about the corpus.
	if edges[GraphEdge{"memory/lonely.md", "memory/lonely.md"}] {
		t.Error("a self-link became an edge")
	}
	if d := degreeOf(g, "memory/lonely.md"); d != -1 {
		t.Errorf("a note whose only link is to itself is in the graph with "+
			"degree %d; it has no relationships to draw", d)
	}

	// Two notes linked twice are linked once. A paragraph that cites the same
	// page twice is ordinary writing, and counting it twice would thicken the
	// line and inflate both degrees for one relationship.
	if got := len(g.Edges); got != 8 {
		t.Errorf("edges = %d, want 8; the target cited two ways was drawn twice: %+v",
			got, g.Edges)
	}

	// Degree counts both directions, which is what sizes a hub.
	if got := degreeOf(g, "memory/zebra.md"); got != 4 {
		t.Errorf("the hub's degree is %d, want 4 — three out and one back", got)
	}
	if got := degreeOf(g, "memory/beta.md"); got != 1 {
		t.Errorf("beta's degree is %d, want 1 — its dangling link was counted", got)
	}

	// Sorted, asserted as the exact expected order rather than as a pairwise
	// scan. A map iteration over a handful of nodes can come out sorted by
	// chance, and a test that only fails sometimes is not a test.
	want := []string{
		"memory/alpha.md", "memory/beta.md", "memory/delta.md",
		"memory/epsilon.md", "memory/gamma.md", "memory/omega.md",
		"memory/sigma.md", "memory/zebra.md",
	}
	if len(g.Nodes) != len(want) {
		t.Fatalf("nodes = %d, want %d: %+v", len(g.Nodes), len(want), g.Nodes)
	}
	for i, w := range want {
		if g.Nodes[i].Rel != w {
			t.Fatalf("node %d is %q, want %q — the nodes are not sorted",
				i, g.Nodes[i].Rel, w)
		}
	}

	for i := 1; i < len(g.Edges); i++ {
		a, b := g.Edges[i-1], g.Edges[i]
		if a.Source > b.Source || (a.Source == b.Source && a.Target > b.Target) {
			t.Fatalf("edges are not sorted: %+v then %+v", a, b)
		}
	}
}

// The cap keeps the hubs, says how many it dropped, and drops the edges that
// would otherwise point at nothing.
func TestTheCapKeepsHubsAndSaysWhatItDropped(t *testing.T) {
	g, err := graphFixture(t).LinkGraph(context.Background(), 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(g.Nodes) != 2 {
		t.Fatalf("nodes = %d under a cap of 2", len(g.Nodes))
	}
	if g.Dropped != 6 || g.Cap != 2 {
		t.Errorf("Dropped = %d and Cap = %d, want 6 and 2 — a cap nobody is told "+
			"about makes a partial picture look like the whole corpus",
			g.Dropped, g.Cap)
	}
	// The hub sorts last by path, so keeping it is evidence the cap ranked by
	// degree rather than falling back to path order.
	if degreeOf(g, "memory/zebra.md") < 0 {
		t.Error("the cap dropped the highest-degree node, which sorts last by path")
	}
	for _, e := range g.Edges {
		if degreeOf(g, e.Source) < 0 || degreeOf(g, e.Target) < 0 {
			t.Errorf("edge %+v points at a node the cap removed", e)
		}
	}
}

// Ties at the cap boundary break on path, so two runs keep the same nodes. A cap
// that chose differently each night would make the picture incomparable to
// yesterday's for a reason nothing in the corpus caused.
func TestTheCapIsStableAcrossRuns(t *testing.T) {
	x := graphFixture(t)
	first, err := x.LinkGraph(context.Background(), 2)
	if err != nil {
		t.Fatal(err)
	}
	second, err := x.LinkGraph(context.Background(), 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(first.Nodes) != len(second.Nodes) {
		t.Fatalf("%d nodes then %d", len(first.Nodes), len(second.Nodes))
	}
	for i := range first.Nodes {
		if first.Nodes[i] != second.Nodes[i] {
			t.Fatalf("node %d differs between runs: %+v then %+v",
				i, first.Nodes[i], second.Nodes[i])
		}
	}
}

// A corpus with no links is a graph with no nodes, not an error.
func TestACorpusWithNoLinksIsAnEmptyGraph(t *testing.T) {
	x, vault := newVaultIndex(t)
	writeVaultNote(t, vault, "memory/a.md", md("A note that links to nothing."))
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}
	g, err := x.LinkGraph(context.Background(), 0)
	if err != nil {
		t.Fatalf("an unlinked corpus produced an error: %v", err)
	}
	if len(g.Nodes) != 0 || len(g.Edges) != 0 {
		t.Errorf("got %d nodes and %d edges over a corpus with no links",
			len(g.Nodes), len(g.Edges))
	}
}
