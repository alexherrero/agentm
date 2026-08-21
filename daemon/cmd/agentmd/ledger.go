package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/ledger"
)

// cmdLedger is how everything that is not this binary asks what dreaming has
// already done.
//
// The same seam `agentmd rules` opened: one implementation, in Go, next to the
// database that holds it, and every other caller asks it. The Python dreaming
// stages read `agentmd ledger --pending --json` once per run rather than
// carrying a second reader of a table they do not own.
//
// It deliberately answers without a running daemon. A stage that has to start a
// server to find out whether it has work would be a stage nobody could run by
// hand, and the first thing anyone does with a queue is look at it.
func cmdLedger(args []string) error {
	fs := newFlagSet("ledger")
	opts := bindCommon(fs)
	asJSON := fs.Bool("json", false, "emit the answer as JSON")
	stage := fs.String("stage", ledger.StageEnrich, "which stage to report on")
	pending := fs.Bool("pending", false,
		"list what the stage still owes, oldest first")
	limit := fs.Int("limit", 20, "how many pending items to print (0 for all)")
	rebuild := fs.Bool("rebuild", false,
		"discard this stage's rows and recover them from the corpus")
	forget := fs.String("forget", "",
		"drop one target's row so the stage runs over it again")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if extra := fs.Args(); len(extra) > 0 {
		return fmt.Errorf("unexpected argument %q; usage: agentmd ledger "+
			"[--stage NAME] [--pending] [--rebuild] [--forget TARGET] [--json]",
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

	led, err := ledger.Open(idx.DB())
	if err != nil {
		return err
	}
	ctx := context.Background()

	switch {
	case *forget != "":
		if err := led.Forget(ctx, *stage, *forget); err != nil {
			return err
		}
		fmt.Printf("forgot %s/%s — the stage will run over it again\n", *stage, *forget)
		return nil

	case *rebuild:
		scan, err := rebuilderFor(*stage, cfg)
		if err != nil {
			return err
		}
		rep, err := led.Rebuild(ctx, *stage, scan)
		if err != nil {
			return err
		}
		if *asJSON {
			return json.NewEncoder(os.Stdout).Encode(rep)
		}
		fmt.Printf("rebuilt %s: dropped %d row(s), recovered %d from the corpus in %s\n",
			rep.Stage, rep.Dropped, rep.Recovered, rep.Elapsed.Round(time.Millisecond))
		if rep.Recovered < int(rep.Dropped) {
			fmt.Printf("  %d row(s) had no durable stamp to recover from; that work "+
				"will be done again\n", int(rep.Dropped)-rep.Recovered)
		}
		return nil

	case *pending:
		rep, err := pendingFor(ctx, *stage, cfg, idx, led)
		if err != nil {
			return err
		}
		if *asJSON {
			return json.NewEncoder(os.Stdout).Encode(rep)
		}
		printPending(rep, *limit)
		return nil
	}

	stats, err := led.Stages(ctx)
	if err != nil {
		return err
	}
	if *asJSON {
		return json.NewEncoder(os.Stdout).Encode(stats)
	}
	if len(stats) == 0 {
		fmt.Println("the ledger is empty — no stage has recorded anything yet")
		return nil
	}
	for _, s := range stats {
		fmt.Printf("%-10s %s\n", s.Stage, s.Version)
		fmt.Printf("  done %d · skipped %d · failed %d\n", s.Done, s.Skipped, s.Failed)
		if s.Oldest != "" {
			fmt.Printf("  written between %s and %s\n", s.Oldest, s.Newest)
		}
	}
	return nil
}

func printPending(rep ledger.Report, limit int) {
	fmt.Printf("%s at %s\n", rep.Stage, rep.Version)
	fmt.Printf("  eligible %d · current %d · pending %d · coverage %.1f%%\n",
		rep.Eligible, rep.Current, len(rep.Pending), rep.Coverage()*100)
	if age := rep.OldestPending(time.Now()); age > 0 {
		fmt.Printf("  oldest pending item stamped %s ago\n", age.Round(time.Minute))
	}
	for _, r := range []ledger.Reason{ledger.ReasonNever, ledger.ReasonStale,
		ledger.ReasonChanged, ledger.ReasonRetry, ledger.ReasonSkipped} {
		if n := rep.Counts[r]; n > 0 {
			fmt.Printf("    %-8s %d\n", r, n)
		}
	}
	shown := rep.Pending
	if limit > 0 && len(shown) > limit {
		shown = shown[:limit]
	}
	for _, it := range shown {
		fmt.Printf("  %-8s %s", it.Reason, it.Target)
		if it.Reason == ledger.ReasonStale && it.Version != "" {
			fmt.Printf(" (at %s)", it.Version)
		}
		if it.Detail != "" {
			fmt.Printf(" — %s", it.Detail)
		}
		fmt.Println()
	}
	// The truncation says so. A list that stopped at twenty and did not mention
	// it reads as a queue of twenty, which is the same silent partiality the
	// cursor rule exists to prevent.
	if len(shown) < len(rep.Pending) {
		fmt.Printf("  … and %d more (pass --limit 0 for all)\n",
			len(rep.Pending)-len(shown))
	}
}

// --- the enrichment stage's glue --------------------------------------------
//
// Everything below knows both halves — what a ledger row is, and what
// enrichment's key and stamps are. It lives here rather than in either package
// because neither should have to import the other: the ledger holds opaque keys
// so any stage can use it, and enrichment takes its `Seen` as a function so it
// never learns where the answer comes from.

// enrichFingerprint is the idempotency gate wired to the ledger.
//
// The same construction the enrichment command uses, in one place, because a
// gate configured differently in two call sites is a gate that answers
// differently in two call sites — and the whole claim is that it answers zero.
func enrichFingerprint(cfg *config.Config, led *ledger.Ledger) *enrich.Fingerprint {
	fp := &enrich.Fingerprint{
		Version:   enrich.PassVersion,
		RulesHash: currentRulesHash(cfg),
	}
	if led == nil {
		return fp
	}
	fp.Seen = func(rel, key string) bool {
		seen, err := led.Seen(context.Background(), ledger.StageEnrich, rel, key)
		if err != nil {
			// A ledger that cannot be read answers "not seen", which costs a
			// call that might not have been needed. The other direction would
			// skip work that was never done and report it as finished, and a
			// wrong "finished" is the one failure this table exists to prevent.
			fmt.Fprintf(os.Stderr, "ledger unavailable for %s: %v\n", rel, err)
			return false
		}
		return seen
	}
	return fp
}

// currentRulesHash reads the filing contract's hash, or says it could not.
//
// "unresolved" rather than an empty string, and it is a real value that goes
// into keys: a run that could not read the contract must not produce the same
// key as one that read it and found nothing, because those are different states
// and only one of them means the work is comparable.
func currentRulesHash(cfg *config.Config) string {
	if loaded, err := cfg.Rules.Get(); err == nil {
		return loaded.Hash
	}
	return "unresolved"
}

// enrichStamp is the durable record a write leaves in the note.
func enrichStamp(cfg *config.Config, at time.Time) enrich.Stamp {
	return enrich.Stamp{
		Version:   enrich.PassVersion,
		RulesHash: currentRulesHash(cfg),
		At:        at.UTC(),
	}
}

// rebuilderFor returns the scan that recovers one stage's rows from the corpus.
//
// A stage with no rebuilder is an error rather than a silent no-op. Rebuilding
// wipes first, so a stage that could not be recovered would be quietly emptied
// by the very command meant to restore it.
func rebuilderFor(stage string, cfg *config.Config) (ledger.Scanner, error) {
	switch stage {
	case ledger.StageEnrich:
		return enrichRebuilder(cfg.VaultPath), nil
	}
	return nil, fmt.Errorf("no rebuilder for stage %q — its rows are not "+
		"recoverable from the corpus, so wiping them would lose the record of "+
		"work that did happen and the work would simply be done again", stage)
}

// enrichRebuilder reads every note's own stamps back out of the vault.
//
// The key is computed under the version the *note* claims, not the version
// running now. A note enriched by an older prompt should come back as a row at
// that older version — which is what makes it show up as stale rather than as
// current, and re-enter the queue on its own.
func enrichRebuilder(vault string) ledger.Scanner {
	return func(ctx context.Context, emit func(ledger.Stamped) error) error {
		return filepath.WalkDir(vault, func(abs string, d fs.DirEntry, err error) error {
			if err != nil {
				// An unreadable subtree on a cloud mount is transient and is not
				// worth failing a whole rebuild over. It costs the rows under it,
				// which come back as never-attempted and are simply redone.
				if d != nil && d.IsDir() {
					return fs.SkipDir
				}
				return nil
			}
			if ctx.Err() != nil {
				return ctx.Err()
			}
			name := d.Name()
			if d.IsDir() {
				if abs != vault && strings.HasPrefix(name, ".") {
					return fs.SkipDir
				}
				return nil
			}
			if !strings.HasSuffix(name, ".md") || strings.HasPrefix(name, ".") {
				return nil
			}
			raw, readErr := os.ReadFile(abs)
			if readErr != nil {
				return nil
			}
			body := string(raw)
			version := enrich.FrontmatterValue(body, "enriched_by")
			if version == "" {
				// Never enriched, or enriched before the stamp existed. Either
				// way there is nothing here to recover, and inventing a row
				// would claim work that may never have happened.
				return nil
			}
			rel, relErr := filepath.Rel(vault, abs)
			if relErr != nil {
				return nil
			}
			rulesHash := enrich.FrontmatterValue(body, "rules_hash")
			fp := &enrich.Fingerprint{Version: version, RulesHash: rulesHash}
			s := ledger.Stamped{
				Target:    filepath.ToSlash(rel),
				Version:   version,
				RulesHash: rulesHash,
				OutputKey: fp.Key(body),
			}
			if at, perr := time.Parse(enrich.StampFormat,
				enrich.FrontmatterValue(body, "enriched_at")); perr == nil {
				s.At = at.UTC()
			}
			return emit(s)
		})
	}
}

// recordEnrich writes one ledger row, reporting a failure rather than raising it.
//
// A ledger write that fails must not fail an enrichment that already landed. The
// note is on disk and in the journal; losing its row costs a re-run of one note,
// while aborting here would leave a written note reported as a failure and send
// the whole batch's error count somewhere it does not belong.
func recordEnrich(ctx context.Context, led *ledger.Ledger, e ledger.Entry) {
	if led == nil {
		return
	}
	if err := led.Record(ctx, e); err != nil {
		fmt.Fprintf(os.Stderr, "ledger: %v\n", err)
	}
}

// pendingFor asks the ledger what a stage still owes over its eligible
// population.
//
// The population comes from the index, because "eligible" is the stage's
// business: for enrichment it is the unfiled queue, which is exactly what the
// batch drain walks. Handing the ledger a different population than the drain
// uses would produce a coverage number about a set nothing works on.
func pendingFor(ctx context.Context, stage string, cfg *config.Config,
	idx *index.Index, led *ledger.Ledger) (ledger.Report, error) {
	if stage != ledger.StageEnrich {
		return ledger.Report{}, fmt.Errorf("no eligible population defined for "+
			"stage %q; a coverage number over a population nobody can name is a "+
			"number nobody can act on", stage)
	}

	fp := enrichFingerprint(cfg, nil)
	var targets []ledger.Target
	cursor := ""
	for {
		page, err := idx.UnfiledPage(ctx, cursor, 500)
		if err != nil {
			return ledger.Report{}, err
		}
		if len(page) == 0 {
			break
		}
		for _, rel := range page {
			cursor = rel
			raw, err := os.ReadFile(filepath.Join(cfg.VaultPath, filepath.FromSlash(rel)))
			if err != nil {
				// In the index and not on disk: a drifted index, which the
				// reconcile pass fixes. Counting it as eligible would put a
				// permanent pending item in a queue nothing can drain.
				continue
			}
			targets = append(targets, ledger.Target{Rel: rel, Key: fp.Key(string(raw))})
		}
		if len(page) < 500 {
			break
		}
	}
	return led.Pending(ctx, stage, enrich.PassVersion, targets)
}
