package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/note"
	"github.com/alexherrero/agentm/daemon/internal/tiers"
)

// The cheap-tier audit for the light pass (operator rulings, 2026-09-11): a
// model judges agreement, the cheap model is named at invocation, and the
// command projects before it spends.

const (
	auditCheap  = "claude-haiku-4-5-20251001"
	auditStrong = "opus"
)

var auditNow = time.Date(2026, 9, 11, 22, 0, 0, 0, time.UTC)

// auditConfig is the smallest config the audit reads: a vault with a live
// contract, a memory root, an engine state dir for the table and the record,
// and an index path.
func auditConfig(t *testing.T) *config.Config {
	t.Helper()
	cfg := configOverRules(t, t.TempDir(), "preference")
	cfg.MemoryRoot = "Agent"
	cfg.EngineStateDir = t.TempDir()
	cfg.IndexPath = filepath.Join(t.TempDir(), "index.db")
	return cfg
}

// fakeAuditCalls answers both tiers with one shape and judges by a rule on
// the sample's ref, which the judge's label carries. It meters what a real
// pair of callers would, so the record's usage has something to say.
func fakeAuditCalls(disagree func(ref string) bool) (*auditCalls, *int) {
	calls := 0
	meter := enrich.NewMeter()
	spend := func(label, model, tier string) {
		meter.Record(enrich.CallRecord{Label: label, Model: model, Tier: tier,
			Usage: enrich.Usage{InputTokens: 100, OutputTokens: 20, CostUSD: 0.01, Calls: 1}})
	}
	return &auditCalls{
		ask: func(_ context.Context, r enrich.Route, label, _ string) (string, error) {
			calls++
			spend(label, r.Model, r.Tier)
			return `{"summary":"what the card is for","tags":["git"],"related":[],"confidence":0.9}`, nil
		},
		judge: func(_ context.Context, label, _ string) (string, error) {
			calls++
			spend(label, defaultAuditJudge, auditMeterJudge)
			ref := strings.TrimPrefix(label, "audit judge · ")
			if disagree != nil && disagree(ref) {
				return `{"agree": false, "reason": "B drops the tag the card is about"}`, nil
			}
			return `{"agree": true, "reason": "the same filing"}`, nil
		},
		meter: meter,
	}, &calls
}

func auditSampleRef(i int) string {
	return fmt.Sprintf("Agent/memory/semantic/card-%02d.md", i)
}

func auditSamplesN(n int) []tiers.Sample {
	out := make([]tiers.Sample, n)
	for i := range out {
		out[i] = tiers.Sample{Ref: auditSampleRef(i), Prompt: "light pass", Card: "the card"}
	}
	return out
}

// disagreeOnFirst is a judge rule that disagrees on the first n samples.
func disagreeOnFirst(n int) func(string) bool {
	first := map[string]bool{}
	for i := 0; i < n; i++ {
		first[auditSampleRef(i)] = true
	}
	return func(ref string) bool { return first[ref] }
}

func plannedAudit(samples int) tierAudit {
	return tierAudit{Job: tiers.Summarize, Cheap: auditCheap, Strong: auditStrong,
		Judge: defaultAuditJudge, PassVersion: enrich.PassVersion, Samples: samples, Seed: 7}
}

// stampedCard is a card the current pass has judged on the strong tier.
func stampedCard(i int) string {
	return fmt.Sprintf("---\ntitle: Card %02d about git and Drive\ntype: preference\n"+
		"summary: Keep git out of Drive, card %d.\ntags: [git, drive]\nstatus: active\n"+
		"enriched_at: 2026-09-11T00:00:00Z\nenriched_by: %s\n---\n\n"+
		"Drive corrupts git index files, card %d.\n", i, i, enrich.PassVersion, i)
}

// putCards writes notes into the vault and indexes them, then closes the
// index so the command can open it the way it does in production.
func putCards(t *testing.T, cfg *config.Config, cards map[string]string) {
	t.Helper()
	x, err := index.Open(cfg.IndexPath, cfg.VaultPath, cfg.MemoryRoot, false)
	if err != nil {
		t.Fatal(err)
	}
	defer x.Close()
	for rel, body := range cards {
		abs := filepath.Join(cfg.VaultPath, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(abs, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
		n := note.Note{Rel: rel, Title: enrich.FrontmatterValue(body, "title"), Body: body,
			Captured: auditNow, CapturedSource: "mtime"}
		if err := x.Upsert(n, 1, int64(len(body))); err != nil {
			t.Fatal(err)
		}
	}
}

func stampedVault(t *testing.T, cfg *config.Config, n int) {
	t.Helper()
	cards := map[string]string{}
	for i := 0; i < n; i++ {
		cards[auditSampleRef(i)] = stampedCard(i)
	}
	putCards(t, cfg, cards)
}

func recordLines(t *testing.T, cfg *config.Config) []tierAuditRecord {
	t.Helper()
	raw, err := os.ReadFile(tierAuditsPath(cfg))
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		t.Fatal(err)
	}
	var out []tierAuditRecord
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		var r tierAuditRecord
		if err := json.Unmarshal([]byte(line), &r); err != nil {
			t.Fatalf("a record line will not parse: %v\n%s", err, line)
		}
		out = append(out, r)
	}
	return out
}

