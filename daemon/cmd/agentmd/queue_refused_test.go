package main

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/enrich"
)

const (
	refusedCard = "memory/semantic/refused.md"
	waitingCard = "memory/semantic/waiting.md"
)

func queueCard(captured string) string {
	return "---\nstatus: unfiled\ncaptured: " + captured + "\n---\nA note awaiting a first judgment.\n"
}

// One card stands refused at the current pass and one genuinely waits. The
// alert's age comes from the waiting card, and the refused one is counted on
// its own (#793).
func TestARefusedCardLeavesTheQueueAgePopulation(t *testing.T) {
	vault := t.TempDir()
	cfg := configOverRules(t, vault, "fact")
	cfg.EngineStateDir = t.TempDir()
	x := openRecordIndex(t, vault)
	putNote(t, x, vault, refusedCard, queueCard("2026-09-26T09:00:00Z"))
	putNote(t, x, vault, waitingCard, queueCard("2026-10-22T09:00:00Z"))

	refusals, err := enrich.NewRefusals(enrichStateDir(cfg))
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(vault, refusedCard))
	if err != nil {
		t.Fatal(err)
	}
	keyer := enrichFingerprint(cfg, nil)
	if err := refusals.Record(enrich.Refusal{Rel: refusedCard, Gate: "faithfulness",
		Key: keyer.Key(string(raw)), Version: keyer.Version, RulesHash: keyer.RulesHash,
		Gates: enrich.GatesVersion, Reason: "a claim the note does not support"}); err != nil {
		t.Fatal(err)
	}

	baseline := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	got, ok := splitRefused(cfg, x, baseline)
	if !ok {
		t.Fatal("no split, with a card standing refused")
	}
	if got.Refused != 1 || got.Unfiled != 1 || got.Since != 1 {
		t.Fatalf("split = %+v, want 1 refused and 1 waiting", got)
	}
	if want := time.Date(2026, 10, 22, 9, 0, 0, 0, time.UTC); !got.OldestSince.Equal(want) {
		t.Errorf("the age comes from %s, want the waiting card's %s", got.OldestSince, want)
	}
	if want := time.Date(2026, 9, 26, 9, 0, 0, 0, time.UTC); !got.OldestRefused.Equal(want) {
		t.Errorf("oldest refused = %s, want %s", got.OldestRefused, want)
	}

	// Its text changes: the refusal no longer matches, and the card rejoins.
	putNote(t, x, vault, refusedCard, queueCard("2026-09-26T09:00:00Z")+"\nAn edit.\n")
	got, ok = splitRefused(cfg, x, baseline)
	if ok || got.Refused != 0 {
		t.Fatalf("after an edit: %+v (split %v), want the card back in the queue", got, ok)
	}
}

// A refusal under an older gates version is a card the night will offer again,
// so it is waiting, not refused.
func TestARefusalUnderAnOlderGateRejoins(t *testing.T) {
	vault := t.TempDir()
	cfg := configOverRules(t, vault, "fact")
	cfg.EngineStateDir = t.TempDir()
	x := openRecordIndex(t, vault)
	putNote(t, x, vault, refusedCard, queueCard("2026-09-26T09:00:00Z"))
	raw, _ := os.ReadFile(filepath.Join(vault, refusedCard))
	refusals, _ := enrich.NewRefusals(enrichStateDir(cfg))
	keyer := enrichFingerprint(cfg, nil)
	if err := refusals.Record(enrich.Refusal{Rel: refusedCard, Gate: "faithfulness",
		Key: keyer.Key(string(raw)), Gates: "gates/0"}); err != nil {
		t.Fatal(err)
	}
	if got, ok := splitRefused(cfg, x, time.Time{}); ok || got.Refused != 0 {
		t.Fatalf("split = %+v (%v), want no refused card", got, ok)
	}
}
