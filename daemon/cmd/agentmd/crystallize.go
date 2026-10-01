package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/crystallize"
	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/tiers"
)

// `agentmd crystallize` — the weekly phase.
//
// A verb of its own rather than a phase of `agentmdream`, and the reason is the
// money. The night's binary is mechanical and free by session 3's ruling; its
// runner job declares no `budget:` and the fleet ceiling therefore never gates
// it. Folding a paid phase into it would make a free job a spending one without
// anything in the manifest saying so, and the first sign would be a bill.
// Separate verb, separate weekly job, its own `budget:` line, its own switch.

// crystallizeRun is one line of the phase's record, beside enrichment's.
type crystallizeRun struct {
	At           time.Time               `json:"at"`
	Job          string                  `json:"job"`
	Model        string                  `json:"model"`
	Tier         string                  `json:"tier"`
	Why          string                  `json:"why"`
	Sources      int                     `json:"sources"`
	Clusters     int                     `json:"clusters"`
	Considered   int                     `json:"considered"`
	ModelCalls   int                     `json:"model_calls"`
	Tokens       int64                   `json:"tokens"`
	TotalCostUSD float64                 `json:"total_cost_usd"`
	Usage        map[string]enrich.Usage `json:"usage,omitempty"`
	ByJob        map[string]enrich.Usage `json:"by_job,omitempty"`
	Lessons      []crystallize.Written   `json:"lessons,omitempty"`
	Skipped      []crystallize.Skipped   `json:"skipped,omitempty"`
	NearMisses   []crystallize.Skipped   `json:"near_misses,omitempty"`
	Errors       []string                `json:"errors,omitempty"`
	ElapsedSec   float64                 `json:"elapsed_seconds"`
	DryRun       bool                    `json:"dry_run,omitempty"`
	// Recheck is a `-recheck` run's findings: per lesson, the stamped cards it
	// is true of and the ones it releases (#749).
	Recheck []crystallize.RecheckLesson `json:"recheck,omitempty"`
}

// CrystallizeRunsFile is where the record lives, beside `enrich-runs.jsonl`.
// The morning note reads both and gives the spend a line per job.
const CrystallizeRunsFile = "crystallize-runs.jsonl"

func crystallizeRunsPath(cfg *config.Config) string {
	return filepath.Join(enrichStateDir(cfg), CrystallizeRunsFile)
}

func appendCrystallizeRun(cfg *config.Config, r crystallizeRun) error {
	p := crystallizeRunsPath(cfg)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return err
	}
	line, err := json.Marshal(r)
	if err != nil {
		return err
	}
	f, err := os.OpenFile(p, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = f.Write(append(line, '\n'))
	return err
}

// crystallizeMemoryRoot is the memory root as an absolute path. The config
// carries it vault-relative (`agent`), and a phase handed that bare name reads
// and writes under whatever directory the job happened to start in: it found
// no cards, saw no lesson it had already written, and would have written its
// lessons outside the vault altogether. The night and the door join it the same
// way.
func crystallizeMemoryRoot(cfg *config.Config) string {
	return filepath.Join(cfg.VaultPath, filepath.FromSlash(cfg.MemoryRoot))
}

