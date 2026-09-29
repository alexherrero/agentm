package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/dreaming"
	"github.com/alexherrero/agentm/daemon/internal/restated"
)

func restatedFixture(t *testing.T) (*config.Config, []restated.Note) {
	t.Helper()
	vault := filepath.Join(t.TempDir(), "vault")
	sem := filepath.Join(vault, "agent", "memory", "semantic")
	if err := os.MkdirAll(sem, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(vault, ".obsidian"), 0o755); err != nil {
		t.Fatal(err)
	}
	notes := map[string]string{
		"tests-are-sacred.md": "---\ntitle: Tests are sacred\ntype: convention\nimportance: 9\nstatus: active\n" +
			"lifecycle: active\ncreated: 2026-05-19\nslug: tests-are-sacred\n---\n\nTests check real behaviour.\n",
		"never-edit-a-failing-test.md": "---\ntitle: Never edit a failing test\ntype: convention\nimportance: 7\n" +
			"status: active\nlifecycle: active\ncreated: 2026-08-21\nslug: never-edit-a-failing-test\n---\n\n" +
			"A failing test is information: fix the code, never the test.\n",
		"plan-md-shape.md": "---\ntitle: PLAN.md shape\ntype: convention\nstatus: active\nlifecycle: active\n" +
			"created: 2026-06-01\n---\n\nA plan ends with its locked design calls.\n",
		"status-report-shape.md": "---\ntitle: Status report shape\ntype: convention\nstatus: active\n" +
			"lifecycle: active\ncreated: 2026-06-02\n---\n\nA status report leads with the roadmap item.\n",
	}
	for name, text := range notes {
		if err := os.WriteFile(filepath.Join(sem, name), []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	cfg := &config.Config{VaultPath: vault, MemoryRoot: "agent", EngineStateDir: filepath.Join(t.TempDir(), "state")}
	got, err := restated.Gather(vault, "agent")
	if err != nil {
		t.Fatal(err)
	}
	return cfg, got
}

func byTitle(notes []restated.Note, title string) restated.Note {
	for _, n := range notes {
		if n.Title == title {
			return n
		}
	}
	return restated.Note{}
}

// A pair judged the same merges, through the journal; a similar pair judged
// different is left alone (task 178, step 8).
func TestARestatedPairMergesAndADifferentPairStays(t *testing.T) {
	cfg, notes := restatedFixture(t)
	same := restated.Pair{A: byTitle(notes, "Never edit a failing test"), B: byTitle(notes, "Tests are sacred"), Similarity: 0.794}
	diff := restated.Pair{A: byTitle(notes, "PLAN.md shape"), B: byTitle(notes, "Status report shape"), Similarity: 0.889}
	run := restatedRun{Pairs: []restatedRow{
		{A: diff.A.MemoryRel, B: diff.B.MemoryRel, Similarity: diff.Similarity, Verdict: "different"},
		{A: same.A.MemoryRel, B: same.B.MemoryRel, Similarity: same.Similarity, Verdict: "same", Reason: "both forbid it"},
	}}
	before := map[string]string{}
	for _, n := range notes {
		before[n.MemoryRel] = n.Raw
	}
	if err := mergeRestated(cfg, []restated.Pair{diff, same}, &run, time.Date(2026, 9, 29, 3, 0, 0, 0, time.UTC)); err != nil {
		t.Fatal(err)
	}
	if run.Merged != 1 || !run.Pairs[1].Merged || run.Pairs[0].Merged || run.RunID == "" {
		t.Fatalf("run %+v", run)
	}
	if run.Pairs[1].Survivor != "memory/semantic/tests-are-sacred.md" ||
		run.Pairs[1].Folded != "memory/semantic/never-edit-a-failing-test.md" {
		t.Errorf("the merged row names survivor %q, folded %q", run.Pairs[1].Survivor, run.Pairs[1].Folded)
	}
	root := filepath.Join(cfg.VaultPath, "agent")
	read := func(rel string) string {
		raw, _ := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
		return string(raw)
	}
	survivor := read("memory/semantic/tests-are-sacred.md")
	for _, want := range []string{"title: Tests are sacred\n", "importance: 9\n",
		"A failing test is information: fix the code, never the test.",
		"supersedes: memory/semantic/never-edit-a-failing-test.md\n"} {
		if !strings.Contains(survivor, want) {
			t.Errorf("the survivor lacks %q:\n%s", want, survivor)
		}
	}
	if newer := read("memory/semantic/never-edit-a-failing-test.md"); !strings.Contains(newer, "lifecycle: superseded\n") ||
		!strings.Contains(newer, "superseded_by: memory/semantic/tests-are-sacred.md\n") {
		t.Errorf("the newer note is not superseded:\n%s", newer)
	}
	for _, rel := range []string{"memory/semantic/plan-md-shape.md", "memory/semantic/status-report-shape.md"} {
		if read(rel) != before[rel] {
			t.Errorf("%s was changed although the judge called the pair different", rel)
		}
	}
	journal, _ := dreaming.OpenJournal(cfg.EngineStateDir)
	entries, _ := journal.Read()
	applied := 0
	for _, e := range entries {
		if e.Kind == dreaming.KindApplied && e.Job == "restated" {
			applied++
		}
	}
	if applied != 2 {
		t.Errorf("%d restated acts journaled applied, want 2", applied)
	}
}

// A note is in one merge a run: the second "same" pair that names a note the
// first already merged waits for the next night.
func TestANoteMergesOnceARun(t *testing.T) {
	cfg, notes := restatedFixture(t)
	a, b, c := byTitle(notes, "Tests are sacred"), byTitle(notes, "Never edit a failing test"), byTitle(notes, "PLAN.md shape")
	pairs := []restated.Pair{{A: a, B: b, Similarity: 0.9}, {A: a, B: c, Similarity: 0.8}}
	run := restatedRun{Pairs: []restatedRow{
		{A: a.MemoryRel, B: b.MemoryRel, Verdict: "same"}, {A: a.MemoryRel, B: c.MemoryRel, Verdict: "same"}}}
	if err := mergeRestated(cfg, pairs, &run, time.Now()); err != nil {
		t.Fatal(err)
	}
	if run.Merged != 1 || !run.Pairs[0].Merged || run.Pairs[1].Merged {
		t.Errorf("run %+v", run)
	}
}
