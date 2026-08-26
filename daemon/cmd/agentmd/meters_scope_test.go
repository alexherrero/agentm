package main

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/index"
)

// Which part of the vault the meters measure.
//
// This is the defect the population filters were added for: the command passed
// `config.EmbedScope`, which is three spaces because the retrieval gold set's
// answers live in `desk` and `external`. Measured live, that made the window 79%
// `_inbox` plus dreaming's own staged proposals. Nothing pinned the choice, so
// reverting one line put it straight back — these tests are what stop that.

func TestTheMetersMeasureTheConfiguredMemorySpace(t *testing.T) {
	cfg := &config.Config{
		Spaces:     map[string]string{"memory": "Agent/memory", "projects": "Agent/desk/projects"},
		EmbedScope: []string{"Agent/memory", "Agent/desk", "Agent/external"},
	}
	got := meterScope(cfg)
	if len(got) != 1 || got[0] != "Agent/memory" {
		t.Fatalf("meterScope = %v, want [Agent/memory] — the memory space, not "+
			"the vector arm's scope", got)
	}
}

func TestTheMetersScopeIsNotTheVectorArmsScope(t *testing.T) {
	// Stated separately and in the negative, because the failure being guarded
	// is specifically "somebody passed EmbedScope again".
	cfg := &config.Config{
		Spaces:     map[string]string{"memory": "Agent/memory"},
		EmbedScope: []string{"Agent/memory", "Agent/desk", "Agent/external"},
	}
	for _, s := range meterScope(cfg) {
		if s == "Agent/desk" || s == "Agent/external" {
			t.Fatalf("meterScope = %v; %q is in the vector arm's scope for a "+
				"retrieval reason and holds no filed memories", meterScope(cfg), s)
		}
	}
}

func TestTheMemorySpaceIsResolvedRatherThanWrittenDown(t *testing.T) {
	// The vault root has moved twice. A literal would resolve to nothing, and an
	// empty scope selects no notes — which reads exactly like a cold index.
	cfg := &config.Config{
		Spaces:     map[string]string{"memory": "SomewhereElse/mem"},
		EmbedScope: []string{"Agent/memory"},
	}
	got := meterScope(cfg)
	if len(got) != 1 || got[0] != "SomewhereElse/mem" {
		t.Fatalf("meterScope = %v, want [SomewhereElse/mem] — read from the "+
			"config, not assumed", got)
	}
}

func TestAnUnconfiguredMemorySpaceFallsBackToTheWiderScope(t *testing.T) {
	// The wrong population, deliberately: a meter reporting numbers over a named
	// wider scope is recoverable, and one reporting nothing looks like a vault
	// with no memories in it.
	cfg := &config.Config{
		Spaces:     map[string]string{"projects": "Agent/desk/projects"},
		EmbedScope: []string{"Agent/memory", "Agent/desk"},
	}
	got := meterScope(cfg)
	if len(got) != 2 {
		t.Fatalf("meterScope = %v, want the EmbedScope fallback", got)
	}
}

func TestABlankMemorySpaceIsNotAScope(t *testing.T) {
	// A configured-but-empty value is the trap: `[]string{""}` becomes a
	// scopeClause that matches nothing, so every meter reads zero and reports it
	// as an absence rather than as a misconfiguration.
	cfg := &config.Config{
		Spaces:     map[string]string{"memory": "   "},
		EmbedScope: []string{"Agent/memory"},
	}
	got := meterScope(cfg)
	if len(got) != 1 || got[0] != "Agent/memory" {
		t.Fatalf("meterScope = %v, want the fallback rather than a blank scope", got)
	}
}

// The wiring, which is a separate claim from the function being right.
//
// `TestTheMetersMeasureTheConfiguredMemorySpace` calls `meterScope` directly, so
// it stays green when `runMeters` stops calling it — exactly the hole task 3's
// breaker wiring test had. These two run the command's own path.

func metersVault(t *testing.T) (*config.Config, *index.Index) {
	t.Helper()
	vault := t.TempDir()
	for rel, status := range map[string]string{
		"Agent/memory/filed-one.md":   "active",
		"Agent/memory/filed-two.md":   "active",
		"Agent/memory/_inbox/clip.md": "active",
		"Agent/desk/briefs/brief.md":  "active",
		"Agent/desk/scratch/r/p.md":   "active",
		"Agent/external/page.md":      "active",
	} {
		p := filepath.Join(vault, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		body := "---\ntitle: t\nstatus: " + status +
			"\ncaptured: 2026-08-01T00:00:00Z\n---\n\n" +
			"Some words about a thing that is written down here.\n"
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	x, err := index.Open(filepath.Join(t.TempDir(), "index.db"), vault, "", false)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { x.Close() })
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}
	cfg := &config.Config{
		VaultPath:  vault,
		Spaces:     map[string]string{"memory": "Agent/memory"},
		EmbedScope: []string{"Agent/memory", "Agent/desk", "Agent/external"},
	}
	return cfg, x
}

func TestRunMetersMeasuresOnlyTheMemorySpace(t *testing.T) {
	cfg, x := metersVault(t)
	rep, err := runMeters(context.Background(), cfg, x, 100, 50, 50, "m")
	if err != nil {
		t.Fatal(err)
	}
	// Two filed memories. Six notes exist; the other four are an inbox clipping,
	// two `desk` files and one in `external` — all `status: active`, so only the
	// scope and the directory rule can keep them out.
	if rep.Sample != 2 {
		t.Fatalf("sample = %d, want 2 filed memories out of six notes", rep.Sample)
	}
}

func TestRunMetersReportsTheScopeItMeasured(t *testing.T) {
	cfg, x := metersVault(t)
	rep, err := runMeters(context.Background(), cfg, x, 100, 50, 50, "m")
	if err != nil {
		t.Fatal(err)
	}
	// The reported scope is what a person reads off the scorecard to know what
	// the numbers describe. Reporting the wide scope while measuring the narrow
	// one — or the reverse — is worse than either, because it is unfalsifiable
	// from the output.
	if rep.Scope != "Agent/memory" {
		t.Fatalf("scope = %q, want %q", rep.Scope, "Agent/memory")
	}
}