func tableExists(cfg *config.Config) bool {
	_, err := os.Stat(tiers.TablePath(tierMetaDir(cfg)))
	return err == nil
}

// --- refusals ---------------------------------------------------------------

// No cheap model is a refusal, not a default: the choice is the operator's at
// invocation. --cheap names it, `daemon.cheap_model` may too, and the flag
// wins when both do.
func TestATierAuditRefusesWithoutACheapModel(t *testing.T) {
	cfg := auditConfig(t)
	_, err := planTierAudit(cfg, "summarize", "", "", "", "", 30, 7)
	if err == nil || !strings.Contains(err.Error(), "no cheap model") {
		t.Fatalf("an audit with no cheap model was not refused for that reason: %v", err)
	}

	cfg.CheapModel = "haiku-from-config"
	a, err := planTierAudit(cfg, "summarize", "", "", "", "", 30, 7)
	if err != nil {
		t.Fatal(err)
	}
	if a.Cheap != "haiku-from-config" {
		t.Errorf("daemon.cheap_model was not honoured: %q", a.Cheap)
	}
	if a.Strong != enrich.DefaultStrongModel || a.Judge != defaultAuditJudge ||
		a.PassVersion != enrich.PassVersion {
		t.Errorf("the defaults did not resolve: %+v", a)
	}

	a, err = planTierAudit(cfg, "summarize", auditCheap, "", "", "", 30, 7)
	if err != nil {
		t.Fatal(err)
	}
	if a.Cheap != auditCheap {
		t.Errorf("--cheap did not win over the config: %q", a.Cheap)
	}
}

// A pinned job is refused before anything is drawn, and even when handed
// straight to the runner nothing is spent and nothing is written.
func TestATierAuditRefusesAPinnedJob(t *testing.T) {
	cfg := auditConfig(t)
	for _, job := range []tiers.Job{tiers.Crystallize, tiers.EntityIdentityMerge,
		tiers.SelfImprovementProposal} {
		_, err := planTierAudit(cfg, string(job), auditCheap, "", "", "", 30, 7)
		if err == nil || !strings.Contains(err.Error(), "pinned") {
			t.Errorf("auditing %s was not refused as pinned: %v", job, err)
		}
	}

	calls, n := fakeAuditCalls(nil)
	a := plannedAudit(30)
	a.Job = tiers.Crystallize
	if _, err := runTierAudit(context.Background(), cfg, a, auditSamplesN(30),
		calls, auditNow); err == nil {
		t.Error("the runner accepted a pinned job")
	}
	if *n != 0 {
		t.Errorf("%d call(s) were made auditing a pinned job", *n)
	}
	if tableExists(cfg) || len(recordLines(t, cfg)) != 0 {
		t.Error("a refused audit wrote a table or a record")
	}
}

// The judge may not be the model on trial, and the audit is keyed to the
// running pass version because that is what the sampled cards carry.
func TestATierAuditRefusesAJudgeOnTrialAndAForeignPassVersion(t *testing.T) {
	cfg := auditConfig(t)
	_, err := planTierAudit(cfg, "summarize", auditCheap, "", auditCheap, "", 30, 7)
	if err == nil || !strings.Contains(err.Error(), "judge") {
		t.Errorf("the cheap model was accepted as its own judge: %v", err)
	}
	_, err = planTierAudit(cfg, "summarize", auditCheap, "", "", "enrich/0", 30, 7)
	if err == nil || !strings.Contains(err.Error(), "pass version") {
		t.Errorf("a foreign --pass-version was accepted: %v", err)
	}
	if _, err := planTierAudit(cfg, "summarize", auditCheap, "", "", "", 0, 7); err == nil {
		t.Error("--samples 0 was accepted")
	}
}

// --- the projection ---------------------------------------------------------

