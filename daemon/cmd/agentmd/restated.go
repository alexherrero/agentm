package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/dreaming"
	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/restated"
	"github.com/alexherrero/agentm/daemon/internal/tiers"
)

// `agentmd restated` — restated rules merge on their own (task 178, step 8).
//
// The nightly pass shortlists convention, preference and workflow pairs by
// embedding similarity, asks the strong tier whether each shortlisted pair is
// the same rule restated, and merges the ones it says are — by supersede,
// through the dreaming journal, the older note keeping its path and taking the
// newer wording. A verb of its own for the same reason crystallize is one: it
// spends, so it runs under its own `budget:` line and its own switch.
//
// Off (`daemon.restated_merge_enabled`), the pass judges and reports without
// merging; `-dry-run` shortlists without spending anything. A verdict is kept
// against both bodies, so a pair is judged once until one of them changes.

// RestatedVerdictsFile and RestatedReviewFile live in the engine state's
// `dreaming/` beside the Python cycle's review proposals. The needs-review map
// reads the second: every shortlisted pair that did not merge, with the judge's
// reason, for the operator.
const (
	RestatedVerdictsFile = "restated-verdicts.json"
	RestatedReviewFile   = "restated-review.json"
	RestatedRunsFile     = "restated-runs.jsonl"
)

type restatedVerdict struct {
	Verdict  string    `json:"verdict"`
	Reason   string    `json:"reason"`
	JudgedAt time.Time `json:"judged_at"`
}

type restatedRow struct {
	A          string  `json:"a"`
	B          string  `json:"b"`
	ATitle     string  `json:"a_title,omitempty"`
	BTitle     string  `json:"b_title,omitempty"`
	Similarity float64 `json:"similarity"`
	Verdict    string  `json:"verdict"`
	Reason     string  `json:"reason,omitempty"`
	Cached     bool    `json:"cached,omitempty"`
	Merged     bool    `json:"merged,omitempty"`
	// Survivor keeps its path and takes the newer wording; Folded is superseded
	// by it. Set only on a merged pair.
	Survivor string `json:"survivor,omitempty"`
	Folded   string `json:"folded,omitempty"`
}

type restatedRun struct {
	At           time.Time               `json:"at"`
	Job          string                  `json:"job"`
	Model        string                  `json:"model"`
	Tier         string                  `json:"tier"`
	Line         float64                 `json:"line"`
	Notes        int                     `json:"notes"`
	Shortlisted  int                     `json:"shortlisted"`
	Judged       int                     `json:"judged"`
	Same         int                     `json:"same"`
	Merged       int                     `json:"merged"`
	ModelCalls   int                     `json:"model_calls"`
	Tokens       int64                   `json:"tokens"`
	TotalCostUSD float64                 `json:"total_cost_usd"`
	Usage        map[string]enrich.Usage `json:"usage,omitempty"`
	ByJob        map[string]enrich.Usage `json:"by_job,omitempty"`
	Pairs        []restatedRow           `json:"pairs"`
	Errors       []string                `json:"errors,omitempty"`
	RunID        string                  `json:"run_id,omitempty"`
	DryRun       bool                    `json:"dry_run,omitempty"`
}

func restatedStateDir(cfg *config.Config) string {
	return filepath.Join(cfg.EngineStateDir, "dreaming")
}

func readJSON(path string, into any) error {
	raw, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	return json.Unmarshal(raw, into)
}

