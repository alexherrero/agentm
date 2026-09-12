package main

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/tiers"
)

// The audit that earns the light pass its cheap tier.
//
// `agentmd tiers --audit --job summarize --cheap MODEL` draws a sample of the
// cards the current pass has already judged on the strong tier, asks both
// tiers the light-pass question about each one under identical conditions,
// and has a third model say whether the two answers would file and rank the
// card the same way. A verdict that meets the pre-registered bar is saved to
// the tier table, and `enrichRouter` routes the light pass cheap from the next
// run; one that does not saves nothing and says why.
//
// # Why the strong tier is asked again rather than read from disk
//
// Every card in the pool carries a strong-tier answer already — that is what
// the stamp means — and it cannot stand in for the strong call, for two
// reasons that each suffice. It is a deep-shape answer: the deep pass set
// title, type and importance, and the light pass is a different question with
// a different last line. And it is not the raw answer: what is on disk went
// through the post-gates, Compose and CarryProvenance, so a field the gate
// stripped or the carry restored would read as the strong tier's judgment
// when it was the gate's. The comparison is between two models on one prompt,
// so both are asked the same prompt, in the same shape, through the same
// Caller.
//
// # Why the judge is a model
//
// `tiers.Agrees` says a summary agrees when it says the same thing, which is a
// judgment rather than a comparison. The operator ruled on 2026-09-11 that a
// third model makes it — Fable by default, `--judge` to change it — shown the
// card, the strong tier's answer and the cheap tier's, returning a binary
// verdict and one sentence of reason. Every disagreement carries that reason
// in the report, so the rate is a number that can be checked.

// defaultAuditJudge decides agreement when --judge names no other model: the
// operator's ruling of 2026-09-11, a flag so it changes without a rebuild.
const defaultAuditJudge = "claude-fable-5-1"

// defaultAuditSamples is how many cards an audit draws when --samples names no
// number: the 25-sample floor and a margin for the calls that fail, since a
// tier or a judge that could not be reached takes its sample out of the rate.
const defaultAuditSamples = 30

// auditCallsPerSample is the strong call, the cheap call and the judge.
const auditCallsPerSample = 3

// The light pass as measured on 2026-09-11 from the recorded runs: about
// 18,000 tokens added and $0.20 per strong-tier call. The projection reads the
// record itself when it has calls to measure from, and falls back to these.
const (
	auditTokensPerCallMeasured int64   = 18_000
	auditUSDPerCallMeasured    float64 = 0.20
)

// tierAudit is one audit as the flags described it.
type tierAudit struct {
	Job         tiers.Job `json:"job"`
	Cheap       string    `json:"cheap_model"`
	Strong      string    `json:"strong_model"`
	Judge       string    `json:"judge"`
	PassVersion string    `json:"pass_version"`
	Samples     int       `json:"samples"`
	Seed        int64     `json:"seed"`
}

