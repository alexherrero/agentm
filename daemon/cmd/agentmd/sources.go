package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/sources"
)

// cmdSources is how ingest asks whether it has already read something.
//
// The same seam `agentmd rules`, `agentmd ledger` and `agentmd queue` opened.
// Ingest lives in the Python half — it is the code that knows how to talk to a
// mail store and a transcript directory — and the watermark it consults lives in
// the daemon's database. One implementation, asked over a command, rather than
// two readers of one table.
//
// The question it is built to answer fast is the cheap one: `--seen`, which
// exits 0 when the unit has already been processed at this content and version.
// A shell loop over a mailbox can ask it per message before deciding to spend
// anything.
func cmdSources(args []string) error {
	fs := newFlagSet("sources")
	opts := bindCommon(fs)
	asJSON := fs.Bool("json", false, "emit the answer as JSON")
	id := fs.String("id", "", "the namespaced source identity to act on")
	namespace := fs.String("namespace", "", "list only this namespace")
	limit := fs.Int("limit", 20, "how many rows to print (0 for all)")
	seen := fs.Bool("seen", false,
		"exit 0 if --id at --hash and --version has already been processed")
	hash := fs.String("hash", "", "content hash, for an immutable unit")
	hashFile := fs.String("hash-file", "",
		"compute the content hash of this file instead of passing --hash")
	version := fs.String("version", "", "the pass version")
	register := fs.Bool("register", false, "watermark --id as processed")
	kind := fs.String("kind", "", "immutable | growing")
	cursor := fs.String("cursor", "", "last offset or message consumed, for a growing unit")
	yield := fs.Int("yield", 0, "how many memories the pass produced from it")
	showCursor := fs.Bool("cursor-of", false, "print where a growing source was last consumed to")
	memories := fs.Bool("memories", false, "list the memories --id produced")
	zeroYield := fs.Bool("zero-yield", false, "list sources that produced nothing")
	forget := fs.Bool("forget", false, "drop --id's watermark so it is mined again")
	supersede := fs.Bool("supersede", false,
		"mark every memory --id already produced as replaced, before a re-ingest "+
			"writes the new ones")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if extra := fs.Args(); len(extra) > 0 {
		return fmt.Errorf("unexpected argument %q; usage: agentmd sources "+
			"[--id ID] [--seen --hash H --version V] [--register --kind K ...] "+
			"[--memories] [--zero-yield] [--json]", extra[0])
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

	reg, err := sources.Open(idx.DB())
	if err != nil {
		return err
	}
	ctx := context.Background()

	// The identity is parsed once, here, so every path below works on a
	// canonical form. A caller passing the same article with and without a
	// tracking parameter must reach the same row.
	var parsed sources.ID
	if *id != "" {
		if parsed, err = sources.ParseID(*id); err != nil {
			return err
		}
	}
	needsID := *seen || *register || *showCursor || *memories || *forget || *supersede
	if needsID && *id == "" {
		return fmt.Errorf("that needs --id: a source identity is what the " +
			"registry is keyed on")
	}

	if *hashFile != "" {
		blob, err := os.ReadFile(*hashFile)
		if err != nil {
			return err
		}
		*hash = sources.HashContent(string(blob))
	}

	switch {
	case *seen:
		ok, err := reg.Seen(ctx, parsed, *hash, *version)
		if err != nil {
			return err
		}
		if *asJSON {
			return json.NewEncoder(os.Stdout).Encode(map[string]any{
				"id": parsed.String(), "seen": ok, "hash": *hash, "version": *version,
			})
		}
		if ok {
			fmt.Printf("%s is already processed at this content and version — "+
				"skip it\n", parsed)
			return nil
		}
		// A non-zero exit so a shell loop can branch on it without parsing
		// anything. Two, not one, so "not seen" is distinguishable from the
		// registry having failed to answer.
		fmt.Printf("%s has not been processed at this content and version\n", parsed)
		return &exitError{code: 2, quiet: true,
			err: fmt.Errorf("%s is not watermarked at this content and version", parsed)}

	case *register:
		u := sources.Unit{
			ID: parsed, Kind: sources.Kind(*kind), Hash: *hash,
			Cursor: *cursor, Version: *version, Yield: *yield,
		}
		if u.Kind == sources.Growing {
			// Advance rather than Register, so a sweep's yield adds to what
			// earlier sweeps of the same log already produced.
			if err := reg.Advance(ctx, parsed, *cursor, *version, *yield); err != nil {
				return err
			}
		} else if err := reg.Register(ctx, u); err != nil {
			return err
		}
		fmt.Printf("watermarked %s at %s\n", parsed, *version)
		return nil

	case *showCursor:
		pos, err := reg.Cursor(ctx, parsed)
		if err != nil {
			return err
		}
		if *asJSON {
			return json.NewEncoder(os.Stdout).Encode(
				map[string]any{"id": parsed.String(), "cursor": pos})
		}
		fmt.Println(pos)
		return nil

	case *memories:
		rels, err := idx.BySource(ctx, parsed.String())
		if err != nil {
			return err
		}
		if *asJSON {
			return json.NewEncoder(os.Stdout).Encode(rels)
		}
		for _, rel := range rels {
			fmt.Println(rel)
		}
		return nil

	case *supersede:
		rep, err := sources.Supersede(ctx, parsed, *version, time.Now(),
			idx.BySource, sourceRewriter(cfg.VaultPath))
		if err != nil {
			return err
		}
		if *asJSON {
			return json.NewEncoder(os.Stdout).Encode(rep)
		}
		if len(rep.Superseded) == 0 {
			fmt.Printf("%s has produced nothing yet — the re-ingest has nothing to "+
				"replace\n", parsed)
			return nil
		}
		fmt.Printf("superseded %d memor(ies) from %s; the re-ingest may write its "+
			"own now\n", len(rep.Superseded), parsed)
		for _, rel := range rep.Superseded {
			fmt.Println("  ", rel)
		}
		// Re-indexed so the new statuses are visible to the next query. Without
		// it the supersession is on disk and invisible until the next reconcile.
		for _, rel := range rep.Superseded {
			if err := idx.IndexFile(rel); err != nil {
				fmt.Fprintf(os.Stderr, "re-indexing %s: %v\n", rel, err)
			}
		}
		return nil

	case *forget:
		if err := reg.Forget(ctx, parsed); err != nil {
			return err
		}
		fmt.Printf("forgot %s — it will be read again\n", parsed)
		return nil

	case *zeroYield:
		recs, err := reg.ZeroYield(ctx)
		if err != nil {
			return err
		}
		if *asJSON {
			return json.NewEncoder(os.Stdout).Encode(recs)
		}
		for _, rec := range recs {
			fmt.Printf("%-12s %s\n", rec.Kind, rec.ID)
		}
		if len(recs) > 0 {
			fmt.Printf("%d source(s) were read and produced nothing. A corpus scan "+
				"cannot recover these — no memory carries their id — which is why "+
				"they belong in the committed file.\n", len(recs))
		}
		return nil
	}

	stats, err := reg.Count(ctx)
	if err != nil {
		return err
	}
	recs, err := reg.All(ctx, sources.Namespace(*namespace), *limit)
	if err != nil {
		return err
	}
	if *asJSON {
		return json.NewEncoder(os.Stdout).Encode(map[string]any{
			"stats": stats, "sources": recs,
		})
	}
	if stats.Total == 0 {
		fmt.Println("the source registry is empty — nothing has been watermarked yet")
		return nil
	}
	fmt.Printf("%d source(s) · %d immutable · %d growing · %d memories · %d yielded nothing\n",
		stats.Total, stats.ByKind[sources.Immutable], stats.ByKind[sources.Growing],
		stats.Memories, stats.ZeroYield)
	for _, rec := range recs {
		fmt.Printf("  %-10s %s\n", rec.Kind, rec.ID)
		mark := rec.Hash
		if rec.Kind == sources.Growing {
			mark = "at " + rec.Cursor
		} else if len(mark) > 12 {
			mark = mark[:12]
		}
		fmt.Printf("             %s · %s · %d memories · last seen %s\n",
			mark, rec.Version, rec.Yield,
			rec.LastSeen.Format(time.RFC3339))
	}
	if *limit > 0 && stats.Total > len(recs) {
		fmt.Printf("  … and %d more (pass --limit 0 for all)\n", stats.Total-len(recs))
	}
	return nil
}

// sourceRewriter writes a note back through the vault, for supersession.
//
// Deliberately a plain write rather than a route through the enrichment
// Applier. Superseding does not change a note's class, its slug or its body — it
// records that something replaced it — and running it through the machinery that
// exists to move and rename notes would be borrowing risk for nothing.
func sourceRewriter(vault string) sources.Rewriter {
	return func(_ context.Context, rel string, rewrite func(string) string) error {
		abs := filepath.Join(vault, filepath.FromSlash(rel))
		blob, err := os.ReadFile(abs)
		if err != nil {
			return err
		}
		next := rewrite(string(blob))
		if next == string(blob) {
			return nil
		}
		return os.WriteFile(abs, []byte(next), 0o644)
	}
}
