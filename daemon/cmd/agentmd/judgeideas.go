package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/dreaming"
	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/ideajudge"
	"github.com/alexherrero/agentm/daemon/internal/tiers"
)

// `agentmd judge-ideas` — the night's idea judge (task 192, backlog #676).
//
// The sessions record their idea-shaped remarks in their traces and file
// nothing; this pass reads the remarks, asks the judge what each one was —
// steering, a repeat of an idea card, or a new idea — and writes what the
// verdicts say through the dreaming journal: a repeat added to its card under
// `## Added by dreaming`, a new idea carded in the inbox. It never creates a
// card in `personal/ideas/`. A verb of its own, as crystallize and restated
// are, because it spends: its own tier job (`judge-ideas`), its own cap, its
// own run record beside theirs, where the morning note reads the night's
// spend.
//
// Off (`daemon.idea_judge_enabled`), the pass judges and reports what it would
// write, writing nothing; `-report` does the same with the switch on, and
// `-dry-run` lists the remarks without spending anything. A verdict is kept
// against its remark, so a reporting night's verdicts are written, without a
// second call, on the first night the switch is on.

const (
	IdeaJudgeStateFile = "idea-judge-state.json"
	IdeaJudgeRunsFile  = "idea-judge-runs.jsonl"
)

type ideaJudgeRun struct {
	At           time.Time               `json:"at"`
	Job          string                  `json:"job"`
	Mode         string                  `json:"mode"`
	Model        string                  `json:"model"`
	Tier         string                  `json:"tier"`
	Cap          int                     `json:"cap"`
	Remarks      int                     `json:"remarks"`
	Judged       int                     `json:"judged"`
	Cached       int                     `json:"cached"`
	Steering     int                     `json:"steering"`
	Repeats      int                     `json:"repeats"`
	New          int                     `json:"new"`
	Removed      int                     `json:"removed"`
	Deferred     int                     `json:"deferred"`
	Written      []string                `json:"written,omitempty"`
	ModelCalls   int                     `json:"model_calls"`
	Tokens       int64                   `json:"tokens"`
	TotalCostUSD float64                 `json:"total_cost_usd"`
	Usage        map[string]enrich.Usage `json:"usage,omitempty"`
	ByJob        map[string]enrich.Usage `json:"by_job,omitempty"`
	Rows         []ideajudge.Row         `json:"rows"`
	Errors       []string                `json:"errors,omitempty"`
	RunID        string                  `json:"run_id,omitempty"`
}