// planTierAudit resolves the flags into an audit, refusing what cannot run
// before anything is drawn or spent.
//
// The cheap model comes from --cheap, else `daemon.cheap_model`, and from
// nowhere else: with neither named the audit refuses rather than picking one,
// because which model is on trial is the operator's choice. The strong model
// resolves as the batch resolves it. The pass version is the running one,
// because that is what the sampled cards carry and what the router checks.
func planTierAudit(cfg *config.Config, job, cheap, strong, judge, version string,
	samples int, seed int64) (tierAudit, error) {
	a := tierAudit{Job: tiers.Job(job), Samples: samples, Seed: seed}
	if job == "" {
		return a, fmt.Errorf("tiers: an audit names the job it measures; usage: " +
			"agentmd tiers --audit --job summarize --cheap MODEL [--judge MODEL] " +
			"[--samples N] [--seed S] [--yes]")
	}
	a.Cheap = cheap
	if a.Cheap == "" {
		a.Cheap = cfg.CheapModel
	}
	if a.Cheap == "" {
		return a, fmt.Errorf("tiers: no cheap model is named — pass --cheap MODEL, " +
			"or set daemon.cheap_model in the kernel config. This refuses rather " +
			"than defaulting one, because which model is on trial is the " +
			"operator's choice")
	}
	a.Strong = strong
	if a.Strong == "" {
		a.Strong = strongModel(cfg)
	}
	if err := tiers.CanAudit(a.Job, a.Cheap, a.Strong); err != nil {
		return a, err
	}
	a.Judge = judge
	if a.Judge == "" {
		a.Judge = defaultAuditJudge
	}
	if a.Judge == a.Cheap {
		return a, fmt.Errorf("tiers: the judge (%s) is the cheap model under audit; "+
			"a model deciding whether it agrees with a stronger one is not a "+
			"measurement of it", a.Judge)
	}
	a.PassVersion = enrich.PassVersion
	if version != "" && version != enrich.PassVersion {
		return a, fmt.Errorf("tiers: the audit is keyed to the running pass version "+
			"%s, which is what the sampled cards carry; --pass-version %s would "+
			"qualify a version nothing routes", enrich.PassVersion, version)
	}
	if a.Samples < 1 {
		return a, fmt.Errorf("tiers: --samples must be at least 1")
	}
	if a.Seed == 0 {
		a.Seed = time.Now().UnixNano()
	}
	return a, nil
}

// auditPool is every card the current pass has already judged on the strong
// tier and that the free gates would let a model see: stamped with the
// running pass version, in a space a background model may read, not a
// derived class or a record, carrying no credential shape, small enough to
// send whole. The same gates the pass runs, less the two that read a ledger
// or a budget — this walk spends nothing.
func auditPool(ctx context.Context, cfg *config.Config, idx *index.Index) ([]string, error) {
	dirs, err := enrichQueueDirs(cfg)
	if err != nil {
		return nil, err
	}
	queue, err := enrichQueue(idx, dirs)
	if err != nil {
		return nil, err
	}
	gates := freeGates(cfg)
	var pool []string
	for _, rel := range queue {
		raw, err := os.ReadFile(filepath.Join(cfg.VaultPath, filepath.FromSlash(rel)))
		if err != nil {
			continue
		}
		if enrich.PassDepth(string(raw)) != enrich.DepthLight {
			continue
		}
		req := enrich.Request{Rel: rel, Raw: string(raw)}
		eligible := true
		for _, g := range gates {
			if g.Check(ctx, req, req.Raw) != nil {
				eligible = false
				break
			}
		}
		if eligible {
			pool = append(pool, rel)
		}
	}
	return pool, nil
}

// auditSamples builds what each call is shown for the drawn cards: the
// light-pass prompt both tiers answer — the same text to both, rendered the
// way the pass renders it, neighbours included — and the card with its
// neighbours for the judge.
func auditSamples(ctx context.Context, cfg *config.Config, idx *index.Index,
	drawn []string) []tiers.Sample {
	var types []string
	rubric := ""
	if loaded, err := cfg.Rules.Get(); err == nil {
		types = loaded.TypesSorted()
		rubric = loaded.ImportanceRubric
	}
	neighbours := enrichNeighbours(cfg, idx, modelMayRead(cfg))
	out := make([]tiers.Sample, 0, len(drawn))
	for _, rel := range drawn {
		raw, err := os.ReadFile(filepath.Join(cfg.VaultPath, filepath.FromSlash(rel)))
		if err != nil {
			continue
		}
		req := enrich.Request{Rel: rel, Raw: string(raw), Trigger: enrich.TriggerBatch}
		// The card's own stamp decides the shape, as it does in the pass; the
		// pool only admits cards it reads as light.
		req.Depth = enrich.PassDepth(req.Raw)
		req.Neighbours = neighbours(ctx, req)
		out = append(out, tiers.Sample{
			Ref:    rel,
			Prompt: enrich.BuildPrompt(req, types, rubric),
			Card:   judgeCard(req),
		})
	}
	return out
}

