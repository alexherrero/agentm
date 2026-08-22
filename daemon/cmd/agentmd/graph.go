package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/graphrender"
	"github.com/alexherrero/agentm/daemon/internal/index"
)

// cmdGraph is what the dreaming stages ask about the corpus's shape.
//
// Three questions, each already answerable by the index and none of them
// answerable in Python without a second walk of fifteen thousand files:
//
//	--entities   what is mentioned enough to deserve a file of its own,
//	             and whether it already has one
//	--dangling   what the corpus expects to exist and does not
//	--backlinks  what points at one note
//
// It answers and stops there. Which entities deserve a rollup, what a stub
// should say, and where a footer belongs are all judgments the filing contract
// and the stages own — putting any of them here would be a second place the
// contract lives, which is the drift surface the rules seam exists to close.
func cmdGraph(args []string) error {
	fs := newFlagSet("graph")
	opts := bindCommon(fs)
	asJSON := fs.Bool("json", false, "emit the answer as JSON")
	entities := fs.Bool("entities", false,
		"list what the corpus mentions, most-mentioned first")
	dangling := fs.Bool("dangling", false,
		"list what the corpus links to and does not have")
	backlinks := fs.String("backlinks", "",
		"list what points at this vault-relative path")
	min := fs.Int("min", 1, "only report targets reaching this many mentions or sources")
	limit := fs.Int("limit", 50, "how many rows to print (0 for all)")
	render := fs.String("render", "",
		"draw the memory context graph as SVG to this path (- for stdout)")
	cap := fs.Int("cap", defaultGraphCap,
		"draw at most this many nodes, highest-degree first (0 for all)")
	width := fs.Int("width", 1000, "render width in pixels")
	height := fs.Int("height", 750, "render height in pixels")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if extra := fs.Args(); len(extra) > 0 {
		return fmt.Errorf("unexpected argument %q; usage: agentmd graph "+
			"[--entities | --dangling | --backlinks PATH | --render PATH] "+
			"[--min N] [--json]", extra[0])
	}

	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}
	idx, err := index.Open(cfg.IndexPath, cfg.VaultPath, cfg.MemoryRoot, cfg.DecayEnabled)
	if err != nil {
		return err
	}
	defer idx.Close()
	ctx := context.Background()

	switch {
	case *render != "":
		return renderGraph(ctx, cfg, idx, *render, *cap, *width, *height, *asJSON)

	case *backlinks != "":
		links, err := idx.Backlinks(*backlinks)
		if err != nil {
			return err
		}
		if *asJSON {
			return json.NewEncoder(os.Stdout).Encode(links)
		}
		for _, l := range links {
			// `Resolved` carries the *source* path on a backlink query — from the
			// target's point of view the interesting path is where the link came
			// from. The overload is documented at Backlinks' own call site; it is
			// repeated here because reading `Resolved` and getting a source is
			// the kind of thing that looks like a bug to whoever sees it next.
			fmt.Println(l.Resolved)
		}
		return nil

	case *entities:
		rows, err := idx.EntityMentions(ctx, *min)
		if err != nil {
			return err
		}
		if *limit > 0 && len(rows) > *limit {
			rows = rows[:*limit]
		}
		if *asJSON {
			return json.NewEncoder(os.Stdout).Encode(rows)
		}
		for _, e := range rows {
			mark := "no file"
			if e.HasFile() {
				mark = e.File
			}
			fmt.Printf("  %-6d %-40s %s\n", e.Mentions, e.URI, mark)
		}
		return nil

	case *dangling:
		rows, err := idx.DanglingTargets(ctx, *min)
		if err != nil {
			return err
		}
		if *limit > 0 && len(rows) > *limit {
			rows = rows[:*limit]
		}
		if *asJSON {
			return json.NewEncoder(os.Stdout).Encode(rows)
		}
		for _, d := range rows {
			fmt.Printf("  %-6d %s\n", len(d.Sources), d.Target)
			for _, s := range d.Sources {
				fmt.Printf("           %s\n", s)
			}
		}
		return nil
	}

	return fmt.Errorf("agentmd graph needs one of --entities, --dangling or " +
		"--backlinks PATH; it answers questions about the corpus's shape and " +
		"has no useful default")
}