// Without --yes the command says what it would spend and stops: three calls
// per card, at the strong tier's measured cost, and not one call made.
func TestATierAuditProjectsBeforeSpending(t *testing.T) {
	cfg := auditConfig(t)
	stampedVault(t, cfg, 40)
	calls, n := fakeAuditCalls(nil)
	var out bytes.Buffer
	deps := auditDeps{
		calls: func(_, _ string, _ io.Writer) *auditCalls { return calls },
		out:   &out, now: func() time.Time { return auditNow },
	}

	err := cmdTiersAudit(cfg, "summarize", auditCheap, "", "", "", 30, 7, false, false, deps)
	if err != nil {
		t.Fatal(err)
	}
	text := out.String()
	for _, want := range []string{
		"40 card(s) carry the current stamp", "30 drawn", "seed 7",
		"3 calls per card", "90 calls",
		// The 2026-09-11 figures, since this state dir holds no recorded runs.
		"~18,000 tokens", "$0.20 per call", "1,620,000 tokens", "$18.00",
		"nothing spent — pass --yes",
	} {
		if !strings.Contains(text, want) {
			t.Errorf("the projection does not say %q:\n%s", want, text)
		}
	}
	if *n != 0 {
		t.Errorf("%d call(s) were made without --yes", *n)
	}
	if tableExists(cfg) || len(recordLines(t, cfg)) != 0 {
		t.Error("a projection wrote a table or a record")
	}

	// --json without --yes emits the projection as one object.
	out.Reset()
	if err := cmdTiersAudit(cfg, "summarize", auditCheap, "", "", "", 30, 7,
		false, true, deps); err != nil {
		t.Fatal(err)
	}
	var proj auditProjection
	if err := json.Unmarshal(out.Bytes(), &proj); err != nil {
		t.Fatalf("--json did not emit one object: %v\n%s", err, out.String())
	}
	if !proj.NothingSpent || proj.Calls != 90 || proj.Drawn != 30 || proj.Pool != 40 {
		t.Errorf("projection = %+v", proj)
	}

	// A --strong the batch would not route against is allowed, and flagged.
	out.Reset()
	if err := cmdTiersAudit(cfg, "summarize", auditCheap, "sonnet", "", "", 30, 7,
		false, false, deps); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out.String(), "the batch routes against opus, not sonnet") {
		t.Errorf("a foreign --strong was not flagged:\n%s", out.String())
	}

	// A draw under the floor is allowed, and says it cannot qualify.
	out.Reset()
	if err := cmdTiersAudit(cfg, "summarize", auditCheap, "", "", "", 10, 7,
		false, false, deps); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out.String(), "under the 25-sample floor") {
		t.Errorf("a draw of 10 was not flagged as unable to qualify:\n%s", out.String())
	}
}

// --- the verdict, and what it saves ------------------------------------------