// judgeCard is the card as the judge reads it: its own text, and the
// neighbours both answers were offered, so a `related` id means something.
func judgeCard(req enrich.Request) string {
	var b strings.Builder
	b.WriteString(req.Raw)
	b.WriteString("\n\nNeighbours offered (the only ids related may name):\n")
	if len(req.Neighbours) == 0 {
		b.WriteString("  (none)\n")
	}
	for _, n := range req.Neighbours {
		fmt.Fprintf(&b, "  - id: %s\n    title: %s\n", n.ID,
			strings.Join(strings.Fields(n.Title), " "))
	}
	return b.String()
}

// auditJudgeSystemPrompt replaces enrichment's for the judge's calls: the
// judge compares, it does not write.
const auditJudgeSystemPrompt = "You compare two answers about one card in a " +
	"personal memory vault. You reply with JSON and nothing else — no preamble, " +
	"no code fence, no commentary."

// auditJudgeInstructions is the question the judge answers.
//
// Written so that "agree" means the two answers would file and rank the card
// the same way — the same meaning in the summary, tags that name the same
// things, a related set that overlaps on what matters, confidence on the same
// side of the floor — and not that they are the same bytes. The strong tier's
// answer is the reference, because the question the audit asks is whether the
// cheap tier could serve in its place.
const auditJudgeInstructions = `You are judging whether two model tiers agree on one card from a
personal memory vault. Both were asked the same light-pass question about the
card: return its summary, tags, related and confidence, keeping the title,
type and importance as the card states them unless plainly wrong. Answer A is
the strong tier's, the tier the vault trusts. Answer B is a cheaper tier on
trial. The question is whether B could serve in A's place on cards like this.

Answer exactly one question: would the vault file and rank this card the same
way under Answer B as under Answer A?

Agree when all of these hold:

  - the summaries say the same thing about what the card is for, in
    different words if need be;
  - the tags name the same things — a spelling, a plural or a synonym is the
    same tag, and an extra tag is fine when it names something the card is
    about;
  - the related sets overlap on the neighbours that bear on the card: the
    same closest neighbours, not necessarily the same list in the same order;
  - the title and type are the same, or both left as the card states them;
  - confidence falls on the same side of the filing floor of %.2f — the vault
    files a card at or above it and sends one below it to review.

Disagree when either answer would file the card differently: a summary about
a different subject or making a different claim, tags that name different
subjects, a related set that drops a neighbour the other answer rightly kept,
a title or type moved by one answer and not the other, or confidence on
opposite sides of the floor.

Byte equality is not the question. Two answers that would file and rank the
card the same way agree.

Return a single JSON object and nothing else:

  {"agree": true, "reason": "one sentence saying what matches"}

or

  {"agree": false, "reason": "one sentence naming what would file differently"}`

// auditJudgePrompt renders the judge's question over one card and its two
// answers.
func auditJudgePrompt(card, strong, cheap string, floor float64) string {
	var b strings.Builder
	fmt.Fprintf(&b, auditJudgeInstructions, floor)
	b.WriteString("\n\nThe card:\n\n")
	b.WriteString(strings.TrimRight(card, "\n"))
	b.WriteString("\n\nAnswer A (the strong tier):\n\n")
	b.WriteString(strings.TrimSpace(strong))
	b.WriteString("\n\nAnswer B (the cheap tier):\n\n")
	b.WriteString(strings.TrimSpace(cheap))
	b.WriteString("\n")
	return b.String()
}

// parseAuditVerdict reads the judge's answer. A verdict must answer: an
// object with no `agree` is not one, and is an error rather than a
// disagreement or an agreement.
func parseAuditVerdict(raw string) (tiers.Verdict, error) {
	var v struct {
		Agree  *bool  `json:"agree"`
		Reason string `json:"reason"`
	}
	obj, err := enrich.ExtractJSON(raw)
	if err != nil {
		return tiers.Verdict{}, err
	}
	if err := json.Unmarshal([]byte(obj), &v); err != nil {
		return tiers.Verdict{}, fmt.Errorf("tiers: the judge's verdict is not the "+
			"expected shape: %w", err)
	}
	if v.Agree == nil {
		return tiers.Verdict{}, fmt.Errorf("tiers: the judge returned no verdict: %s",
			obj)
	}
	return tiers.Verdict{Agree: *v.Agree, Reason: strings.TrimSpace(v.Reason)}, nil
}

