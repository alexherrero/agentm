package main

import (
	"context"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/ledger"
)

// #785, through the shipped wiring: the fingerprint gate and the observer the
// nightly command attaches, over the shipped ledger.
//
// A note this pass already judged is skipped free on one night. The skip must
// not cost it its done row, or the next night offers it again and pays for the
// same judgment — the pass has no model caller here, so a second night that
// reached the call would spend one and fail.
func TestAFingerprintSkipLeavesTheNoteSeenTheNextNight(t *testing.T) {
	ctx := context.Background()
	vault := t.TempDir()
	cfg := configOverRules(t, vault, "fact")
	led := newTestLedger(t)
	keyer := enrichFingerprint(cfg, nil)

	rel := "memory/semantic/judged.md"
	body := writeNote(t, vault, rel, response("judged", 0.9), enrich.Stamp{
		Version: enrich.PassVersion, RulesHash: currentRulesHash(cfg),
		At: time.Date(2026, 9, 30, 9, 0, 0, 0, time.UTC),
	})
	if err := led.Record(ctx, ledger.Entry{
		Stage: ledger.StageEnrich, Target: rel, Version: enrich.PassVersion,
		RulesHash: currentRulesHash(cfg), InputKey: keyer.Key("the raw capture"),
		OutputKey: keyer.Key(body), Outcome: ledger.Done,
	}); err != nil {
		t.Fatal(err)
	}

	pass := enrich.NewPass(nil, 1)
	pass.SetEnabled(true)
	pass.AddPre(enrichFingerprint(cfg, led))
	pass.SetObserver(enrichObserver(cfg, led, keyer, nil))

	for night := 1; night <= 2; night++ {
		out, err := pass.Run(ctx, enrich.Request{Rel: rel, Raw: body})
		if err != nil || !out.Skipped || out.SkippedBy != "fingerprint" || out.Calls != 0 {
			t.Fatalf("night %d: the judged note was not skipped free: skipped=%v "+
				"by=%q calls=%d err=%v", night, out.Skipped, out.SkippedBy, out.Calls, err)
		}
		row, ok, err := led.Lookup(ctx, ledger.StageEnrich, rel)
		if err != nil || !ok || row.Outcome != ledger.Done {
			t.Fatalf("night %d: the skip replaced the done row: %+v (found %v, %v)",
				night, row, ok, err)
		}
	}

	rep, err := pendingFor(ctx, ledger.StageEnrich, cfg, indexOverVault(t, vault, rel, body), led)
	if err != nil {
		t.Fatal(err)
	}
	if rep.Eligible != 1 || rep.Current != 1 || len(rep.Pending) != 0 {
		t.Errorf("after two skipped nights: eligible %d · current %d · pending %+v; "+
			"want the one note eligible and current", rep.Eligible, rep.Current, rep.Pending)
	}
}
