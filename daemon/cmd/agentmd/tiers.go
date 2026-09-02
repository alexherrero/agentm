package main

import (
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/tiers"
)

// cmdTiers is how a dreaming stage asks which model it may use.
//
// The same seam the rules, the ledger, the queue and the registry opened. The
// stages live in the Python half; the measurement that decides what they may
// spend lives here, in a file committed to the vault. One reader, asked over a
// command, rather than a rule each stage carries its own copy of.
//
// Two seams in `dream.py` and `dream_confirm.py` — `cheap_model_tier_available`
// and `higher_tier_model_available` — have returned `False` since they were
// written, each with a comment saying it is the point a future build wires to a
// real primitive. Part 4 built that primitive. This is what those seams ask.
func cmdTiers(args []string) error {
	fs := newFlagSet("tiers")
	opts := bindCommon(fs)
	asJSON := fs.Bool("json", false, "emit the answer as JSON")
	job := fs.String("job", "", "report on one job (default: all of them)")
	cheap := fs.String("cheap", "", "the cheap model this run would use")
	strong := fs.String("strong", "", "the strong model this run would use")
	version := fs.String("pass-version", "", "the pass version this run is at")
	forget := fs.String("forget", "",
		"drop a job's qualification, sending it back to the strong tier")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if extra := fs.Args(); len(extra) > 0 {
		return fmt.Errorf("unexpected argument %q; usage: agentmd tiers "+
			"[--job NAME] [--cheap MODEL --strong MODEL --pass-version V] [--json]",
			extra[0])
	}

	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}
	dir := tierMetaDir(cfg)
	table, err := tiers.Load(dir)
	if err != nil {
		return err
	}

	if *forget != "" {
		if !table.Forget(tiers.Job(*forget)) {
			return fmt.Errorf("tiers: %s has no qualification to drop", *forget)
		}
		if err := table.Save(dir, time.Now()); err != nil {
			return err
		}
		fmt.Printf("dropped %s's qualification — it runs on the strong tier until "+
			"a fresh audit earns it back\n", *forget)
		return nil
	}

	// The models default to what the daemon is configured with, so a stage can
	// ask the plain question and get an answer about the run that would happen.
	if *strong == "" {
		*strong = cfg.EnrichModel
	}
	if *cheap == "" {
		*cheap = cfg.CheapModel
	}

	if *job != "" {
		r := table.Route(tiers.Job(*job), *cheap, *strong, *version)
		if *asJSON {
			return json.NewEncoder(os.Stdout).Encode(r)
		}
		// The model alone on the first line, so a shell can take it with `head
		// -1` without parsing anything, and the reason underneath so a human
		// reading the same output learns how it was arrived at.
		fmt.Println(r.Model)
		fmt.Fprintf(os.Stderr, "%s → %s: %s\n", r.Job, r.Tier, r.Why)
		return nil
	}

	routes := table.RouteAll(*cheap, *strong, *version)
	if *asJSON {
		return json.NewEncoder(os.Stdout).Encode(routes)
	}
	fmt.Printf("cheap %s · strong %s · pass %s\n", *cheap, *strong, *version)
	for _, r := range routes {
		fmt.Printf("  %-26s %-6s %s\n", r.Job, r.Tier, r.Why)
	}
	fmt.Printf("\nbar: %.0f%% agreement over at least %d samples, pre-registered\n",
		tiers.MinAgreement*100, tiers.MinSamples)
	fmt.Printf("table: %s\n", tiers.TablePath(dir))
	return nil
}

// tierMetaDir is where the durable tier table lives — the engine state
// directory, beside the source registry's sidecar, per filing-v2 part 2a:
// machine state left the vault, and its durability property (history) moved
// with it, because the engine state dir is a git repository the runner
// commits on the vault's own cadence.
func tierMetaDir(cfg *config.Config) string {
	return cfg.EngineStateDir
}
