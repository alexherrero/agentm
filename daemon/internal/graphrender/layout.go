// Package graphrender lays out and draws the memory context graph.
//
// The design asks for a force-directed render "laid out to approximate
// Obsidian's algorithm", which is a concrete instruction rather than a vague
// one: Obsidian's graph view is d3-force, so approximating it means implementing
// d3-force's three default forces with d3's own constants — link, many-body and
// centre, over 300 ticks with d3's alpha schedule. The constants below are
// d3's, named, rather than numbers picked because a picture looked right.
//
// # Determinism
//
// The render has to be byte-identical across runs, which rules out the usual
// force-layout habit of seeding positions at random. d3 does not actually
// randomise either — `initializeNodes` places nodes on a phyllotactic spiral by
// index — so the faithful implementation and the deterministic one are the same
// implementation. Given a node order, everything downstream is fixed: IEEE-754
// arithmetic in a fixed sequence produces the same bits every time.
//
// That makes node order the whole determinism story, and it is settled before
// this package sees the graph — `index.LinkGraph` sorts nodes by path and edges
// by endpoint. Nothing here iterates a map.
package graphrender

import (
	"math"
	"sort"
)

// d3-force's defaults, in d3's own terms.
const (
	// alphaMin and tickCount give the decay schedule: alpha falls from 1 to
	// alphaMin over tickCount ticks, which is when d3 considers a simulation
	// settled.
	alphaMin  = 0.001
	tickCount = 300
	// velocityDecay is friction. d3 calls 0.4 "a good default"; lower and the
	// layout oscillates, higher and it freezes before it has spread out.
	velocityDecay = 0.4
	// linkDistance is the rest length of an edge, and manyBodyStrength is the
	// repulsion every node applies to every other. Their ratio is what sets the
	// overall density of the picture.
	linkDistance     = 30.0
	manyBodyStrength = -30.0
	// centerStrength pulls the whole graph back toward the origin each tick, so
	// a disconnected component cannot drift away forever under pure repulsion.
	centerStrength = 1.0
	// theta is not used: repulsion here is exact rather than Barnes-Hut. The
	// corpus this draws has hundreds of linked notes, not hundreds of
	// thousands, and an exact sum over pairs is both simpler and free of the
	// tree-construction order that would put determinism back in question.
)

// Node is one laid-out note.
type Node struct {
	Rel    string
	Class  string
	Degree int
	X, Y   float64
}

// Edge is one drawn link, by index into the node slice.
type Edge struct{ Source, Target int }

// Layout is a settled graph.
type Layout struct {
	Nodes []Node
	Edges []Edge
	// Ticks is how many simulation steps ran, recorded so a render can say what
	// produced it rather than leaving the reader to assume it converged.
	Ticks int
}

// Input is what the layout needs: nodes in a fixed order, and edges between
// them.
type Input struct {
	Nodes []Node
	Edges []Edge
}

type body struct {
	x, y   float64
	vx, vy float64
}

