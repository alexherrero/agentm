package capture

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// Capture is the one operation that must never fail for an interesting reason,
// and the budget is what makes that true in practice rather than in intent: it
// writes the file and updates the index in a single synchronous step, offline,
// in under a hundred milliseconds.
//
// This file is the gate on that number, and it exists *before* the extraction
// steps this part adds rather than after them. A measurement taken at the end
// can only report a total; one taken from the start reports which step spent
// what. The property has been broken before — a previous synchronous
// embed-at-save change put a model on this path and had to be reverted — and a
// bar written down in advance is what turns that from a story into a check.
//
// The bar, fixed before the first extraction landed:
//
//	p95 < 100ms over 200 captures against a warm index.
//
// Deliberately not a tighter number. This runs on CI hardware of unknown
// contention, and a bar tuned to a quiet laptop would fail for reasons that have
// nothing to do with the code. It is a regression gate, not a benchmark: what it
// catches is something being added to this path that does not belong on it, and
// anything of that shape costs far more than the headroom here.
const (
	captureBudgetP95 = 100 * time.Millisecond
	captureSamples   = 200
)

// newHarness builds a real Capturer over a scratch vault and a real index. Not a
// mock: the thing being measured is the write plus the index update, so a
// harness that stubbed either would measure nothing.
func newHarness(t testing.TB) *Capturer {
	t.Helper()
	dir := t.TempDir()
	vault := filepath.Join(dir, "vault")
	if err := os.MkdirAll(filepath.Join(vault, "memory"), 0o755); err != nil {
		t.Fatal(err)
	}

	holder := rules.NewHolder("", time.Now())
	if _, err := holder.Get(); err != nil {
		t.Fatalf("the shipped filing contract does not resolve: %v", err)
	}

	cfg := &config.Config{
		VaultPath: vault,
		IndexPath: filepath.Join(dir, "index.db"),
		Rules:     holder,
		Spaces:    map[string]string{"memory": "memory"},
		Shard:     "date",
	}
	idx, err := index.Open(cfg.IndexPath, cfg.VaultPath)
	if err != nil {
		t.Fatalf("index.Open: %v", err)
	}
	t.Cleanup(func() { idx.Close() })

	return New(cfg, idx)
}

// body returns a note of a realistic size — long enough that per-note work is
// visible, short enough to be an ordinary capture rather than a document.
func body(i int) string {
	var b strings.Builder
	fmt.Fprintf(&b, "Capture number %d, written to measure the transaction.\n\n", i)
	b.WriteString("## Context\n\nThe daemon commits whatever git reports dirty, so a " +
		"vault file does not need hand-committing before a gate will pass.\n\n")
	b.WriteString("## Detail\n\nThe index is a cache and the files are truth, which is " +
		"why a drifted index costs a rebuild rather than data.\n")
	return b.String()
}

func TestCaptureStaysUnderBudget(t *testing.T) {
	if testing.Short() {
		t.Skip("timing test")
	}
	cp := newHarness(t)

	// One warm-up capture, excluded. The first one pays for schema migration and
	// page-cache misses that no later capture pays, and folding that into the
	// distribution would measure startup rather than the transaction.
	if _, err := cp.Do(Request{Text: body(0), Title: "warm up"}); err != nil {
		t.Fatalf("warm-up capture: %v", err)
	}

	samples := make([]time.Duration, 0, captureSamples)
	for i := 1; i <= captureSamples; i++ {
		req := Request{Text: body(i), Title: fmt.Sprintf("capture %d", i)}
		start := time.Now()
		if _, err := cp.Do(req); err != nil {
			t.Fatalf("capture %d: %v", i, err)
		}
		samples = append(samples, time.Since(start))
	}

	sort.Slice(samples, func(a, b int) bool { return samples[a] < samples[b] })
	p50 := samples[len(samples)*50/100]
	p95 := samples[len(samples)*95/100]
	worst := samples[len(samples)-1]

	t.Logf("capture over %d samples: p50 %v · p95 %v · max %v (budget p95 < %v)",
		len(samples), p50.Round(time.Microsecond), p95.Round(time.Microsecond),
		worst.Round(time.Microsecond), captureBudgetP95)

	if p95 >= captureBudgetP95 {
		t.Errorf("capture p95 is %v, past the %v budget. Something was added to the "+
			"capture path that does not belong on it — capture writes the file and "+
			"updates the index, and waits on nothing else.", p95, captureBudgetP95)
	}
}

// The budget protects a property, not a number: capture must not wait on
// anything that can be slow or absent. A timing bar alone would pass a capture
// that made a network call on a fast network, so this asserts the shape too.
func TestCaptureDoesNotDependOnTheContract(t *testing.T) {
	cp := newHarness(t)

	// A capture that names no type does not need the taxonomy, and must land even
	// when the contract is unavailable — the state filing drains anyway.
	broken := filepath.Join(t.TempDir(), "broken.md")
	if err := os.WriteFile(broken, []byte("# Rules\n\n```storage-rules\nmemory_types: [\n```\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AGENTM_STORAGE_RULES", broken)
	cp.cfg.Rules = rules.NewHolder("", time.Now())

	res, err := cp.Do(Request{Text: "An ambient capture with no type.", Title: "ambient"})
	if err != nil {
		t.Fatalf("an untyped capture failed while the contract was broken: %v", err)
	}
	if res.Type != "" {
		t.Errorf("type = %q; with no contract there is nothing to default from", res.Type)
	}
	if res.Status != "unfiled" {
		t.Errorf("status = %q, want unfiled", res.Status)
	}
}

func BenchmarkCapture(b *testing.B) {
	cp := newHarness(b)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if _, err := cp.Do(Request{Text: body(i), Title: fmt.Sprintf("bench %d", i)}); err != nil {
			b.Fatal(err)
		}
	}
}