// A qualification is saved only when the judge's agreements meet the bar
// over at least the floor's worth of judged samples. Every run is recorded,
// saved or not, with the judge's reason on each disagreement.
func TestATierAuditSavesAQualificationOnlyWhenTheJudgeMeetsTheBar(t *testing.T) {
	cases := []struct {
		name      string
		samples   int
		disagree  int
		wantSaved bool
	}{
		{"two of thirty disagree, over the bar", 30, 2, true},
		{"five of thirty disagree, under the bar", 30, 5, false},
		{"perfect agreement over too few", tiers.MinSamples - 1, 0, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cfg := auditConfig(t)
			calls, n := fakeAuditCalls(disagreeOnFirst(tc.disagree))
			rec, err := runTierAudit(context.Background(), cfg, plannedAudit(tc.samples),
				auditSamplesN(tc.samples), calls, auditNow)
			if err != nil {
				t.Fatal(err)
			}
			if *n != auditCallsPerSample*tc.samples || rec.Report.Calls != *n {
				t.Errorf("made %d call(s), reported %d; want %d — three per sample",
					*n, rec.Report.Calls, auditCallsPerSample*tc.samples)
			}
			if rec.Saved != tc.wantSaved || tableExists(cfg) != tc.wantSaved {
				t.Errorf("saved = %v, table on disk = %v; want %v: %s",
					rec.Saved, tableExists(cfg), tc.wantSaved, rec.Report.Why)
			}
			if len(rec.Report.Disagreements) != tc.disagree {
				t.Fatalf("named %d disagreement(s), want %d", len(rec.Report.Disagreements),
					tc.disagree)
			}
			for _, d := range rec.Report.Disagreements {
				if d.Reason != "B drops the tag the card is about" {
					t.Errorf("a disagreement lost the judge's reason: %+v", d)
				}
			}

			lines := recordLines(t, cfg)
			if len(lines) != 1 {
				t.Fatalf("the record holds %d line(s), want one per run", len(lines))
			}
			got := lines[0]
			if got.Audit.Judge != defaultAuditJudge || got.Audit.Seed != 7 ||
				got.Saved != tc.wantSaved || got.Report.Sampled != tc.samples {
				t.Errorf("the record does not describe the run: %+v", got.Audit)
			}
			for _, tier := range []string{enrich.TierStrong, enrich.TierCheap, auditMeterJudge} {
				if got.Usage[tier].Calls != tc.samples {
					t.Errorf("the record's %s usage counts %d call(s), want %d",
						tier, got.Usage[tier].Calls, tc.samples)
				}
			}

			if !tc.wantSaved {
				return
			}
			table, err := tiers.Load(tierMetaDir(cfg))
			if err != nil {
				t.Fatal(err)
			}
			r := table.Route(tiers.Summarize, auditCheap, auditStrong, enrich.PassVersion)
			if r.Tier != tiers.Cheap || r.Model != auditCheap {
				t.Errorf("after a qualifying audit the light pass routes %s: %s", r.Tier, r.Why)
			}
			q, _ := table.Lookup(tiers.Summarize)
			if q.Judge != defaultAuditJudge {
				t.Errorf("the saved qualification does not name its judge: %+v", q)
			}
			// And the router the batch uses reads the same table.
			cfg.CheapModel = auditCheap
			if got := enrichRouter(cfg, auditStrong)(enrich.DepthLight); got.Tier != enrich.TierCheap {
				t.Errorf("enrichRouter still routes the light pass %s: %s", got.Tier, got.Why)
			}
			if got := enrichRouter(cfg, auditStrong)(enrich.DepthDeep); got.Tier != enrich.TierStrong {
				t.Errorf("the deep pass moved with it: %s", got.Why)
			}
		})
	}
}

// The whole command path with --yes: the projection, the run, the table and
// the record, and under --json one object on stdout.
func TestTheTierAuditCommandRunsEndToEnd(t *testing.T) {
	cfg := auditConfig(t)
	stampedVault(t, cfg, 40)
	calls, n := fakeAuditCalls(nil)
	var out bytes.Buffer
	deps := auditDeps{
		calls: func(_, _ string, _ io.Writer) *auditCalls { return calls },
		out:   &out, now: func() time.Time { return auditNow },
	}
	if err := cmdTiersAudit(cfg, "summarize", auditCheap, "", "", "", 30, 7,
		true, false, deps); err != nil {
		t.Fatal(err)
	}
	if *n != 90 {
		t.Errorf("the command made %d call(s), want 90", *n)
	}
	text := out.String()
	for _, want := range []string{"90 calls", "30 judged · 30 agreed · 100.0%",
		"qualified:", "saved to", "runs on the cheap tier (" + auditCheap + ")",
		"recorded: " + tierAuditsPath(cfg)} {
		if !strings.Contains(text, want) {
			t.Errorf("the report does not say %q:\n%s", want, text)
		}
	}
	if !tableExists(cfg) || len(recordLines(t, cfg)) != 1 {
		t.Error("the run did not leave a table and one record line")
	}

	// A second run under --json: stdout is one object, the record grows by one.
	out.Reset()
	if err := cmdTiersAudit(cfg, "summarize", auditCheap, "", "", "", 30, 7,
		true, true, deps); err != nil {
		t.Fatal(err)
	}
	var rec tierAuditRecord
	if err := json.Unmarshal(out.Bytes(), &rec); err != nil {
		t.Fatalf("--json --yes did not emit one object: %v\n%s", err, out.String())
	}
	if !rec.Saved || rec.Report.Agreed != 30 {
		t.Errorf("record = %+v", rec.Report)
	}
	if len(recordLines(t, cfg)) != 2 {
		t.Error("the second run was not recorded")
	}
}

// --- the sample ---------------------------------------------------------------