// Run settles the graph and returns the laid-out result.
//
// Empty input is a valid graph rather than an error. A corpus with no links is a
// real state — an early one, or one where the backlink index has not been built
// — and the honest picture of it is an empty picture.
func Run(in Input) Layout {
	n := len(in.Nodes)
	out := Layout{Nodes: append([]Node(nil), in.Nodes...), Edges: append([]Edge(nil), in.Edges...)}
	if n == 0 {
		return out
	}

	bodies := make([]body, n)
	for i := range bodies {
		x, y := phyllotaxis(i)
		bodies[i] = body{x: x, y: y}
	}

	// d3's link strength: an edge between two well-connected nodes pulls less
	// than one between two leaves, so a hub is not dragged around by each of its
	// many neighbours in turn.
	count := make([]int, n)
	for _, e := range in.Edges {
		count[e.Source]++
		count[e.Target]++
	}
	strength := make([]float64, len(in.Edges))
	bias := make([]float64, len(in.Edges))
	for i, e := range in.Edges {
		lo := count[e.Source]
		if count[e.Target] < lo {
			lo = count[e.Target]
		}
		if lo < 1 {
			lo = 1
		}
		strength[i] = 1 / float64(lo)
		bias[i] = float64(count[e.Source]) / float64(count[e.Source]+count[e.Target])
	}

	alpha := 1.0
	decay := 1 - math.Pow(alphaMin, 1.0/tickCount)
	for tick := 0; tick < tickCount; tick++ {
		applyLinks(bodies, in.Edges, strength, bias, alpha)
		applyManyBody(bodies, alpha)
		applyCenter(bodies)
		for i := range bodies {
			bodies[i].vx *= velocityDecay
			bodies[i].vy *= velocityDecay
			bodies[i].x += bodies[i].vx
			bodies[i].y += bodies[i].vy
		}
		alpha += (0 - alpha) * decay
		out.Ticks++
	}

	for i := range out.Nodes {
		out.Nodes[i].X = bodies[i].x
		out.Nodes[i].Y = bodies[i].y
	}
	return out
}

// phyllotaxis is d3's own initial placement: a sunflower spiral by index.
//
// Deterministic, and spread out enough that the first tick's repulsion has
// something to push apart — every node at the origin would divide by zero, and
// every node on a line would settle into one.
func phyllotaxis(i int) (float64, float64) {
	const initialRadius = 10.0
	initialAngle := math.Pi * (3 - math.Sqrt(5))
	radius := initialRadius * math.Sqrt(0.5+float64(i))
	angle := float64(i) * initialAngle
	return radius * math.Cos(angle), radius * math.Sin(angle)
}

func applyLinks(b []body, edges []Edge, strength, bias []float64, alpha float64) {
	for i, e := range edges {
		s, t := &b[e.Source], &b[e.Target]
		dx := t.x + t.vx - s.x - s.vx
		dy := t.y + t.vy - s.y - s.vy
		d := math.Hypot(dx, dy)
		if d == 0 {
			// Two nodes exactly on top of each other have no direction to be
			// pushed apart along. Jiggling them randomly is d3's answer and is
			// exactly what this render cannot do, so they are nudged along a
			// fixed axis instead — same effect, same result every run.
			dx, d = 1e-6, 1e-6
		}
		l := (d - linkDistance) / d * alpha * strength[i]
		dx *= l
		dy *= l
		t.vx -= dx * bias[i]
		t.vy -= dy * bias[i]
		s.vx += dx * (1 - bias[i])
		s.vy += dy * (1 - bias[i])
	}
}

// applyManyBody is exact rather than approximated — see theta's note above.
func applyManyBody(b []body, alpha float64) {
	for i := range b {
		for j := i + 1; j < len(b); j++ {
			dx := b[j].x - b[i].x
			dy := b[j].y - b[i].y
			d2 := dx*dx + dy*dy
			if d2 == 0 {
				dx, d2 = 1e-6, 1e-12
			}
			w := manyBodyStrength * alpha / d2
			// Repulsion is negative, so this pushes them apart.
			b[i].vx += dx * w
			b[i].vy += dy * w
			b[j].vx -= dx * w
			b[j].vy -= dy * w
		}
	}
}

func applyCenter(b []body) {
	var sx, sy float64
	for i := range b {
		sx += b[i].x
		sy += b[i].y
	}
	n := float64(len(b))
	sx = sx / n * centerStrength
	sy = sy / n * centerStrength
	for i := range b {
		b[i].x -= sx
		b[i].y -= sy
	}
}

// Classes returns the class names present, sorted, so a legend lists them in a
// fixed order rather than whichever one the map handed back first.
func (l Layout) Classes() []string {
	seen := map[string]bool{}
	for _, n := range l.Nodes {
		if n.Class != "" {
			seen[n.Class] = true
		}
	}
	out := make([]string, 0, len(seen))
	for c := range seen {
		out = append(out, c)
	}
	sort.Strings(out)
	return out
}