// auditCalls is how the audit reaches a model: one seam for the tiers, one
// for the judge, both over the shared Caller in production and over a fake in
// tests. The meter is where every call's spend adds up.
type auditCalls struct {
	ask   func(ctx context.Context, r enrich.Route, label, prompt string) (string, error)
	judge func(ctx context.Context, label, prompt string) (string, error)
	meter *enrich.Meter
}

// auditMeterJudge is the meter's key for the judge's spend. Its own line
// rather than the strong tier's, so the report can say what the judge cost
// apart from the tier it judged.
const auditMeterJudge = "judge"

// newAuditCalls wires both seams to `claude -p` through the enrichment
// Caller, so every call the audit makes carries the same isolation the pass
// does and reads its usage from the same envelope.
func newAuditCalls(strong, judge string, lines io.Writer) *auditCalls {
	meter := enrich.NewMeter()
	meter.OnCall = callLinePrinter(meter, lines)
	tier := enrich.DefaultCaller(strong)
	tier.Meter = meter
	judged := enrich.DefaultCaller(judge)
	judged.Meter = meter
	judged.SystemPrompt = auditJudgeSystemPrompt
	judged.Tier = auditMeterJudge
	return &auditCalls{
		ask: func(ctx context.Context, r enrich.Route, label, prompt string) (string, error) {
			return tier.With(r, label).Call(ctx, prompt)
		},
		judge: func(ctx context.Context, label, prompt string) (string, error) {
			return judged.With(enrich.Route{}, label).Call(ctx, prompt)
		},
		meter: meter,
	}
}

// tierAuditRecord is one line of `tier-audits.jsonl`: the audit as it ran,
// what it measured, and what it spent.
type tierAuditRecord struct {
	At           time.Time               `json:"at"`
	Audit        tierAudit               `json:"audit"`
	Drawn        int                     `json:"drawn"`
	Report       tiers.AuditReport       `json:"report"`
	Saved        bool                    `json:"saved"`
	Usage        map[string]enrich.Usage `json:"usage,omitempty"`
	Tokens       int64                   `json:"tokens"`
	TotalCostUSD float64                 `json:"total_cost_usd"`
	ElapsedSec   float64                 `json:"elapsed_seconds"`
}

// tierAuditsPath is where the record lives: beside the enrichment record, in
// the engine state directory, so the morning note can read both from one place.
func tierAuditsPath(cfg *config.Config) string {
	return filepath.Join(filepath.Dir(enrichRunsPath(cfg)), "tier-audits.jsonl")
}

// appendTierAudit adds one audit to the record.
func appendTierAudit(cfg *config.Config, rec tierAuditRecord) error {
	p := tierAuditsPath(cfg)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return err
	}
	line, err := json.Marshal(rec)
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

// confidenceFloor is the filing floor the judge is told about, read from the
// contract the way the stamp reads it.
func confidenceFloor(cfg *config.Config) float64 {
	floor := 0.0
	if cfg.Rules != nil {
		if loaded, err := cfg.Rules.Get(); err == nil {
			if v, ok := loaded.Threshold(enrich.ConfidenceFloorThreshold); ok {
				floor = v
			}
		}
	}
	return enrich.Floor(floor)
}