func writeJSON(path string, v any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	blob, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, append(blob, '\n'), 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// vectorModel is the embedding model whose vectors the pass compares: the
// configured one when it has vectors, else the only one the index holds.
func vectorModel(cfg *config.Config, idx *index.Index) (string, error) {
	models, err := idx.VectorModels()
	if err != nil {
		return "", err
	}
	for _, m := range models {
		if m == cfg.EmbedModel {
			return m, nil
		}
	}
	if len(models) == 1 {
		return models[0], nil
	}
	return "", fmt.Errorf("the index holds vectors for %d models %v and daemon.embed_model names none of "+
		"them; run `agentmd embed` first", len(models), models)
}

func cmdRestated(args []string) error {
	fs := newFlagSet("restated")
	opts := bindCommon(fs)
	line := fs.Float64("line", restated.DefaultReviewLine, "the similarity a pair must reach to be judged")
	cap := fs.Int("cap", restated.DefaultCap, "the most pairs one run sends to the judge")
	dryRun := fs.Bool("dry-run", false, "shortlist and stop: no model calls, nothing written")
	apply := fs.Bool("apply", false, "merge the pairs judged the same rule, through the journal")
	yes := fs.Bool("yes", false, "merge even though daemon.restated_merge_enabled is off")
	asJSON := fs.Bool("json", false, "emit the run as JSON")
	model := fs.String("model", "", "override the strong-tier model")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cfg, err := config.Load(*opts)
	if err != nil {
		return err
	}
	// The nightly job always passes -apply; with the switch off the pass judges
	// and reports, and says so, which is what the switch off means.
	merging := *apply && (cfg.RestatedMergeEnabled || *yes)
	if *apply && !merging && !*asJSON {
		fmt.Println("restated: daemon.restated_merge_enabled is off — judging and reporting, merging nothing " +
			"(pass -yes to merge this once)")
	}

	notes, err := restated.Gather(cfg.VaultPath, cfg.MemoryRoot)
	if err != nil {
		return err
	}
	idx, err := index.OpenWithSidecar(cfg.IndexPath, cfg.VaultPath, cfg.MemoryRoot, cfg.EngineStateDir, cfg.DecayEnabled)
	if err != nil {
		return err
	}
	defer idx.Close()
	vmodel, err := vectorModel(cfg, idx)
	if err != nil {
		return err
	}
	rels := make([]string, len(notes))
	for i, n := range notes {
		rels[i] = n.Rel
	}
	vecs, err := idx.VectorsFor(vmodel, rels)
	if err != nil {
		return err
	}
	pairs := restated.Shortlist(notes, vecs, *line)

	name := strongModel(cfg)
	if *model != "" {
		name = *model
	}
	table, err := tiers.Load(tierMetaDir(cfg))
	if err != nil {
		table = &tiers.Table{}
	}
	route := table.Route(tiers.FuzzyMerge, cfg.CheapModel, name, enrich.PassVersion)
	meter := enrich.NewMeter()
	if !*asJSON {
		meter.OnCall = callLinePrinter(meter, os.Stdout)
	}
	caller := enrich.DefaultCaller(route.Model)
	caller.Meter = meter
	caller.Tier = string(route.Tier)
	caller.Label = string(tiers.FuzzyMerge)
	caller.Job = string(tiers.FuzzyMerge)
	caller.SystemPrompt = restated.SystemPrompt

	verdictsPath := filepath.Join(restatedStateDir(cfg), RestatedVerdictsFile)
	verdicts := map[string]restatedVerdict{}
	if err := readJSON(verdictsPath, &verdicts); err != nil {
		return fmt.Errorf("reading %s: %w", verdictsPath, err)
	}
	now := time.Now().UTC()
	run := restatedRun{At: now, Job: string(tiers.FuzzyMerge), Model: route.Model, Tier: string(route.Tier),
		Line: *line, Notes: len(notes), Shortlisted: len(pairs), DryRun: *dryRun}
	judged := 0
	for _, p := range pairs {
		row := restatedRow{A: p.A.MemoryRel, B: p.B.MemoryRel, ATitle: p.A.Title, BTitle: p.B.Title,
			Similarity: p.Similarity, Verdict: "unjudged"}
		if v, ok := verdicts[p.Key()]; ok {
			row.Verdict, row.Reason, row.Cached = v.Verdict, v.Reason, true
		} else if !*dryRun && judged < *cap {
			judged++
			ctx, cancel := context.WithTimeout(context.Background(), caller.Timeout)
			out, err := caller.Call(ctx, restated.Prompt(p))
			cancel()
			if err == nil {
				var v restated.Verdict
				if v, err = restated.ParseVerdict(out); err == nil {
					row.Verdict, row.Reason = v.Verdict, v.Reason
					verdicts[p.Key()] = restatedVerdict{Verdict: v.Verdict, Reason: v.Reason, JudgedAt: now}
				}
			}
			if err != nil {
				run.Errors = append(run.Errors, fmt.Sprintf("%s ~ %s: %v", p.A.MemoryRel, p.B.MemoryRel, err))
			}
		}
		if row.Verdict == "same" {
			run.Same++
		}
		run.Pairs = append(run.Pairs, row)
	}
	run.Judged = judged

	if !*dryRun {
		if err := writeJSON(verdictsPath, verdicts); err != nil {
			return err
		}
	}

	if merging && !*dryRun {
		if err := mergeRestated(cfg, pairs, &run, now); err != nil {
			run.Errors = append(run.Errors, err.Error())
		}
	}

	if !*dryRun {
		var review []restatedRow
		for _, r := range run.Pairs {
			if !r.Merged {
				review = append(review, r)
			}
		}
		if err := writeJSON(filepath.Join(restatedStateDir(cfg), RestatedReviewFile), map[string]any{
			"written": now, "line": *line, "pairs": review}); err != nil {
			return err
		}
	}
	total := meter.Total()
	run.ModelCalls, run.Tokens, run.TotalCostUSD = total.Calls, total.Added(), total.CostUSD
	run.Usage, run.ByJob = meter.ByTier(), meter.ByJob()
	if !*dryRun {
		// Beside the enrichment and crystallize records, where the morning note
		// reads a night's spend and acts.
		if err := appendJSONLine(filepath.Join(enrichStateDir(cfg), RestatedRunsFile), run); err != nil {
			fmt.Fprintf(os.Stderr, "restated: writing the run record: %v\n", err)
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
	fmt.Printf("restated: %d rule note(s), %d pair(s) at or above %.2f, %d judged this run, %d the same, %d merged\n",
		run.Notes, run.Shortlisted, run.Line, run.Judged, run.Same, run.Merged)
	fmt.Printf("  judge: %s on %s — %s\n", route.Tier, route.Model, route.Why)
	for _, r := range run.Pairs {
		mark := r.Verdict
		if r.Merged {
			mark = "merged"
		}
		fmt.Printf("  %.3f  %-9s %s ~ %s — %s\n", r.Similarity, mark, filepath.Base(r.A), filepath.Base(r.B), r.Reason)
	}
	for _, e := range run.Errors {
		fmt.Println("  error:", e)
	}
	blob, err := json.Marshal(map[string]any{"total_cost_usd": run.TotalCostUSD, "tokens": run.Tokens,
		"model_calls": run.ModelCalls, "merged": run.Merged})
	if err != nil {
		return err
	}
	fmt.Println(string(blob))
	return nil
}

// mergeRestated merges the pairs judged the same, the most alike first, each
// note in at most one merge a run: through the dreaming journal, as one
// manifest, refused whole when any of its notes changed since it was read.
func mergeRestated(cfg *config.Config, pairs []restated.Pair, run *restatedRun, now time.Time) error {
	byKey := map[string]int{}
	for i, r := range run.Pairs {
		byKey[r.A+"|"+r.B] = i
	}
	used := map[string]bool{}
	var acts []dreaming.ManifestAct
	var merged []int
	order := map[int][2]string{}
	for _, p := range pairs {
		i := byKey[p.A.MemoryRel+"|"+p.B.MemoryRel]
		if run.Pairs[i].Verdict != "same" || used[p.A.Rel] || used[p.B.Rel] {
			continue
		}
		older, newer := restated.Order(p)
		survivor, superseded, err := restated.Merge(older, newer, now.Format("2006-01-02"))
		if err != nil {
			return err
		}
		why := fmt.Sprintf("the same rule as %s, restated (similarity %.3f; the judge: %s)",
			older.MemoryRel, p.Similarity, run.Pairs[i].Reason)
		acts = append(acts,
			dreaming.ManifestAct{Rel: older.MemoryRel, Before: dreaming.Hash([]byte(older.Raw)), After: survivor,
				Summary: "takes the newer wording of " + newer.MemoryRel},
			dreaming.ManifestAct{Rel: newer.MemoryRel, Before: dreaming.Hash([]byte(newer.Raw)), After: superseded,
				Summary: why, From: "active", To: "superseded"})
		used[p.A.Rel], used[p.B.Rel] = true, true
		merged = append(merged, i)
		order[i] = [2]string{older.MemoryRel, newer.MemoryRel}
	}
	if len(acts) == 0 {
		return nil
	}
	sort.Ints(merged)
	res, err := dreaming.ApplyManifest(cfg, dreaming.Manifest{Job: "restated",
		Reason: "restated rules merge on their own (agentm-vault § Dreaming, amended 2026-09-28)",
		Acts:   acts}, dreaming.ApplyManifestOptions{Apply: true, Now: now})
	run.RunID = res.RunID
	if err != nil {
		return err
	}
	for _, i := range merged {
		run.Pairs[i].Merged = true
		run.Pairs[i].Survivor, run.Pairs[i].Folded = order[i][0], order[i][1]
	}
	run.Merged = len(merged)
	return nil
}

func appendJSONLine(path string, v any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	line, err := json.Marshal(v)
	if err != nil {
		return err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = f.Write(append(line, '\n'))
	return err
}
