package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/crystallize"
	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/tiers"
)

// The second job that spends, and the record the runner and the morning note
// read its spend from.

func TestTheWeeklyPhaseIsPinnedToTheStrongTier(t *testing.T) {
	// Not a configuration. A bad lesson enters the decay-exempt layer, where
	// nothing ages it out, so no qualification may route this job cheap — and
	// the table refuses to store one that says otherwise.
	table := &tiers.Table{}
	route := table.Route(tiers.Crystallize, "haiku", "opus", enrich.PassVersion)
	if route.Tier != tiers.Strong || route.Model != "opus" {
		t.Fatalf("routed %s on %s, want strong", route.Tier, route.Model)
	}
	if !strings.Contains(route.Why, "pinned") {
		t.Errorf("the route's reason reads %q, want it to say it is pinned", route.Why)
	}
	err := table.Record(tiers.Qualification{
		Job: tiers.Crystallize, Tier: tiers.Cheap, CheapModel: "haiku",
		StrongModel: "opus", PassVersion: enrich.PassVersion, Sampled: 100,
		Agreed: 100, Rate: 1.0, MinAgreement: tiers.MinAgreement,
		MinSamples: tiers.MinSamples, QualifiedAt: time.Now(),
	})
	if err == nil {
		t.Error("a perfect audit qualified the phase for the cheap tier; pinning " +
			"that can be measured away is not pinning")
	}
}

func TestTheRunRecordNamesTheJobAndCarriesItsSpend(t *testing.T) {
	state := t.TempDir()
	cfg := &config.Config{EngineStateDir: state, IndexPath: filepath.Join(state, "i.db")}
	rec := crystallizeRun{
		At: time.Now().UTC(), Job: string(tiers.Crystallize), Model: "opus",
		Tier: string(tiers.Strong), ModelCalls: 2, Tokens: 48000, TotalCostUSD: 1.40,
		ByJob: map[string]enrich.Usage{"crystallize": {
			InputTokens: 40000, OutputTokens: 8000, CostUSD: 1.40, Calls: 2}},
		Lessons: []crystallize.Written{{Rel: "memory/crystallized/a.md", Subject: "a"}},
	}
	if err := appendCrystallizeRun(cfg, rec); err != nil {
		t.Fatal(err)
	}

	raw, err := os.ReadFile(filepath.Join(state, CrystallizeRunsFile))
	if err != nil {
		t.Fatal(err)
	}
	var back map[string]any
	if err := json.Unmarshal(raw, &back); err != nil {
		t.Fatalf("the record is not one JSON object per line: %v\n%s", err, raw)
	}
	if back["job"] != "crystallize" {
		t.Errorf("the record's job reads %v; the morning note gives the spend a "+
			"line per job and reads the name from here", back["job"])
	}
	// `total_cost_usd` is what the runner's spend line reads, and `by_job` is
	// what the morning note's per-job line reads. Both by those exact names.
	if back["total_cost_usd"] != 1.40 {
		t.Errorf("total_cost_usd reads %v", back["total_cost_usd"])
	}
	if _, ok := back["by_job"].(map[string]any)["crystallize"]; !ok {
		t.Errorf("by_job does not name the job: %v", back["by_job"])
	}
}

func TestTheRecordSitsBesideEnrichments(t *testing.T) {
	// One directory, two files, because a person debugging a night opens them
	// together — and because the morning note reads the pair from one place.
	state := t.TempDir()
	cfg := &config.Config{EngineStateDir: state, IndexPath: filepath.Join(state, "i.db")}
	if got, want := filepath.Dir(crystallizeRunsPath(cfg)),
		filepath.Dir(enrichRunsPath(cfg)); got != want {
		t.Errorf("crystallize records land in %s and enrichment's in %s", got, want)
	}
}

func TestThePhaseRefusesWhileItsSwitchIsOff(t *testing.T) {
	// The same refusal enrichment makes. "I ran the command and nothing
	// happened" is a report this project has debugged too many times, and a
	// job that spends is not armed by a binary update.
	dir := t.TempDir()
	t.Setenv("HOME", dir)
	t.Setenv("MEMORY_ROOT", filepath.Join(dir, "vault", "agent"))
	if err := os.MkdirAll(filepath.Join(dir, "vault", "agent"), 0o755); err != nil {
		t.Fatal(err)
	}
	err := cmdCrystallize(nil)
	if err == nil {
		t.Fatal("the phase ran with its switch off")
	}
	for _, want := range []string{"--yes", "--dry-run", "daemon.crystallize_enabled"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the refusal does not mention %s: %v", want, err)
		}
	}
}
