package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/note"
)

// agentm-vault plan 04, task 2: the batch's queue, its record, and the line
// the runner reads.

// The queue is the directories the contract routes a memory type into, less
// the derived classes — read from the contract, so a type routed somewhere new
// is walked without a code change, and episodic (traces) and _watchlist
// (forward-learning records) are walked by nothing.
func TestTheQueueWalksTheContractsClassDirectories(t *testing.T) {
	vault := t.TempDir()
	cfg := configOverRules(t, vault, "preference", "workflow")
	cfg.MemoryRoot = "Agent"
	// writeRules routes every type to memory/semantic; a second destination
	// proves the list is read from routing rather than assumed.
	body, err := os.ReadFile(filepath.Join(vault, "standards", "storage-rules.md"))
	if err != nil {
		t.Fatal(err)
	}
	fixed := strings.Replace(string(body), "  workflow: memory/semantic\n",
		"  workflow: memory/procedural\n", 1)
	if err := os.WriteFile(filepath.Join(vault, "standards", "storage-rules.md"),
		[]byte(fixed), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := cfg.Rules.Refresh(time.Now()); err != nil {
		t.Fatal(err)
	}

	dirs, err := enrichQueueDirs(cfg)
	if err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(dirs, ","); got != "Agent/memory/procedural/,Agent/memory/semantic/" {
		t.Fatalf("queue directories %s, want procedural and semantic from routing", got)
	}

	x, err := index.Open(filepath.Join(t.TempDir(), "index.db"), vault, "", false)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { x.Close() })
	for _, rel := range []string{
		"Agent/memory/semantic/a.md", "Agent/memory/procedural/b.md",
		"Agent/memory/episodic/2026-09-11-trace.md", "Agent/memory/_watchlist/src/w.md",
		"Agent/memory/mocs/moc.md", "Agent/memory/semantic-extra/nope.md",
	} {
		if err := x.Upsert(note.Note{Rel: rel, Title: "t", Body: "b",
			Captured: time.Now().UTC(), CapturedSource: "mtime"}, 1, 1); err != nil {
			t.Fatal(err)
		}
	}
	q, err := enrichQueue(x, dirs)
	if err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(q, ","); got != "Agent/memory/procedural/b.md,Agent/memory/semantic/a.md" {
		t.Errorf("the queue offered %s; want only the two cards", got)
	}
}

// A seeded sample draws the same notes twice and keeps path order, so a
// resumed or deferred sample walks the set it started on.
func TestASeededSampleIsReproducible(t *testing.T) {
	q := []string{"a", "b", "c", "d", "e", "f", "g"}
	one, two := sampleQueue(q, 3, 42), sampleQueue(q, 3, 42)
	if strings.Join(one, ",") != strings.Join(two, ",") || len(one) != 3 {
		t.Fatalf("seed 42 drew %v then %v", one, two)
	}
	for i := 1; i < len(one); i++ {
		if one[i-1] > one[i] {
			t.Errorf("the sample is not in path order: %v", one)
		}
	}
}

// The run's last line is what the runner reads a job's cost from, and it is
// the same number the record carries.
func TestTheRunsLastLineCarriesTheSpendTheRunnerReads(t *testing.T) {
	run := newEnrichRun(enrich.BatchReport{
		Enriched: 2, Calls: 2, ModelCalls: 4, Tokens: 5200, TotalCostUSD: 0.0421,
		StoppedBy: "the call guard (4 calls)",
	}, enrichVerdicts{Active: 1, BelowFloor: 1}, "opus", enrich.DefaultBudget())
	b, err := json.Marshal(run.summary())
	if err != nil {
		t.Fatal(err)
	}
	var back map[string]any
	if err := json.Unmarshal(b, &back); err != nil {
		t.Fatal(err)
	}
	if back["total_cost_usd"] != 0.0421 || back["stopped_by"] != "the call guard (4 calls)" {
		t.Errorf("the summary line reads %s", b)
	}
}

// A flag may lower the operator's line and never raise it.
func TestABudgetFlagLowersTheLineAndNeverRaisesIt(t *testing.T) {
	if got, err := lowerOnly("max-calls", 0, 250); err != nil || got != 250 {
		t.Errorf("no flag gave %d, %v; want the line kept", got, err)
	}
	if got, err := lowerOnly("max-calls", 6, 250); err != nil || got != 6 {
		t.Errorf("--max-calls 6 gave %d, %v; want it lowered", got, err)
	}
	if _, err := lowerOnly("strong-tokens", 2_000_000, 1_000_000); err == nil ||
		!strings.Contains(err.Error(), "not raise") {
		t.Errorf("a flag over the line was accepted: %v", err)
	}
}

// The record lands where the morning note reads it, one line per run.
func TestTheRunIsRecordedForTheMorningNote(t *testing.T) {
	vault := t.TempDir()
	cfg := configOverRules(t, vault, "preference")
	cfg.EngineStateDir = t.TempDir()
	for i := 0; i < 2; i++ {
		if err := appendEnrichRun(cfg, enrichRun{Model: "opus", Tokens: int64(i)}); err != nil {
			t.Fatal(err)
		}
	}
	raw, err := os.ReadFile(filepath.Join(cfg.EngineStateDir, "enrich-runs.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	if n := strings.Count(string(raw), "\n"); n != 2 {
		t.Errorf("%d lines in the record, want one per run", n)
	}
}
