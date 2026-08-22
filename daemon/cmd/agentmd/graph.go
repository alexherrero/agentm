package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"

	"github.com/alexherrero/agentm/daemon/internal/config"
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
	if err := fs.Parse(args); err != nil {
		return err
	}
	if extra := fs.Args(); len(extra) > 0 {
		return fmt.Errorf("unexpected argument %q; usage: agentmd graph "+
			"[--entities | --dangling | --backlinks PATH] [--min N] [--json]",
			extra[0])
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