// runTierAudit spends: both tiers and the judge over every sample, the
// qualification saved when the verdict meets the bar, and the run recorded
// either way.
func runTierAudit(ctx context.Context, cfg *config.Config, a tierAudit,
	samples []tiers.Sample, calls *auditCalls, now time.Time) (tierAuditRecord, error) {
	started := time.Now()
	floor := confidenceFloor(cfg)

	ask := func(ctx context.Context, model, prompt string) (string, error) {
		r := enrich.Route{Model: model, Tier: enrich.TierStrong, Job: string(a.Job)}
		if model == a.Cheap {
			r.Tier = enrich.TierCheap
		}
		return calls.ask(ctx, r, "audit · "+string(a.Job)+" · "+r.Tier, prompt)
	}
	agrees := func(ctx context.Context, s tiers.Sample, cheap, strong string) (tiers.Verdict, error) {
		raw, err := calls.judge(ctx, "audit judge · "+s.Ref,
			auditJudgePrompt(s.Card, strong, cheap, floor))
		if err != nil {
			return tiers.Verdict{}, err
		}
		return parseAuditVerdict(raw)
	}

	rep, q, err := tiers.Audit(ctx, a.Job, a.Cheap, a.Strong, a.PassVersion,
		samples, ask, agrees, now)
	if err != nil {
		return tierAuditRecord{}, err
	}

	rec := tierAuditRecord{At: now.UTC(), Audit: a, Drawn: len(samples), Report: rep}
	if calls.meter != nil {
		total := calls.meter.Total()
		rec.Usage = calls.meter.ByTier()
		rec.Tokens = total.Tokens()
		rec.TotalCostUSD = total.CostUSD
	}

	// Saved only on a verdict that meets the bar, and loaded fresh rather than
	// from before the run: the audit takes minutes, and another writer may
	// have touched the table since.
	var saveErr error
	if rep.Qualified {
		q.Judge = a.Judge
		saveErr = saveQualification(cfg, q, now)
		rec.Saved = saveErr == nil
	}
	rec.ElapsedSec = time.Since(started).Seconds()
	// Recorded whatever happened to the save: the money was spent and the
	// measurement exists, and a record that only held the runs that also
	// saved would be a record of the wrong thing.
	if err := appendTierAudit(cfg, rec); err != nil {
		return rec, err
	}
	return rec, saveErr
}

// saveQualification records one qualification in the tier table.
func saveQualification(cfg *config.Config, q tiers.Qualification, now time.Time) error {
	dir := tierMetaDir(cfg)
	table, err := tiers.Load(dir)
	if err != nil {
		return err
	}
	if err := table.Record(q); err != nil {
		return err
	}
	return table.Save(dir, now)
}

// auditCost is what one call is expected to cost, for the projection.
type auditCost struct {
	Tokens int64   `json:"tokens_per_call"`
	USD    float64 `json:"usd_per_call"`
	Basis  string  `json:"basis"`
}

// measuredAuditCost reads the strong tier's cost per call from the recorded
// enrichment runs — a live measurement rather than a constant — and falls
// back to the 2026-09-11 figures when the record holds no calls.
func measuredAuditCost(cfg *config.Config) auditCost {
	fallback := auditCost{
		Tokens: auditTokensPerCallMeasured, USD: auditUSDPerCallMeasured,
		Basis: "the light pass as measured on 2026-09-11",
	}
	f, err := os.Open(enrichRunsPath(cfg))
	if err != nil {
		return fallback
	}
	defer f.Close()
	var added int64
	var usd float64
	calls := 0
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	for sc.Scan() {
		var r enrichRun
		if json.Unmarshal(sc.Bytes(), &r) != nil {
			continue
		}
		u, ok := r.Usage[enrich.TierStrong]
		if !ok || u.Calls == 0 {
			continue
		}
		added += u.Added()
		usd += u.CostUSD
		calls += u.Calls
	}
	if calls == 0 {
		return fallback
	}
	return auditCost{
		Tokens: added / int64(calls), USD: usd / float64(calls),
		Basis: fmt.Sprintf("%d strong-tier call(s) recorded in %s", calls,
			filepath.Base(enrichRunsPath(cfg))),
	}
}