// The pool is the cards the current pass has already judged — stamped with the
// running version — and only those the free gates would let a model see.
func TestTheAuditPoolIsTheCardsTheCurrentPassJudged(t *testing.T) {
	cfg := auditConfig(t)
	old := strings.Replace(stampedCard(1), enrich.PassVersion, "enrich/0+prompt/000000000000", 1)
	unstamped := "---\ntitle: Never judged\ntype: preference\n---\n\nNothing judged this.\n"
	record := strings.Replace(stampedCard(2), "status: active\n", "status: active\nkind: brief\n", 1)
	leaky := strings.Replace(stampedCard(3), "card 3.\n", "card 3 ghp_"+strings.Repeat("a", 36)+"\n", 1)
	huge := stampedCard(4) + strings.Repeat("padding to push the card over the size ceiling\n", 800)
	putCards(t, cfg, map[string]string{
		auditSampleRef(0):                     stampedCard(0),
		"Agent/memory/semantic/old-stamp.md":  old,
		"Agent/memory/semantic/unstamped.md":  unstamped,
		"Agent/memory/semantic/a-record.md":   record,
		"Agent/memory/semantic/leaky.md":      leaky,
		"Agent/memory/semantic/huge.md":       huge,
		"Agent/memory/mocs/moc-git.md":        stampedCard(5),
		"Agent/memory/episodic/2026-trace.md": stampedCard(6),
		// The test contract routes every type to memory/semantic, so a
		// procedural card is outside the queue here; the queue's own test
		// proves a second routed class is walked.
		"Agent/memory/procedural/not-routed.md": stampedCard(7),
		"Agent/memory/semantic/second-ok.md":    stampedCard(8),
	})

	x, err := index.Open(cfg.IndexPath, cfg.VaultPath, cfg.MemoryRoot, false)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { x.Close() })
	pool, err := auditPool(context.Background(), cfg, x)
	if err != nil {
		t.Fatal(err)
	}
	want := auditSampleRef(0) + ",Agent/memory/semantic/second-ok.md"
	if got := strings.Join(pool, ","); got != want {
		t.Errorf("the pool is %s\nwant %s", got, want)
	}

	samples := auditSamples(context.Background(), cfg, x, pool)
	if len(samples) != 2 {
		t.Fatalf("built %d sample(s) from a pool of 2", len(samples))
	}
	for _, s := range samples {
		if !strings.HasSuffix(strings.TrimSpace(s.Prompt), "This is a light pass.") {
			t.Errorf("%s is not asked the light-pass question:\n%s", s.Ref, s.Prompt)
		}
		if !strings.Contains(s.Prompt, "The card:") || !strings.Contains(s.Prompt, "enriched_by:") {
			t.Errorf("%s's prompt does not carry the card", s.Ref)
		}
		if !strings.Contains(s.Card, "Neighbours offered") || !strings.Contains(s.Card, "enriched_by:") {
			t.Errorf("%s's judge card lacks the card or its neighbours:\n%s", s.Ref, s.Card)
		}
	}
	// The other card is the one neighbour on offer, so `related` means something
	// to the judge.
	if !strings.Contains(samples[0].Card, "id: second-ok") ||
		!strings.Contains(samples[1].Card, "id: card-00") {
		t.Errorf("the judge is not shown the neighbour both tiers were offered:\n%s\n%s",
			samples[0].Card, samples[1].Card)
	}
}

// --- the judge ----------------------------------------------------------------

// The judge is shown the card, the strong answer as the reference and the
// cheap answer on trial, and asked one question: the same filing and rank,
// not the same bytes.
func TestTheJudgePromptCarriesTheCardAndBothAnswers(t *testing.T) {
	p := auditJudgePrompt("THE CARD", `{"summary":"strong"}`, `{"summary":"cheap"}`, 0.65)
	for _, want := range []string{
		"THE CARD", `{"summary":"strong"}`, `{"summary":"cheap"}`,
		"Answer A (the strong tier)", "Answer B (the cheap tier)",
		"would the vault file and rank this card the same", "Byte equality is not the question",
		"filing floor of 0.65", `{"agree": false, "reason":`,
	} {
		if !strings.Contains(p, want) {
			t.Errorf("the judge prompt does not carry %q", want)
		}
	}
	if strings.Index(p, "Answer A") > strings.Index(p, "Answer B") {
		t.Error("the strong answer is not the reference shown first")
	}
}

// A verdict must answer. A fence around it is tolerated; no `agree` is not a
// verdict, and is an error rather than either answer.
func TestTheJudgesVerdictMustAnswer(t *testing.T) {
	v, err := parseAuditVerdict("```json\n{\"agree\": false, \"reason\": \"B renames the subject\"}\n```")
	if err != nil || v.Agree || v.Reason != "B renames the subject" {
		t.Errorf("a fenced verdict was not read: %+v, %v", v, err)
	}
	if _, err := parseAuditVerdict(`{"reason": "no verdict here"}`); err == nil {
		t.Error("an object with no agree field was read as a verdict")
	}
	if _, err := parseAuditVerdict("I think they broadly agree."); err == nil {
		t.Error("prose with no object was read as a verdict")
	}
}