func cmdJudgeIdeas(args []string) error {
	fs := newFlagSet("judge-ideas")
	opts := bindCommon(fs)
	cap := fs.Int("cap", ideajudge.DefaultCap, "the most remarks one run sends to the judge")
	dryRun := fs.Bool("dry-run", false, "list the remarks and stop: no model calls, nothing written")
	report := fs.Bool("report", false, "judge and report what would be written, writing nothing")
	apply := fs.Bool("apply", false, "write what the verdicts say, through the journal")
	yes := fs.Bool("yes", false, "write even though daemon.idea_judge_enabled is off")
	asJSON := fs.Bool("json", false, "emit the run as JSON")
	model := fs.String("model", "", "override the judge's model")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}
	// The nightly job always passes -apply; with the switch off the pass judges
	// and reports, and says so, which is what the switch off means.
	writing := *apply && !*report && !*dryRun && (cfg.IdeaJudgeEnabled || *yes)
	mode := "report"
	switch {
	case *dryRun:
		mode = "dry-run"
	case writing:
		mode = "apply"
	}
	if *apply && !writing && !*dryRun && !*report && !*asJSON {
		fmt.Println("judge-ideas: daemon.idea_judge_enabled is off — judging and reporting, writing nothing " +
			"(pass -yes to write this once)")
	}

	name := strongModel(cfg)
	if *model != "" {
		name = *model
	}
	table, err := tiers.Load(tierMetaDir(cfg))
	if err != nil {
		table = &tiers.Table{}
	}
	route := table.Route(tiers.JudgeIdeas, cfg.CheapModel, name, enrich.PassVersion)
	meter := enrich.NewMeter()
	if !*asJSON {
		meter.OnCall = callLinePrinter(meter, os.Stdout)
	}
	caller := enrich.DefaultCaller(route.Model)
	caller.Meter = meter
	caller.Tier = string(route.Tier)
	caller.Label = string(tiers.JudgeIdeas)
	caller.Job = string(tiers.JudgeIdeas)
	caller.SystemPrompt = ideajudge.SystemPrompt

	statePath := filepath.Join(restatedStateDir(cfg), IdeaJudgeStateFile)
	var st ideajudge.State
	if err := readJSON(statePath, &st); err != nil {
		return fmt.Errorf("reading %s: %w", statePath, err)
	}
	ledger, err := ideajudge.ReadLedger(filepath.Join(cfg.EngineStateDir, "reflect-filed.jsonl"))
	if err != nil {
		return err
	}
	now := time.Now().UTC()
	opt := ideajudge.Options{Vault: cfg.VaultPath, MemoryRoot: cfg.MemoryRoot, Cap: *cap, Now: now, Ledger: ledger}
	if !*dryRun {
		opt.Call = func(prompt string) (string, error) {
			ctx, cancel := context.WithTimeout(context.Background(), caller.Timeout)
			defer cancel()
			return caller.Call(ctx, prompt)
		}
	}
	plan, err := ideajudge.Decide(opt, &st)
	if err != nil {
		return err
	}

	run := ideaJudgeRun{At: now, Job: ideajudge.Job, Mode: mode, Model: route.Model, Tier: string(route.Tier),
		Cap: *cap, Remarks: plan.Remarks, Judged: plan.Calls, Cached: plan.Cached, Removed: plan.Removed,
		Deferred: plan.Deferred, Errors: plan.Errors}
	if writing && len(plan.Intents) > 0 {
		outcome := fmt.Sprintf("judge-ideas: %d repeat(s) added to their cards, %d new idea(s) carded in the inbox",
			plan.Count(ideajudge.Repeat), plan.Count(ideajudge.New))
		res, err := dreaming.ApplyIntents(cfg, ideajudge.Job, outcome, plan.Intents, now, 0)
		run.RunID = res.RunID
		if err != nil {
			run.Errors = append(run.Errors, err.Error())
		}
		ideajudge.MarkApplied(&plan, &st, res.AppliedRels, now)
	}
	run.Rows = plan.Rows
	run.Steering, run.Repeats, run.New = plan.Count(ideajudge.Steering), plan.Count(ideajudge.Repeat), plan.Count(ideajudge.New)
	for _, r := range plan.Rows {
		if r.Applied && r.Rel != "" && !contains(run.Written, r.Rel) {
			run.Written = append(run.Written, r.Rel)
		}
	}

	if !*dryRun {
		if err := writeJSON(statePath, st); err != nil {
			return err
		}
	}
	total := meter.Total()
	run.ModelCalls, run.Tokens, run.TotalCostUSD = total.Calls, total.Added(), total.CostUSD
	run.Usage, run.ByJob = meter.ByTier(), meter.ByJob()
	if !*dryRun {
		// Beside the enrichment, crystallize and restated records, where the
		// morning note reads a night's spend; and the counts the dreaming
		// binary's facet reports, for the pass that runs after this one.
		if err := appendJSONLine(filepath.Join(enrichStateDir(cfg), IdeaJudgeRunsFile), run); err != nil {
			fmt.Fprintf(os.Stderr, "judge-ideas: writing the run record: %v\n", err)
		}
		if err := dreaming.WriteIdeaJudgeSummary(cfg.EngineStateDir, dreaming.IdeaJudgeSummary{
			At: now, Mode: mode, Remarks: run.Remarks, Judged: run.Judged, Steering: run.Steering,
			Repeats: run.Repeats, New: run.New, Removed: run.Removed, Deferred: run.Deferred,
			Written: run.Written}); err != nil {
			fmt.Fprintf(os.Stderr, "judge-ideas: writing the night's summary: %v\n", err)
		}
	}

	if *asJSON {
		blob, err := json.MarshalIndent(run, "", "  ")
		if err != nil {
			return err
		}
		fmt.Println(string(blob))
		return nil
	}
	fmt.Printf("judge-ideas (%s): %d remark(s), %d judged this run, %d remembered, %d steering, %d repeat(s), "+
		"%d new, %d removed (no call), %d past the cap of %d\n", mode, run.Remarks, run.Judged, run.Cached,
		run.Steering, run.Repeats, run.New, run.Removed, run.Deferred, *cap)
	fmt.Printf("  judge: %s on %s — %s\n", route.Tier, route.Model, route.Why)
	for _, r := range plan.Rows {
		mark := r.Verdict
		if r.Applied {
			mark += " (written)"
		}
		line := fmt.Sprintf("  %-20s %s — %q", mark, filepath.Base(r.Trace), clip(r.Remark, 90))
		if r.Rel != "" {
			line += " → " + r.Rel
		}
		if r.Reason != "" {
			line += " — " + r.Reason
		}
		fmt.Println(line)
	}
	for _, e := range run.Errors {
		fmt.Println("  error:", e)
	}
	blob, err := json.Marshal(map[string]any{"total_cost_usd": run.TotalCostUSD, "tokens": run.Tokens,
		"model_calls": run.ModelCalls, "written": len(run.Written)})
	if err != nil {
		return err
	}
	fmt.Println(string(blob))
	return nil
}

func contains(xs []string, s string) bool {
	for _, x := range xs {
		if x == s {
			return true
		}
	}
	return false
}

func clip(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n]) + "…"
}
