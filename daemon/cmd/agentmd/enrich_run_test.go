package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
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

// The neighbours go into a model's prompt, so what is offered is bounded: never
// the card itself, never a derived class, never a space no background model
// may read — and at most five, with a title and a summary each.
func TestTheNeighboursNeverOfferWhatAModelMayNotRead(t *testing.T) {
	vault := t.TempDir()
	cfg := configOverRules(t, vault, "preference")
	x, err := index.Open(filepath.Join(t.TempDir(), "index.db"), vault, "", false)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { x.Close() })
	put := func(rel, body string) {
		abs := filepath.Join(vault, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(abs, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
		n := note.Note{Rel: rel, Title: enrich.FrontmatterValue(body, "title"), Body: body,
			Captured: time.Now().UTC(), CapturedSource: "mtime"}
		if err := x.Upsert(n, 1, int64(len(body))); err != nil {
			t.Fatal(err)
		}
	}
	card := "---\ntitle: Keep git out of Google Drive\ntags: [git, drive]\n---\n\nDrive corrupts git.\n"
	put("Agent/memory/semantic/keep-git-out-of-drive.md", card)
	put("Agent/memory/procedural/drive-churns-git.md",
		"---\ntitle: Drive churns git index files\nsummary: Drive rewrites .git/index.\n---\n\ngit drive\n")
	put("Agent/memory/mocs/moc-git.md", "---\ntitle: Git drive map\n---\n\ngit drive\n")
	put("Personal/Home/git-drive-passwords.md", "---\ntitle: Git drive secrets\n---\n\ngit drive\n")
	put("Agent/memory/semantic/leaky.md",
		"---\ntitle: Git drive token\nsummary: token ghp_"+strings.Repeat("a", 36)+"\n---\n\ngit drive\n")
	for i := 0; i < 8; i++ {
		put(fmt.Sprintf("Agent/memory/semantic/git-drive-%d.md", i),
			fmt.Sprintf("---\ntitle: Git and Drive note %d\n---\n\ngit drive %d\n", i, i))
	}

	mayRead := func(rel string) bool { return !strings.HasPrefix(rel, "Personal/") }
	got := enrichNeighbours(cfg, x, mayRead)(context.Background(), enrich.Request{
		Rel: "Agent/memory/semantic/keep-git-out-of-drive.md", Raw: card,
	})
	if len(got) == 0 || len(got) > enrich.MaxRelated {
		t.Fatalf("offered %d neighbours, want between 1 and %d", len(got), enrich.MaxRelated)
	}
	for _, n := range got {
		switch {
		case n.Rel == "Agent/memory/semantic/keep-git-out-of-drive.md":
			t.Error("the card was offered as its own neighbour")
		case strings.Contains(n.Rel, "/mocs/"):
			t.Errorf("a derived class was offered: %s", n.Rel)
		case strings.HasPrefix(n.Rel, "Personal/"):
			t.Errorf("a space no model may read was offered: %s", n.Rel)
		case n.ID == "leaky":
			t.Error("a neighbour carrying a credential shape was offered")
		case n.Title == "" || n.Summary == "":
			t.Errorf("a neighbour without a title or summary: %+v", n)
		}
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
	if _, err := lowerOnly("strong-tokens", 4_000_000, 2_000_000); err == nil ||
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

// The refusal record's two halves have to agree: the row the observer writes
// and the gate that reads it back. This walks the round trip through the same
// wiring the command uses, because a row written under one key and looked up
// under another is a record that is written every night and matched by nothing
// — which looks from outside exactly like a record that is working.
func TestARefusedCardIsDeclinedByTheGateThatReadsTheRowBack(t *testing.T) {
	vault := t.TempDir()
	cfg := configOverRules(t, vault, "reference")
	cfg.EngineStateDir = t.TempDir()

	raw := "---\ntype: reference\nstatus: unfiled\n---\n\nA card the judge said no to.\n"
	req := enrich.Request{Rel: "Agent/memory/semantic/a.md", Raw: raw}
	out := enrich.Outcome{Rel: req.Rel, RefusedBy: "grounding"}

	keyer := enrichFingerprint(cfg, nil)
	row, ok := refusalFor(cfg, keyer, req, out, "the proposal asserts what the card does not")
	if !ok {
		t.Fatal("a post-gate rejection produced no row to record")
	}

	refusals, err := enrich.NewRefusals(enrichStateDir(cfg))
	if err != nil {
		t.Fatal(err)
	}
	if err := refusals.Record(row); err != nil {
		t.Fatal(err)
	}

	// The next night: a fresh read of the record, and the gate the command
	// builds from it.
	next, err := enrich.NewRefusals(enrichStateDir(cfg))
	if err != nil {
		t.Fatal(err)
	}
	gate := enrichRefused(cfg, enrichFingerprint(cfg, nil), next)
	err = gate.Check(context.Background(), req, raw)
	if !errors.Is(err, enrich.ErrNotEligible) {
		t.Fatalf("the card was offered again after being refused: %v", err)
	}
	if n := next.Open(enrich.PassVersion, currentRulesHash(cfg), enrich.GatesVersion); n != 1 {
		t.Errorf("open = %d, want 1 — the standing count is what the morning note reads", n)
	}

	// And the record lands beside the run record, where a person debugging a
	// night reads the two together.
	if got, want := next.Path(),
		filepath.Join(cfg.EngineStateDir, enrich.RefusalsName); got != want {
		t.Errorf("the refusal record is at %s, want %s", got, want)
	}
}

// A rejection that named no claim is a fact about the judge rather than about
// the card. Recording it would blacklist every card a lapsed login touched.
func TestAJudgeThatCouldNotAnswerLeavesNoRow(t *testing.T) {
	cfg := configOverRules(t, t.TempDir(), "reference")
	req := enrich.Request{Rel: "a.md", Raw: "raw"}
	if _, ok := refusalFor(cfg, enrichFingerprint(cfg, nil), req,
		enrich.Outcome{Rel: "a.md"}, "the faithfulness judge could not answer"); ok {
		t.Error("a judge that could not answer was recorded as a refusal of the card")
	}
}

// The run record carries both numbers, because the morning note reads one file.
func TestTheRunRecordCarriesTheRefusalNumbers(t *testing.T) {
	vault := t.TempDir()
	cfg := configOverRules(t, vault, "reference")
	cfg.EngineStateDir = t.TempDir()
	run := newEnrichRun(enrich.BatchReport{Considered: 10, Skipped: 3, Refused: 2},
		enrichVerdicts{}, "opus", enrich.Budget{})
	run.RefusalsOpen = 19
	if err := appendEnrichRun(cfg, run); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(enrichRunsPath(cfg))
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	if got["refused"] != float64(2) || got["refusals_open"] != float64(19) {
		t.Errorf("the record carries refused=%v open=%v, want 2 and 19",
			got["refused"], got["refusals_open"])
	}
}
