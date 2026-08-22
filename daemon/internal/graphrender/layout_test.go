package graphrender

import (
	"math"
	"strings"
	"testing"
)

// A graph whose edges deliberately run *against* the initial placement.
//
// Phyllotaxis puts consecutive indices near each other, so a fixture that linked
// neighbours would let the initialisation take credit for what the simulation is
// supposed to do — measured: with the link force deleted, an earlier version of
// this fixture still passed "linked nodes settle nearer". Here node 0 links to
// node 7 across the spiral and node 1 is its unlinked neighbour, so only the link
// force can put 0 nearer 7 than 1.
func sample() Input {
	nodes := []Node{
		{Rel: "memory/a.md", Class: "semantic", Degree: 2},
		{Rel: "memory/b.md", Class: "procedural", Degree: 1},
		{Rel: "memory/c.md", Class: "semantic", Degree: 1},
		{Rel: "memory/d.md", Class: UnfiledClass, Degree: 1},
		{Rel: "memory/e.md", Class: "mocs", Degree: 1},
		{Rel: "memory/f.md", Class: "mocs", Degree: 1},
		{Rel: "memory/g.md", Class: "semantic", Degree: 1},
		{Rel: "memory/h.md", Class: "procedural", Degree: 2},
	}
	edges := []Edge{{0, 7}, {7, 6}, {2, 3}, {4, 5}}
	return Input{Nodes: nodes, Edges: edges}
}

// The bar this whole package is written against: same graph, same picture.
//
// Compared as the rendered bytes rather than as coordinates. The coordinates are
// what the layout produces, but the SVG is what lands in the vault and gets
// committed, and a render that agreed to sixteen decimals while emitting
// different text would still show up as a diff every night.
func TestTheSameGraphRendersTheSameBytes(t *testing.T) {
	first := Run(sample()).SVG(800, 600)
	second := Run(sample()).SVG(800, 600)
	if first != second {
		t.Fatal("two runs over the same graph produced different SVG; the " +
			"nightly scorecard would show a change every night that nothing made")
	}
	if len(first) < 200 {
		t.Fatalf("the render is %d bytes, which is too short to have drawn "+
			"anything — two identical empties would pass the check above", len(first))
	}
}

// And a different graph renders differently, so the check above is not passing
// because the renderer emits a constant.
func TestADifferentGraphRendersDifferently(t *testing.T) {
	in := sample()
	moved := sample()
	moved.Edges = append(moved.Edges, Edge{Source: 1, Target: 4})

	if Run(in).SVG(800, 600) == Run(moved).SVG(800, 600) {
		t.Error("adding an edge changed nothing in the render")
	}
}

// Node order is the determinism story, so a graph handed over in a different
// order is a different picture — and that is why the ordering is settled in the
// index query rather than here. Asserted so the coupling is visible: if this
// ever stops being true, the sorting in `index.LinkGraph` has stopped being
// load-bearing and somebody should know.
func TestOrderDecidesTheLayout(t *testing.T) {
	in := sample()
	swapped := sample()
	swapped.Nodes[0], swapped.Nodes[1] = swapped.Nodes[1], swapped.Nodes[0]

	if Run(in).SVG(800, 600) == Run(swapped).SVG(800, 600) {
		t.Error("reordering the nodes changed nothing, so the ordering the " +
			"index query guarantees is not what makes this deterministic")
	}
}

// The simulation runs d3's schedule rather than stopping when it feels settled.
//
// Against the literal 300 rather than against `tickCount`. Comparing to the
// constant the implementation uses is not a test — shortening the schedule moves
// both sides of the comparison, which is exactly what a battery run showed.
func TestItRunsTheFullSchedule(t *testing.T) {
	if got := Run(sample()).Ticks; got != 300 {
		t.Errorf("Ticks = %d, want d3's 300", got)
	}
}