func cmdCrystallize(args []string) error {
	fs := newFlagSet("crystallize")
	opts := bindCommon(fs)
	topic := fs.String("topic", "",
		"narrow the run to subjects matching this word — `/memory crystallize <topic>`")
	cap := fs.Int("cap", 0,
		fmt.Sprintf("the most lessons one run writes (0 keeps %d)", crystallize.DefaultCap))
	dryRun := fs.Bool("dry-run", false,
		"find the recurrences and stop, making no model calls and writing nothing")
	asJSON := fs.Bool("json", false, "emit the report as JSON")
	model := fs.String("model", "", "override the strong-tier model")
	yes := fs.Bool("yes", false,
		"run this one pass even though the weekly phase is off")
	recheck := fs.Bool("recheck", false,
		"ask which stamped cards each lesson is true of, and write a manifest releasing the rest (#749); "+
			"apply it with agentmdream apply -manifest")
	if err := fs.Parse(args); err != nil {
		return err
	}

	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}

	// The same refusal enrichment makes, for the same reason: a command that
	// quietly does nothing is one somebody debugs twice. A dry run is exempt —
	// it spends nothing, and refusing to *look* would make the switch harder
	// to decide about than to throw.
	if !cfg.CrystallizeEnabled && !*yes && !*dryRun {
		return fmt.Errorf("the crystallize phase is off — pass --yes to run this " +
			"one pass, --dry-run to see what it would find for free, or set " +
			"daemon.crystallize_enabled to let the weekly job run on its own")
	}

	name := strongModel(cfg)
	if *model != "" {
		name = *model
	}

	// The tier table's answer, printed rather than assumed. Crystallize is one
	// of the three jobs pinned strong without audit — a bad lesson enters the
	// decay-exempt layer, where nothing ages it out — so this route is always
	// strong and the line says why.
	table, err := tiers.Load(tierMetaDir(cfg))
	if err != nil {
		fmt.Fprintf(os.Stderr, "tiers: %v — the phase runs strong\n", err)
		table = &tiers.Table{}
	}
	route := table.Route(tiers.Crystallize, cfg.CheapModel, name, enrich.PassVersion)

	meter := enrich.NewMeter()
	meter.OnCall = callLinePrinter(meter, os.Stdout)
	caller := enrich.DefaultCaller(route.Model)
	caller.Meter = meter
	caller.Tier = string(route.Tier)
	caller.Label = string(tiers.Crystallize)
	caller.Job = string(tiers.Crystallize)
	caller.SystemPrompt = crystallize.SystemPrompt

	started := time.Now()
	now := started
	if *recheck {
		return runRecheck(cfg, caller, meter, route, *dryRun, *cap, *asJSON, now)
	}
	rep, err := crystallize.Run(crystallize.Options{
		Root: crystallizeMemoryRoot(cfg), Vault: cfg.VaultPath, Now: now,
		Topic: *topic, Cap: *cap, DryRun: *dryRun,
	}, func(prompt string) (string, error) {
		ctx, cancel := context.WithTimeout(context.Background(), caller.Timeout)
		defer cancel()
		return caller.Call(ctx, prompt)
	})
	if err != nil {
		return err
	}

	total := meter.Total()
	rec := crystallizeRun{
		At: now.UTC(), Job: string(tiers.Crystallize), Model: route.Model,
		Tier: string(route.Tier), Why: route.Why,
		Sources: rep.Sources, Clusters: rep.Clusters, Considered: rep.Considered,
		ModelCalls: total.Calls, Tokens: total.Added(), TotalCostUSD: total.CostUSD,
		Usage: meter.ByTier(), ByJob: meter.ByJob(),
		Lessons: rep.Lessons, Skipped: rep.Skipped, NearMisses: rep.NearMisses,
		Errors: rep.Errors, ElapsedSec: time.Since(started).Seconds(),
		DryRun: rep.DryRun,
	}
	// A dry run leaves no record: the file is the night's spend ledger, and a
	// zero-cost line in it would dilute the seven-day total with runs that were
	// never the night's work.
	if !rep.DryRun {
		if err := appendCrystallizeRun(cfg, rec); err != nil {
			fmt.Fprintf(os.Stderr, "crystallize: writing the run record: %v\n", err)
		}
	}

	if *asJSON {
		blob, err := json.MarshalIndent(rec, "", "  ")
		if err != nil {
			return err
		}
		fmt.Println(string(blob))
		return nil
	}

	fmt.Printf("crystallize: %d source(s), %d recurrence(s) over the bar "+
		"(%d sources · %d sessions · %d days)\n", rep.Sources, rep.Clusters,
		crystallize.MinSources, crystallize.MinSessions, crystallize.MinSpanDays)
	fmt.Printf("  tier: %s on %s — %s\n", route.Tier, route.Model, route.Why)
	for _, l := range rep.Lessons {
		fmt.Printf("  wrote %s — %s (from %d, stamped %d)\n", l.Rel, l.Title,
			len(l.Sources), len(l.Stamped))
	}
	for _, s := range rep.Skipped {
		fmt.Printf("  skipped %s (%d sources): %s\n", s.Subject, s.Sources, s.Reason)
	}
	for _, e := range rep.Errors {
		fmt.Printf("  error: %s\n", e)
	}
	// The runner reads a job's cost from the last line of stdout.
	blob, err := json.Marshal(map[string]any{
		"total_cost_usd": rec.TotalCostUSD,
		"tokens":         rec.Tokens,
		"model_calls":    rec.ModelCalls,
		"lessons":        len(rep.Lessons),
	})
	if err != nil {
		return err
	}
	fmt.Println(string(blob))
	return nil
}