// auditProjection is what the audit would spend, printed before it does.
type auditProjection struct {
	Audit tierAudit `json:"audit"`
	// BatchStrong is the strong model the batch itself would route against.
	// When --strong names another, the qualification this audit could save is
	// one the router will never match, and the projection says so.
	BatchStrong    string    `json:"batch_strong_model"`
	Pool           int       `json:"pool"`
	Drawn          int       `json:"drawn"`
	Calls          int       `json:"calls"`
	PerCall        auditCost `json:"per_call"`
	Tokens         int64     `json:"tokens_if_every_call_were_strong"`
	USD            float64   `json:"usd_if_every_call_were_strong"`
	UnderTheFloor  bool      `json:"under_the_floor"`
	MinAgreement   float64   `json:"min_agreement"`
	MinSamples     int       `json:"min_samples"`
	NothingSpent   bool      `json:"nothing_spent"`
	HowToSpendNote string    `json:"note,omitempty"`
}

func projectTierAudit(a tierAudit, batchStrong string, pool, drawn int,
	cost auditCost) auditProjection {
	calls := auditCallsPerSample * drawn
	return auditProjection{
		Audit: a, BatchStrong: batchStrong, Pool: pool, Drawn: drawn, Calls: calls,
		PerCall: cost,
		Tokens:  int64(calls) * cost.Tokens, USD: float64(calls) * cost.USD,
		UnderTheFloor: drawn < tiers.MinSamples,
		MinAgreement:  tiers.MinAgreement, MinSamples: tiers.MinSamples,
	}
}

// printAuditProjection is the projection as a person reads it.
func printAuditProjection(w io.Writer, p auditProjection) {
	a := p.Audit
	fmt.Fprintf(w, "audit: %s · cheap %s · strong %s · judge %s · pass %s\n",
		a.Job, a.Cheap, a.Strong, a.Judge, a.PassVersion)
	fmt.Fprintf(w, "  %d card(s) carry the current stamp and pass the free gates; "+
		"%d drawn (seed %d — pass --seed %d to draw the same cards again)\n",
		p.Pool, p.Drawn, a.Seed, a.Seed)
	fmt.Fprintf(w, "  %d calls per card — the cheap tier, the strong tier, then the "+
		"judge — %d calls\n", auditCallsPerSample, p.Calls)
	fmt.Fprintf(w, "  at ~%s tokens added and ~$%.2f per call (%s): about %s tokens "+
		"and $%.2f if every call cost what a strong call does; the cheap call "+
		"costs less, the judge may cost more\n",
		commasInt(p.PerCall.Tokens), p.PerCall.USD, p.PerCall.Basis,
		commasInt(p.Tokens), p.USD)
	fmt.Fprintf(w, "  bar: %.0f%% agreement over at least %d usable samples, "+
		"pre-registered\n", p.MinAgreement*100, p.MinSamples)
	if p.UnderTheFloor {
		fmt.Fprintf(w, "  %d is under the %d-sample floor: this run measures but "+
			"cannot qualify anything\n", p.Drawn, p.MinSamples)
	}
	if p.BatchStrong != "" && p.BatchStrong != a.Strong {
		fmt.Fprintf(w, "  note: the batch routes against %s, not %s; a qualification "+
			"against %s routes nothing until daemon.enrich_model names it\n",
			p.BatchStrong, a.Strong, a.Strong)
	}
}

