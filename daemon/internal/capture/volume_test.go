package capture

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// newCappedHarness is newHarness with the vault's own contract naming a
// daily write cap — the shipped default with one threshold line added, read
// through the same holder the daemon uses, so the gate is exercised the way
// an operator's edit would exercise it.
func newCappedHarness(t *testing.T, cap int) *Capturer {
	t.Helper()
	dir := t.TempDir()
	vault := filepath.Join(dir, "vault")
	for _, d := range []string{"memory", "standards"} {
		if err := os.MkdirAll(filepath.Join(vault, d), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	// The shipped default already names a cap; the vault's own copy names
	// this test's. One key, replaced — a second `daily_write_cap` line would
	// be the duplicate-key YAML error the contract parser rightly refuses.
	capLine := regexp.MustCompile(`(?m)^[ \t]*daily_write_cap:.*$`)
	text := rules.Default()
	if !capLine.MatchString(text) {
		t.Fatal("the shipped contract no longer names daily_write_cap")
	}
	text = capLine.ReplaceAllString(text, fmt.Sprintf("  daily_write_cap: %d", cap))
	if err := os.WriteFile(filepath.Join(vault, "standards", "storage-rules.md"), []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	holder := rules.NewHolder(vault, time.Now())
	contract, err := holder.Get()
	if err != nil {
		t.Fatalf("the capped contract does not resolve: %v", err)
	}
	if got := contract.Thresholds["daily_write_cap"]; got != float64(cap) {
		t.Fatalf("the contract read %v for the cap, want %d", got, cap)
	}
	cfg := &config.Config{
		VaultPath: vault,
		IndexPath: filepath.Join(dir, "index.db"),
		Rules:     holder,
		Spaces:    map[string]string{"memory": "memory"},
		Shard:     "date",
	}
	idx, err := index.Open(cfg.IndexPath, cfg.VaultPath, cfg.MemoryRoot, cfg.DecayEnabled)
	if err != nil {
		t.Fatalf("index.Open: %v", err)
	}
	t.Cleanup(func() { idx.Close() })
	return New(cfg, idx)
}

// A synthetic flood is caught at the door: the cap's worth lands, the next
// capture is refused with a message naming the count, the cap and the edit
// that raises it, and the corpus holds exactly the cap.
func TestTheVolumeGateRefusesTheFloodAtTheDoor(t *testing.T) {
	cp := newCappedHarness(t, 3)
	for i := 0; i < 3; i++ {
		if _, err := cp.Do(Request{Text: fmt.Sprintf("thought number %d, distinct enough", i)}); err != nil {
			t.Fatalf("capture %d under the cap: %v", i, err)
		}
	}
	_, err := cp.Do(Request{Text: "one too many"})
	if err == nil {
		t.Fatal("the fourth capture must be refused")
	}
	for _, want := range []string{"capture refused", "3 memories already written today", "daily cap is 3", "thresholds.daily_write_cap"} {
		if !strings.Contains(err.Error(), want) {
			t.Fatalf("refusal must say %q; got %q", want, err.Error())
		}
	}
	if cp.RefusedByVolume() != 1 {
		t.Fatalf("one refusal counted, got %d", cp.RefusedByVolume())
	}
	n, err := cp.idx.CapturedSince(dayStart(time.Now()), "memory/")
	if err != nil || n != 3 {
		t.Fatalf("the corpus holds exactly the cap: n=%d err=%v", n, err)
	}
}

func TestZeroDisablesTheVolumeGate(t *testing.T) {
	cp := newCappedHarness(t, 0)
	for i := 0; i < 5; i++ {
		if _, err := cp.Do(Request{Text: fmt.Sprintf("thought number %d, distinct enough", i)}); err != nil {
			t.Fatalf("with the gate disabled capture %d failed: %v", i, err)
		}
	}
	if cp.RefusedByVolume() != 0 {
		t.Fatalf("nothing may be refused with the gate disabled, got %d", cp.RefusedByVolume())
	}
}

func TestAContractWithoutACapUsesTheDefault(t *testing.T) {
	holder := newHarness(t).cfg.Rules
	contract, err := holder.Get()
	if err != nil {
		t.Fatal(err)
	}
	delete(contract.Thresholds, "daily_write_cap")
	if got := dailyWriteCap(contract, nil); got != DefaultDailyWriteCap {
		t.Fatalf("absent threshold: %d, want the default %d", got, DefaultDailyWriteCap)
	}
	if got := dailyWriteCap(nil, errHalted); got != DefaultDailyWriteCap {
		t.Fatalf("a halted contract still gates: %d", got)
	}
}