// runRecheck asks, for every lesson with stamped cards, which of them the
// lesson is true of, and writes the release of the rest as a manifest the
// dreaming binary makes through its journal (#749). It writes no note itself.
func runRecheck(cfg *config.Config, caller *enrich.Caller, meter *enrich.Meter, route tiers.Routing,
	dryRun bool, cap int, asJSON bool, now time.Time) error {
	root := crystallizeMemoryRoot(cfg)
	call := func(prompt string) (string, error) {
		if dryRun {
			return "", fmt.Errorf("dry run: no call made")
		}
		ctx, cancel := context.WithTimeout(context.Background(), caller.Timeout)
		defer cancel()
		return caller.Call(ctx, prompt)
	}
	caller.SystemPrompt = "You check which notes a written lesson is actually true of. Answer with one JSON object and nothing else."
	found, acts, err := crystallize.PlanRecheck(root, call, cap)
	if err != nil {
		return err
	}
	total := meter.Total()
	rec := crystallizeRun{At: now.UTC(), Job: string(tiers.Crystallize), Model: route.Model,
		Tier: string(route.Tier), Why: route.Why, ModelCalls: total.Calls, Tokens: total.Added(),
		TotalCostUSD: total.CostUSD, Usage: meter.ByTier(), ByJob: meter.ByJob(), Recheck: found, DryRun: dryRun}
	manifest := ""
	if !dryRun {
		if err := appendCrystallizeRun(cfg, rec); err != nil {
			fmt.Fprintf(os.Stderr, "crystallize: writing the run record: %v\n", err)
		}
		if len(acts) > 0 {
			manifest = filepath.Join(root, "diagnostics", "migrations", "crystallize-recheck",
				now.Format("2006-01-02")+"-recheck.json")
			if err := os.MkdirAll(filepath.Dir(manifest), 0o755); err != nil {
				return err
			}
			blob, err := json.MarshalIndent(map[string]any{
				"job":    "manifest-crystallize-recheck",
				"reason": "release the stamped cards a lesson is not true of (#749)",
				"acts":   acts}, "", "  ")
			if err != nil {
				return err
			}
			if err := os.WriteFile(manifest, append(blob, '\n'), 0o644); err != nil {
				return err
			}
		}
	}
	if asJSON {
		blob, err := json.MarshalIndent(map[string]any{"run": rec, "manifest": manifest, "acts": len(acts)}, "", "  ")
		if err != nil {
			return err
		}
		fmt.Println(string(blob))
		return nil
	}
	released := 0
	for _, f := range found {
		released += len(f.Released)
		switch {
		case f.Error != "":
			fmt.Printf("  %s: %s\n", f.Lesson, f.Error)
		case len(f.Released) > 0:
			fmt.Printf("  %s keeps %d, releases %d: %s — %s\n", f.Lesson, len(f.Kept), len(f.Released),
				strings.Join(f.Released, ", "), f.Reason)
		default:
			fmt.Printf("  %s keeps all %d\n", f.Lesson, len(f.Kept))
		}
	}
	fmt.Printf("crystallize -recheck: %d lesson(s) asked, %d card(s) to release, %d act(s)\n", len(found), released, len(acts))
	if manifest != "" {
		fmt.Printf("  manifest %s\n  check it: agentmdream apply -manifest %s\n", manifest, manifest)
	}
	blob, err := json.Marshal(map[string]any{"total_cost_usd": rec.TotalCostUSD, "tokens": rec.Tokens,
		"model_calls": rec.ModelCalls})
	if err != nil {
		return err
	}
	fmt.Println(string(blob))
	return nil
}
