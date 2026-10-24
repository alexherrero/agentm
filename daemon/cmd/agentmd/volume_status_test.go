package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/capture"
	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/health"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// Captures driven past the daily cap are counted, and the count reaches the
// health JSON and the status text (#813). The counter existed and nothing read
// it, so a capture the volume gate dropped left no trace an operator could see.
func TestVolumeRefusalsReachTheHealthJSONAndTheStatusText(t *testing.T) {
	t.Setenv("AGENTM_STORAGE_RULES", "")
	os.Unsetenv("AGENTM_STORAGE_RULES")
	dir := t.TempDir()
	vault := filepath.Join(dir, "vault")
	for _, d := range []string{"memory", "standards"} {
		if err := os.MkdirAll(filepath.Join(vault, d), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	text := regexp.MustCompile(`(?m)^[ \t]*daily_write_cap:.*$`).
		ReplaceAllString(rules.Default(), "  daily_write_cap: 2")
	if err := os.WriteFile(filepath.Join(vault, "standards", "storage-rules.md"), []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := &config.Config{
		VaultPath: vault, IndexPath: filepath.Join(dir, "index.db"),
		Rules:  rules.NewHolder(vault, time.Now()),
		Spaces: map[string]string{"memory": "memory"}, Shard: "date",
	}
	idx, err := index.Open(cfg.IndexPath, cfg.VaultPath, cfg.MemoryRoot, cfg.DecayEnabled)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { idx.Close() })
	cp := capture.New(cfg, idx)
	for i := 0; i < 3; i++ {
		_, _ = cp.Do(capture.Request{Text: fmt.Sprintf("thought number %d, distinct enough", i)})
	}

	c := contractHealth(cfg, cp)
	if c.RefusedByVolume != 1 {
		t.Fatalf("refused by volume = %d, want 1 (two under a cap of 2, the third turned away)", c.RefusedByVolume)
	}
	rep := health.Evaluate(health.Input{Now: time.Now(), GitAvailable: true, Contract: c})
	blob, err := json.Marshal(rep)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(blob), `"refused_by_volume":1`) {
		t.Errorf("the health JSON does not carry the count:\n%s", blob)
	}
	if out := renderReport(rep, cfg); !strings.Contains(out, "1 over the daily write cap") {
		t.Errorf("the status text does not show the count:\n%s", out)
	}
}

// The captures line comes after the filing line's own explanations, so a
// broken contract's remedy still reads under `filing`.
func TestTheCapturesLineFollowsTheFilingHints(t *testing.T) {
	rep := health.Evaluate(health.Input{Now: time.Now(), GitAvailable: true,
		Contract: health.Contract{State: health.ContractBroken, Detail: "bad yaml"}})
	out := renderReport(rep, &config.Config{VaultPath: "/v"})
	filing := strings.Index(out, "  filing   ")
	hint := strings.Index(out, "fix the block and the next health pass")
	captures := strings.Index(out, "  captures refused since boot")
	if filing < 0 || hint < 0 || captures < 0 || !(filing < hint && hint < captures) {
		t.Fatalf("want filing, then its hint, then captures:\n%s", out)
	}
}