// printTierAudit is the report as a person reads it.
func printTierAudit(w io.Writer, cfg *config.Config, rec tierAuditRecord) {
	a, rep := rec.Audit, rec.Report
	fmt.Fprintf(w, "audit: %s · cheap %s · strong %s · judge %s · pass %s · seed %d\n",
		a.Job, a.Cheap, a.Strong, a.Judge, a.PassVersion, a.Seed)
	fmt.Fprintf(w, "  %d drawn · %d judged · %d agreed · %.1f%% · %d excluded (a "+
		"tier failed) · %d unjudged (the judge failed)\n",
		rec.Drawn, rep.Sampled, rep.Agreed, rep.Rate*100, rep.Failed, rep.Unjudged)
	for _, d := range rep.Disagreements {
		fmt.Fprintf(w, "  disagreed: %s — %s\n", d.Ref, d.Reason)
	}
	for _, f := range rep.Failures {
		fmt.Fprintf(w, "  failed: %s · %s — %s\n", f.Ref, f.Tier, oneLine(f.Reason, 300))
	}
	if rep.StoppedBy != "" {
		fmt.Fprintf(w, "  stopped early: %s\n", oneLine(rep.StoppedBy, 300))
	}
	keys := make([]string, 0, len(rec.Usage))
	for k := range rec.Usage {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		fmt.Fprintf(w, "  %s: %s\n", k, rec.Usage[k])
	}
	fmt.Fprintf(w, "  %d call(s) · %s tokens · $%.4f · %.0fs\n", rep.Calls,
		commasInt(rec.Tokens), rec.TotalCostUSD, rec.ElapsedSec)
	switch {
	case rep.Qualified && rec.Saved:
		fmt.Fprintf(w, "qualified: %s — saved to %s; %s runs on the cheap tier (%s) "+
			"from the next run\n", rep.Why, tiers.TablePath(tierMetaDir(cfg)), a.Job, a.Cheap)
	case rep.Qualified:
		fmt.Fprintf(w, "qualified: %s — but the table could not be written; %s stays "+
			"on the strong tier until it is\n", rep.Why, a.Job)
	default:
		fmt.Fprintf(w, "not qualified: %s — nothing saved; %s stays on the strong "+
			"tier\n", rep.Why, a.Job)
	}
	fmt.Fprintf(w, "recorded: %s\n", tierAuditsPath(cfg))
}

// auditDeps is what the command needs from outside the flags, replaceable in
// a test: the model seams, where to print, and the clock.
type auditDeps struct {
	calls func(strong, judge string, lines io.Writer) *auditCalls
	out   io.Writer
	now   func() time.Time
}

func productionAuditDeps() auditDeps {
	return auditDeps{calls: newAuditCalls, out: os.Stdout, now: time.Now}
}

// cmdTiersAudit is the --audit path of `agentmd tiers`.
//
// It projects before it spends and stops there unless --yes was passed: this
// is a deliberate, operator-initiated measurement at full price, and the
// first thing the operator should see is what it will cost.
func cmdTiersAudit(cfg *config.Config, job, cheap, strong, judge, version string,
	samples int, seed int64, yes, asJSON bool, deps auditDeps) error {
	a, err := planTierAudit(cfg, job, cheap, strong, judge, version, samples, seed)
	if err != nil {
		return err
	}
	idx, err := index.OpenWithSidecar(cfg.IndexPath, cfg.VaultPath, cfg.MemoryRoot, cfg.EngineStateDir, cfg.DecayEnabled)
	if err != nil {
		return err
	}
	defer idx.Close()
	ctx := context.Background()

	pool, err := auditPool(ctx, cfg, idx)
	if err != nil {
		return err
	}
	if len(pool) == 0 {
		return fmt.Errorf("tiers: no card carries the current stamp (%s) — the audit "+
			"draws from what the strong tier has already judged under this pass, "+
			"and nothing has been; run the enrichment batch first", enrich.PassVersion)
	}
	drawn := sampleQueue(pool, a.Samples, a.Seed)
	proj := projectTierAudit(a, strongModel(cfg), len(pool), len(drawn),
		measuredAuditCost(cfg))
	if !yes {
		proj.NothingSpent = true
		proj.HowToSpendNote = "pass --yes to run it"
		if asJSON {
			return json.NewEncoder(deps.out).Encode(proj)
		}
		printAuditProjection(deps.out, proj)
		fmt.Fprintln(deps.out, "nothing spent — pass --yes to run it")
		return nil
	}
	// The per-call lines go where the report goes, except under --json, where
	// stdout is one object and the lines would break it.
	lines := deps.out
	if asJSON {
		lines = os.Stderr
	}
	if !asJSON {
		printAuditProjection(deps.out, proj)
	}

	rec, err := runTierAudit(ctx, cfg, a, auditSamples(ctx, cfg, idx, drawn),
		deps.calls(a.Strong, a.Judge, lines), deps.now())
	if err != nil {
		return err
	}
	if asJSON {
		return json.NewEncoder(deps.out).Encode(rec)
	}
	printTierAudit(deps.out, cfg, rec)
	return nil
}
