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
	"github.com/alexherrero/agentm/daemon/internal/extract"
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
// # Two bars, because one of them was measuring the machine
//
// The bar fixed before the first extraction landed was absolute: p95 under 100ms
// over 200 captures. That is the design's own number and it is the right promise
// to make about a deployment. It is the wrong thing to assert on a CI runner.
//
// The Windows runner measured p50 58ms and p95 263ms on this same code, against
// 4.8ms and 5.9ms on the development machine. A twelve-fold gap on the *median*
// is not contention — it is a filesystem where each small-file write costs tens
// of milliseconds. Under that floor no amount of code could reach 100ms, so the
// assertion was reporting the runner rather than the change.
//
// Raising the absolute number to accommodate it would have turned a real gate
// into a rubber stamp. So the gate splits along what it is actually for:
//
//   - **The overhead ratio, enforced everywhere.** Extraction must stay a small
//     share of the transaction it rides in. This is machine-independent, because
//     a slow disk slows the floor and the total together, and it is a direct test
//     of the thing the gate exists to catch: something expensive added to this
//     path.
//   - **The absolute budget, enforced where it can be met.** Measured on every
//     machine and reported on every machine. Enforced only when the irreducible
//     floor leaves room for it — and when it does not, the skip says so loudly
//     rather than passing quietly, because a gate that goes silent on the
//     machines it cannot measure is indistinguishable from one that passes.
//
// The floor took two attempts to specify, and the first one was wrong in a way
// worth recording. It timed a bare `os.WriteFile`, on the assumption that file
// I/O is what a capture spends its time on. A Windows runner then reported a
// 2.1ms floor beside a 226ms capture p95: the file write was fast and the SQLite
// commit was not. A floor that measures the wrong half of the transaction will
// wave through exactly the machine it was meant to excuse, so it now measures a
// real capture of a near-empty note — the same file write, the same index
// commit, and nothing to extract.
const (
	captureBudgetP95 = 100 * time.Millisecond
	captureSamples   = 200

	// Extraction may take at most this share of a capture. Everything added in
	// this part measured under 12% on the development machine; a third is
	// generous headroom that still catches a model call, a network round trip, or
	// a full-corpus scan, none of which fit in any fraction of a file write.
	maxExtractionShare = 0.33

	// When the I/O floor alone eats this much of the budget, the absolute
	// assertion is measuring the disk. Half, because a floor past that leaves the
	// code no room to be judged in.
	floorSkipThreshold = captureBudgetP95 / 2
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

	// The irreducible floor on this machine: the same transaction with nothing to
	// extract. Whatever this costs is what the machine costs, and the code cannot
	// be judged below it.
	floor := measureFloor(t)

	t.Logf("capture over %d samples: p50 %v · p95 %v · max %v · txn floor p95 %v "+
		"(budget p95 < %v)",
		len(samples), p50.Round(time.Microsecond), p95.Round(time.Microsecond),
		worst.Round(time.Microsecond), floor.Round(time.Microsecond), captureBudgetP95)

	if floor >= floorSkipThreshold {
		t.Logf("SKIPPING the absolute budget: the transaction floor alone is %v, past the %v "+
			"point where this assertion measures the machine rather than the code. The "+
			"overhead ratio below still runs, and it is the assertion that catches a "+
			"regression.", floor.Round(time.Millisecond), floorSkipThreshold)
		return
	}
	if p95 >= captureBudgetP95 {
		t.Errorf("capture p95 is %v, past the %v budget on a machine whose transaction "+
			"floor is only %v. Something was added to the capture path that does not "+
			"belong on it — capture writes the file and updates the index, and waits "+
			"on nothing else.", p95, captureBudgetP95, floor.Round(time.Microsecond))
	}
}

// measureFloor times the irreducible part of a capture, so the absolute budget
// can tell a slow machine from a slow change.
//
// A real capture of a near-empty note: the same file write and the same index
// commit an ordinary capture pays, with nothing to extract. Deliberately not a
// bare `os.WriteFile` — that was the first version, and it reported 2.1ms on a
// runner where capture cost 226ms, because the slow half was the database
// commit and the floor never touched it.
func measureFloor(t *testing.T) time.Duration {
	t.Helper()
	cp := newHarness(t)
	samples := make([]time.Duration, 0, 64)
	for i := 0; i < 64; i++ {
		req := Request{Text: "x", Title: fmt.Sprintf("floor %d", i)}
		start := time.Now()
		if _, err := cp.Do(req); err != nil {
			t.Fatalf("floor capture: %v", err)
		}
		samples = append(samples, time.Since(start))
	}
	sort.Slice(samples, func(a, b int) bool { return samples[a] < samples[b] })
	return samples[len(samples)*95/100]
}

// The assertion that runs everywhere, and the one that actually catches a
// regression: extraction must stay a small share of the transaction it rides in.
//
// Machine-independent by construction. A slow disk slows the floor and the total
// together, so the ratio holds where an absolute number does not — and the thing
// the gate exists to catch is something expensive being added to this path,
// which is a statement about proportion rather than about milliseconds.
func TestExtractionStaysASmallShareOfCapture(t *testing.T) {
	if testing.Short() {
		t.Skip("timing test")
	}
	cp := newHarness(t)

	// Warm up both paths so neither pays for first-call setup.
	if _, err := cp.Do(Request{Text: body(0), Title: "warm up"}); err != nil {
		t.Fatal(err)
	}
	_ = extract.Aliases("warm up", body(0))

	var extraction, total time.Duration
	const runs = 100
	for i := 1; i <= runs; i++ {
		title, text := fmt.Sprintf("capture %d", i), body(i)

		start := time.Now()
		_ = extract.Aliases(title, text)
		_ = extract.HeaderChunks(text)
		_ = extract.Links(text)
		_ = extract.Entities(text)
		extraction += time.Since(start)

		start = time.Now()
		if _, err := cp.Do(Request{Text: text, Title: title}); err != nil {
			t.Fatalf("capture %d: %v", i, err)
		}
		total += time.Since(start)
	}

	share := float64(extraction) / float64(total)
	t.Logf("extraction is %.1f%% of capture (%v of %v over %d runs, ceiling %.0f%%)",
		share*100, extraction.Round(time.Microsecond), total.Round(time.Microsecond),
		runs, maxExtractionShare*100)

	if share > maxExtractionShare {
		t.Errorf("extraction is %.1f%% of the capture transaction, past the %.0f%% "+
			"ceiling. Everything on this path is meant to be regex over text the "+
			"caller already handed us; a share this large means something is reading "+
			"the corpus, calling a model, or waiting on a network.",
			share*100, maxExtractionShare*100)
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