// Linked nodes end up nearer each other than unlinked ones. This is the only
// test that checks the layout *means* something — everything else here would
// pass over a renderer that placed nodes on a spiral and never simulated.
func TestLinkedNodesSettleNearerThanUnlinkedOnes(t *testing.T) {
	l := Run(sample())
	at := func(rel string) Node {
		for _, n := range l.Nodes {
			if n.Rel == rel {
				return n
			}
		}
		t.Fatalf("no node %s", rel)
		return Node{}
	}
	dist := func(a, b Node) float64 { return math.Hypot(a.X-b.X, a.Y-b.Y) }

	// a→h is an edge across the spiral; a and b are spiral neighbours with no
	// edge. Only the link force can invert that.
	linked := dist(at("memory/a.md"), at("memory/h.md"))
	unlinked := dist(at("memory/a.md"), at("memory/b.md"))
	if linked >= unlinked {
		t.Errorf("a linked pair placed far apart sits %.1f apart, and an "+
			"unlinked pair placed adjacent %.1f — the edges are not pulling",
			linked, unlinked)
	}
}

// An empty corpus is a state, not an error, and the picture says which.
func TestAnEmptyGraphRendersAndSaysSo(t *testing.T) {
	out := Run(Input{}).SVG(800, 600)
	if !strings.Contains(out, "no links in the corpus yet") {
		t.Errorf("an empty graph rendered without saying it was empty: %q", out)
	}
	if !strings.Contains(out, "</svg>") {
		t.Error("the empty render is not a closed document")
	}
}

// One node has no span on either axis. Fitting it must not divide by zero and
// put it at infinity.
func TestASingleNodeIsPlacedInsideTheViewport(t *testing.T) {
	l := Run(Input{Nodes: []Node{{Rel: "a.md", Degree: 0}}})
	out := l.SVG(800, 600)
	if strings.Contains(out, "NaN") || strings.Contains(out, "Inf") {
		t.Fatalf("a single node produced a non-finite coordinate: %s", out)
	}
	if !strings.Contains(out, "<circle") {
		t.Error("the single node was not drawn")
	}
}

// Degree sizes the hub, and on a square root so one very connected note does not
// take over the picture.
func TestAHubIsBiggerButNotOverwhelming(t *testing.T) {
	leaf, hub := radiusFor(1), radiusFor(100)
	if hub <= leaf {
		t.Errorf("a hub of 100 links is %.1f and a leaf %.1f", hub, leaf)
	}
	if hub > 6*leaf {
		t.Errorf("a hub of 100 links is %.1f against a leaf's %.1f — more than "+
			"six times the radius swallows everything around it", hub, leaf)
	}
}

// A class keeps its colour, and an unknown one is drawn as unfiled rather than
// as whichever colour came first.
func TestColoursAreFixedAndUnknownClassesAreDrab(t *testing.T) {
	if colourFor("semantic") == colourFor("procedural") {
		t.Error("two classes share a colour")
	}
	if got := colourFor("a-class-nobody-defined"); got != unfiledColour {
		t.Errorf("an unknown class drew as %s, want the unfiled colour", got)
	}
	if got := colourFor(UnfiledClass); got != unfiledColour {
		t.Errorf("unfiled drew as %s", got)
	}
}

// The legend lists what is actually in the picture, in a fixed order.
func TestTheLegendIsSortedAndCoversWhatIsDrawn(t *testing.T) {
	got := Run(sample()).Classes()
	want := []string{"mocs", "procedural", "semantic", UnfiledClass}
	if len(got) != len(want) {
		t.Fatalf("Classes() = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("Classes() = %v, want %v", got, want)
		}
	}
}

// A note whose name carries XML syntax must not break the document.
func TestANoteNamedWithMarkupDoesNotBreakTheDocument(t *testing.T) {
	l := Run(Input{Nodes: []Node{{Rel: `memory/Q&A <draft>.md`, Degree: 0}}})
	out := l.SVG(400, 300)
	if strings.Contains(out, "Q&A <draft>") {
		t.Error("the note's name went into the document unescaped")
	}
	if !strings.Contains(out, "Q&amp;A &lt;draft&gt;") {
		t.Errorf("the escaped name is not in the output: %s", out)
	}
}

// Zero renders as zero. `-0.00` and `0.00` are the same number and different
// text, and the determinism check compares text.
func TestNegativeZeroIsNormalised(t *testing.T) {
	if got := f(math.Copysign(0, -1)); got != "0.00" {
		t.Errorf("f(-0) = %q, want %q", got, "0.00")
	}
}
