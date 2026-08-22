package index

import (
	"context"
	"fmt"
	"sort"
)

// The whole link graph, as one query.
//
// `Backlinks` and `LinksFrom` answer about one note, which is what a lookup
// needs and what the review surfaces use. A render needs every edge at once, and
// asking per note would be one query per node over a corpus of sixteen thousand.

// GraphNode is one note in the link graph.
type GraphNode struct {
	// Rel is the vault-relative path, which is the note's identity.
	Rel string `json:"rel"`
	// Degree counts edges touching this node in either direction. It is what
	// sizes a hub in the render, and it is computed here rather than by the
	// renderer so that the number in a JSON dump and the number a circle is
	// drawn from cannot disagree.
	Degree int `json:"degree"`
}

// GraphEdge is one resolved link.
//
// Unresolved links are not edges. A wikilink pointing at a note nobody has
// written yet is a real thing about the corpus — it is what the stub-synthesis
// stage exists for — but it has no second endpoint, and drawing it would mean
// inventing a node for a file that does not exist.
type GraphEdge struct {
	Source string `json:"source"`
	Target string `json:"target"`
}

// LinkGraph is the resolved link structure of the corpus.
type LinkGraph struct {
	Nodes []GraphNode `json:"nodes"`
	Edges []GraphEdge `json:"edges"`
	// Dropped is how many nodes the cap left out, and Cap is the cap that did
	// it. Reported rather than silently applied: a render that quietly showed
	// the top thousand of four thousand nodes would look like the whole corpus.
	Dropped int `json:"dropped,omitempty"`
	Cap     int `json:"cap,omitempty"`
}

// LinkGraph returns every resolved link, with the nodes they touch.
//
// Sorted throughout — nodes by path, edges by source then target — because the
// render must be byte-identical across runs and SQLite makes no ordering promise
// without an ORDER BY. Sorting here rather than in the renderer means every
// consumer gets the same order, including a JSON dump somebody diffs.
//
// `cap` bounds the node count for a corpus large enough that an exact-repulsion
// layout stops being cheap. Zero means no cap. When it bites, the nodes kept are
// the highest-degree ones — a graph is mostly about its hubs — and the number
// dropped is reported rather than swallowed.
func (x *Index) LinkGraph(ctx context.Context, cap int) (LinkGraph, error) {
	x.mu.Lock()
	defer x.mu.Unlock()

	rows, err := x.db.QueryContext(ctx, `
		SELECT d.path, l.resolved
		FROM links l
		JOIN docmeta d ON d.id = l.source_id
		WHERE l.resolved != '' AND l.resolved != d.path
		ORDER BY d.path, l.resolved`)
	if err != nil {
		return LinkGraph{}, fmt.Errorf("reading the link graph: %w", err)
	}
	defer rows.Close()

	degree := map[string]int{}
	seen := map[GraphEdge]bool{}
	var edges []GraphEdge
	for rows.Next() {
		var e GraphEdge
		if err := rows.Scan(&e.Source, &e.Target); err != nil {
			return LinkGraph{}, err
		}
		// Two notes can link to each other several times over — a paragraph
		// that references the same page twice is ordinary writing. The graph
		// wants the relationship once, or a repeated reference would draw a
		// thick line and inflate both degrees for saying the same thing twice.
		if seen[e] {
			continue
		}
		seen[e] = true
		edges = append(edges, e)
		degree[e.Source]++
		degree[e.Target]++
	}
	if err := rows.Err(); err != nil {
		return LinkGraph{}, err
	}

	g := LinkGraph{Edges: edges}
	for rel, d := range degree {
		g.Nodes = append(g.Nodes, GraphNode{Rel: rel, Degree: d})
	}
	sort.Slice(g.Nodes, func(i, j int) bool { return g.Nodes[i].Rel < g.Nodes[j].Rel })

	if cap > 0 && len(g.Nodes) > cap {
		g = applyCap(g, cap)
	}
	return g, nil
}

// applyCap keeps the highest-degree nodes and the edges between them.
//
// Ties break on path so that two nodes of equal degree at the cap boundary
// resolve the same way every run. Without it the render would flicker between
// two equally valid graphs and stop being comparable to yesterday's.
func applyCap(g LinkGraph, cap int) LinkGraph {
	ranked := make([]GraphNode, len(g.Nodes))
	copy(ranked, g.Nodes)
	sort.SliceStable(ranked, func(i, j int) bool {
		if ranked[i].Degree != ranked[j].Degree {
			return ranked[i].Degree > ranked[j].Degree
		}
		return ranked[i].Rel < ranked[j].Rel
	})

	keep := map[string]bool{}
	for _, n := range ranked[:cap] {
		keep[n.Rel] = true
	}

	out := LinkGraph{Dropped: len(g.Nodes) - cap, Cap: cap}
	for _, e := range g.Edges {
		if keep[e.Source] && keep[e.Target] {
			out.Edges = append(out.Edges, e)
		}
	}
	// Degrees are recomputed over the kept subgraph rather than carried over.
	// A node whose neighbours were all dropped is not a hub in the graph being
	// drawn, and sizing it as one would be reporting a number about a different
	// graph than the picture.
	degree := map[string]int{}
	for _, e := range out.Edges {
		degree[e.Source]++
		degree[e.Target]++
	}
	for rel := range keep {
		out.Nodes = append(out.Nodes, GraphNode{Rel: rel, Degree: degree[rel]})
	}
	sort.Slice(out.Nodes, func(i, j int) bool { return out.Nodes[i].Rel < out.Nodes[j].Rel })
	return out
}
