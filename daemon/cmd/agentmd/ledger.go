package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/ledger"
	"github.com/alexherrero/agentm/daemon/internal/people"
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
	idx, err := index.OpenWithSidecar(cfg.IndexPath, cfg.VaultPath, cfg.MemoryRoot, cfg.EngineStateDir, cfg.DecayEnabled)
	if err != nil {
		return err
	}
	defer idx.Close()

	ctx := context.Background()
	led, err := openLedger(ctx, cfg, idx, os.Stderr)
	if err != nil {
		return err
	}
	defer led.Close()

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
		// The stamps a rebuild reads carry the hash they were written with,
		// and older notes carry the whole contract's: translate them as the
		// first open of a ledger file does (task 181 step 3).
		if err := led.Unmark(ctx, judgmentCutover); err != nil {
			return err
		}
		if err := cutoverJudgmentHash(ctx, cfg, led, os.Stderr); err != nil {
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
	// The same set by cause, in the order the night drains it (task 181 step 4).
	fmt.Printf("  by cause: %s\n", formatCauses(rep.Causes))
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
		RulesHash: currentJudgmentHash(cfg),
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

// currentJudgmentHash reads the part of the filing contract an enrichment
// judgment reads (rules.Rules.JudgmentHash), or says it could not.
//
// The judgment hash, not the whole contract's: it is the contract half of the
// fingerprint key, the ledger's version and the stamp, so an edit to anything a
// judgment does not read — the dampened spaces, the wall, a threshold — leaves
// every judgment standing (task 181, #784). A contract that will not parse
// still halts filing; that is the whole contract's business.
//
// "unresolved" rather than an empty string, and it is a real value that goes
// into keys: a run that could not read the contract must not produce the same
// key as one that read it and found nothing, because those are different states
// and only one of them means the work is comparable.
func currentJudgmentHash(cfg *config.Config) string {
	if loaded, err := cfg.Rules.Get(); err == nil {
		return loaded.JudgmentHash
	}
	return "unresolved"
}

// enrichStamp is the durable record a write leaves in the note.
//
// The confidence floor rides along rather than being compiled in: the design
// wants a threshold moved by editing the contract, and the stamp is already
// the place a judgment records which contract it was made under.
func enrichStamp(cfg *config.Config, at time.Time) enrich.Stamp {
	floor := 0.0
	if loaded, err := cfg.Rules.Get(); err == nil {
		if v, ok := loaded.Threshold(enrich.ConfidenceFloorThreshold); ok {
			floor = v
		}
	}
	return enrich.Stamp{
		Version:         enrich.PassVersion,
		RulesHash:       currentJudgmentHash(cfg),
		ConfidenceFloor: floor,
		At:              at.UTC(),
		People:          peopleTable(cfg),
	}
}

// peopleTable is the operator's alias and deny table (task 179), read fresh
// for each write: it is one small file the operator edits in Obsidian, and a
// deny line added mid-night should hold from the next note on. A table that
// does not parse is reported once and read as empty — the names a pass
// returns are still held to the note's own text, so an empty table grounds
// every name and merges none.
func peopleTable(cfg *config.Config) people.Table {
	t, err := people.Load(cfg.VaultPath)
	if err != nil {
		peopleTableWarn.Do(func() { fmt.Fprintln(os.Stderr, "agentmd:", err) })
	}
	return t
}

var peopleTableWarn sync.Once

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

// ledgerPath is the enrichment ledger's file: in the engine state directory,
// with the night's other records, where an index schema bump cannot reach it.
func ledgerPath(cfg *config.Config) string {
	return filepath.Join(enrichStateDir(cfg), ledger.FileName)
}

// openLedger opens the ledger's file, making it the first time.
//
// The first time is the move out of the index (#783): every row the index still
// holds is carried across as it stands, and the index's table is then dropped,
// so no second copy goes stale beside the first. A file that is missing with
// nothing to carry — a new machine, or a ledger file that was lost — is rebuilt
// from the stamps the notes carry, and says so, because a rebuilt ledger has
// lost every input key and the night after it re-judges more than it would
// have.
//
// Made under a name of its own and linked into place, so a run killed half way
// leaves no file a later run would trust as complete, and two runs making it
// at once cannot replace one another's work: the second finds the first's file
// and opens that.
func openLedger(ctx context.Context, cfg *config.Config, idx *index.Index,
	log io.Writer) (*ledger.Ledger, error) {
	led, err := openLedgerFile(ctx, cfg, idx, log)
	if err != nil {
		return nil, err
	}
	// Once per file: a ledger carried out of the index, or rebuilt from the
	// stamps, holds rows keyed to the whole contract's hash (task 181 step 3).
	if err := cutoverJudgmentHash(ctx, cfg, led, log); err != nil {
		led.Close()
		return nil, err
	}
	return led, nil
}

// openLedgerFile is openLedger's first half: the file, made if it is missing.
func openLedgerFile(ctx context.Context, cfg *config.Config, idx *index.Index,
	log io.Writer) (*ledger.Ledger, error) {
	path := ledgerPath(cfg)
	if _, err := os.Stat(path); err == nil {
		return ledger.OpenFile(path)
	} else if !errors.Is(err, fs.ErrNotExist) {
		return nil, fmt.Errorf("ledger: %w", err)
	}

	tmp := fmt.Sprintf("%s.new-%d", path, os.Getpid())
	led, err := ledger.OpenFile(tmp)
	if err != nil {
		return nil, err
	}
	discard := func() {
		led.Close()
		os.Remove(tmp)
		os.Remove(tmp + "-journal")
	}
	carried, err := led.CarryFrom(ctx, idx.DB())
	if err != nil {
		discard()
		return nil, err
	}
	recovered := 0
	if carried == 0 {
		rep, err := led.Rebuild(ctx, ledger.StageEnrich, enrichRebuilder(cfg.VaultPath))
		if err != nil {
			discard()
			return nil, err
		}
		recovered = rep.Recovered
	}
	if err := led.Close(); err != nil {
		discard()
		return nil, err
	}
	if err := os.Link(tmp, path); err != nil {
		os.Remove(tmp)
		if errors.Is(err, fs.ErrExist) {
			return ledger.OpenFile(path)
		}
		return nil, fmt.Errorf("ledger: putting %s in place: %w", path, err)
	}
	os.Remove(tmp)
	if carried > 0 {
		fmt.Fprintf(log, "ledger: moved %d row(s) out of the index into %s\n", carried, path)
		if err := ledger.DropFrom(ctx, idx.DB()); err != nil {
			// Reported, not fatal: the file is in place and complete. The stale
			// table only matters if the file is lost, and then it is carried
			// back rather than rebuilt from the notes — still keyed by content,
			// so never wrong about what it claims, only behind.
			fmt.Fprintf(log, "ledger: the index still holds its old table: %v\n", err)
		}
	} else {
		fmt.Fprintf(log, "ledger: %s was missing, so it was rebuilt from the notes' "+
			"stamps: %d row(s), none with an input key\n", path, recovered)
	}
	return ledger.OpenFile(path)
}

// followMoves moves the ledger's rows after the notes the night moved, over
// one population of vault-relative paths, and says how many it moved.
//
// A note's existence is checked with its exact spelling. The vault sits on a
// case-insensitive disk, where the old path of a case-only rename (`Agent/` to
// `agent/`) still answers a plain stat, and the row would never move.
func followMoves(ctx context.Context, cfg *config.Config, led *ledger.Ledger,
	population []string, log io.Writer) {
	if led == nil || len(population) == 0 {
		return
	}
	n, err := led.Follow(ctx, ledger.StageEnrich, population,
		func(rel string) bool { return existsExactly(cfg.VaultPath, rel) },
		func(rel, version, rules string) (string, bool) {
			raw, err := os.ReadFile(filepath.Join(cfg.VaultPath, filepath.FromSlash(rel)))
			if err != nil {
				return "", false
			}
			fp := &enrich.Fingerprint{Version: version, RulesHash: rules}
			return fp.Key(string(raw)), true
		})
	if err != nil {
		// Reported, not fatal: an unfollowed row costs the moved note one
		// judgment, which is what it cost before rows followed at all.
		fmt.Fprintf(log, "ledger: following moved notes: %v\n", err)
		return
	}
	if n > 0 {
		fmt.Fprintf(log, "ledger: followed %d moved note(s) to their new path\n", n)
	}
}

// existsExactly reports whether a vault-relative path names a file, every
// segment spelled exactly as the directory lists it.
func existsExactly(vault, rel string) bool {
	dir := vault
	parts := strings.Split(rel, "/")
	for i, part := range parts {
		entries, err := os.ReadDir(dir)
		if err != nil {
			return false
		}
		found := false
		for _, e := range entries {
			if e.Name() == part {
				found = i == len(parts)-1 || e.IsDir()
				break
			}
		}
		if !found {
			return false
		}
		dir = filepath.Join(dir, part)
	}
	return true
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
// business: for enrichment it is the cards in the contract's class
// directories, which is exactly what the batch drain walks. Handing the ledger a different population than the drain
// uses would produce a coverage number about a set nothing works on.
func pendingFor(ctx context.Context, stage string, cfg *config.Config,
	idx *index.Index, led *ledger.Ledger) (ledger.Report, error) {
	if stage != ledger.StageEnrich {
		return ledger.Report{}, fmt.Errorf("no eligible population defined for "+
			"stage %q; a coverage number over a population nobody can name is a "+
			"number nobody can act on", stage)
	}

	fp := enrichFingerprint(cfg, nil)
	// The drop folder and the class directories, in the order the drain serves
	// them, and not the project records — a card is eligible here, a record is
	// counted by the drain and not by this number (agentm-vault plan 16 locked
	// the inbox into the eligible population: the drain walks it, so the
	// coverage number has to, or it is a number about a set nobody can name).
	queue, err := enrichServeOrder(cfg, idx, false)
	if err != nil {
		return ledger.Report{}, err
	}
	followMoves(ctx, cfg, led, queue, os.Stderr)
	var targets []ledger.Target
	for _, rel := range queue {
		raw, err := os.ReadFile(filepath.Join(cfg.VaultPath, filepath.FromSlash(rel)))
		if err != nil {
			// In the index and not on disk: a drifted index, which the
			// reconcile pass fixes. Counting it as eligible would put a
			// permanent pending item in a queue nothing can drain.
			continue
		}
		targets = append(targets, ledger.Target{Rel: rel, Key: fp.Key(string(raw))})
	}
	// Both halves of the version, because the contract is part of it: a rules
	// edit makes every enriched note re-enrichment eligible, and it should read
	// as one contract edit rather than as a corpus that changed under you.
	return led.Pending(ctx, stage, ledger.Version{
		Stage: enrich.PassVersion, Rules: currentJudgmentHash(cfg),
	}, targets)
}
