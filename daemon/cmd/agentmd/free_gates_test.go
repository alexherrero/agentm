package main

import (
	"context"
	"errors"
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/enrich"
)

// The wiring, not the gate. `enrich.SelfProbe` refuses a synthetic note on its
// own merits and has its own tests; what this asserts is that the shipped pass
// and the tier audit actually run it, which is the half that was missing when
// the nightly enrichment rewrote the daemon's self-probe card on 2026-09-17.
//
// It goes through `freeGates` rather than `attachPreGates` because that is the
// set both callers share: the pass registers it (main.go), and the tier audit
// draws its sample through the same slice, so a probe is refused in both places
// or in neither.
func TestFreeGatesRefuseTheDaemonsSelfProbe(t *testing.T) {
	cfg := configOverRules(t, t.TempDir(), "reference")
	cfg.MemoryRoot = "agent"

	probe := "---\ntitle: AgentM self-probe 2026-09-17T06:11:19Z\ntype: reference\n" +
		"status: active\nprobe: self-probe\n---\n\nSynthetic round-trip probe.\n"
	req := enrich.Request{Rel: "agent/memory/semantic/agentm-self-probe-2026-09-17t06-11-19z.md"}

	var refusedBy string
	for _, g := range freeGates(cfg) {
		if err := g.Check(context.Background(), req, probe); err != nil {
			if !errors.Is(err, enrich.ErrNotEligible) {
				t.Fatalf("%s failed rather than refusing: %v", g.Name(), err)
			}
			refusedBy = g.Name()
			break
		}
	}
	if refusedBy != (&enrich.SelfProbe{}).Name() {
		t.Fatalf("the free gates let the probe through to a model call "+
			"(refused by %q)", refusedBy)
	}
}

// And the same gates leave an ordinary card alone, so the refusal above is the
// marker's doing rather than a set of gates that refuses everything.
func TestFreeGatesStillOfferAnOrdinaryCard(t *testing.T) {
	cfg := configOverRules(t, t.TempDir(), "reference")
	cfg.MemoryRoot = "agent"

	card := "---\ntitle: A thought\ntype: reference\nstatus: unfiled\n---\n\nkeep this\n"
	req := enrich.Request{Rel: "agent/memory/semantic/a-thought.md"}
	for _, g := range freeGates(cfg) {
		if err := g.Check(context.Background(), req, card); err != nil {
			t.Fatalf("%s refused an ordinary card: %v", g.Name(), err)
		}
	}
}