// defaultGraphCap bounds an exact-repulsion layout.
//
// The corpus this was measured against has roughly nine hundred linked notes out
// of sixteen thousand documents, so the cap does not bite today and is not
// pretending to be tuned. It is a backstop: repulsion is summed over every pair,
// and a corpus ten times larger would take a hundred times as long without
// anybody having chosen that.
const defaultGraphCap = 3000

// renderGraph draws the memory context graph.
//
// Zero model calls, by construction rather than by policy — nothing in this path
// can reach one. The class comes from each note's own frontmatter read through
// the contract's routing, because the corpus is not filed by class on disk yet:
// `memory/` is date-sharded, filing is what would create the class folders, and
// enrichment is off. Notes whose class nothing can state are drawn as unfiled,
// which is the honest picture of where this corpus actually is.
func renderGraph(ctx context.Context, cfg *config.Config, idx *index.Index,
	dest string, cap, width, height int, asJSON bool) error {
	g, err := idx.LinkGraph(ctx, cap)
	if err != nil {
		return err
	}

	in := graphrender.Input{Nodes: make([]graphrender.Node, len(g.Nodes))}
	at := make(map[string]int, len(g.Nodes))
	for i, n := range g.Nodes {
		in.Nodes[i] = graphrender.Node{
			Rel:    n.Rel,
			Degree: n.Degree,
			Class:  classOf(cfg, n.Rel),
		}
		at[n.Rel] = i
	}
	for _, e := range g.Edges {
		si, sok := at[e.Source]
		ti, tok := at[e.Target]
		if !sok || !tok {
			// An edge whose endpoint the cap removed. Skipped rather than
			// drawn to a node that is not in the picture.
			continue
		}
		in.Edges = append(in.Edges, graphrender.Edge{Source: si, Target: ti})
	}

	layout := graphrender.Run(in)
	svg := layout.SVG(width, height)

	if dest == "-" {
		fmt.Print(svg)
	} else if err := os.WriteFile(dest, []byte(svg), 0o644); err != nil {
		return fmt.Errorf("writing the graph to %s: %w", dest, err)
	}

	report := struct {
		Path    string `json:"path"`
		Nodes   int    `json:"nodes"`
		Edges   int    `json:"edges"`
		Dropped int    `json:"dropped,omitempty"`
		Cap     int    `json:"cap,omitempty"`
		Ticks   int    `json:"ticks"`
		Calls   int    `json:"model_calls"`
	}{dest, len(layout.Nodes), len(layout.Edges), g.Dropped, g.Cap, layout.Ticks, 0}

	if asJSON {
		return json.NewEncoder(os.Stdout).Encode(report)
	}
	if dest != "-" {
		fmt.Printf("drew %d node(s) and %d edge(s) to %s after %d ticks\n",
			report.Nodes, report.Edges, dest, report.Ticks)
	}
	// Never silently. A picture of the top three thousand of a larger corpus
	// looks exactly like a picture of the whole corpus.
	if g.Dropped > 0 {
		fmt.Printf("  %d node(s) below the %d-node cap are not drawn; raise "+
			"--cap or pass --cap 0 to draw all of them\n", g.Dropped, g.Cap)
	}
	return nil
}

// classOf reads a note's class from its own frontmatter through the contract.
//
// Read per render rather than cached in the index on purpose. A class derived
// from the contract goes stale the moment somebody edits the contract, and a
// cached wrong class is worse than no class because it reads as filed. Nine
// hundred file reads is milliseconds.
func classOf(cfg *config.Config, rel string) string {
	loaded, err := cfg.Rules.Get()
	if err != nil || loaded == nil {
		return graphrender.UnfiledClass
	}
	raw, err := os.ReadFile(filepath.Join(cfg.VaultPath, filepath.FromSlash(rel)))
	if err != nil {
		return graphrender.UnfiledClass
	}
	class, ok := loaded.ClassFor(enrich.FrontmatterValue(string(raw), "type"))
	if !ok {
		return graphrender.UnfiledClass
	}
	return class
}
