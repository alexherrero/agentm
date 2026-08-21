package enrich

import (
	"context"
	"errors"
	"math"
	"strings"
	"testing"
)

// A fake embedder with a stated geometry.
//
// Hand-built vectors rather than a real model, because the property under test
// is the arithmetic and the decision rule — not whether an embedding model
// represents prose well. A test that stood up the real embedder would be slow,
// non-deterministic, and would pass or fail for reasons that have nothing to do
// with this code.
func geometry(m map[string][]float32) Embedder {
	return func(_ context.Context, texts []string) ([][]float32, error) {
		out := make([][]float32, len(texts))
		for i, t := range texts {
			v, ok := m[t]
			if !ok {
				return nil, errors.New("no vector for " + t)
			}
			out[i] = v
		}
		return out, nil
	}
}

// The failure this exists to catch: three notes that pointed in different
// directions come back pointing in nearly the same one.
func TestHomogenizationIsDetected(t *testing.T) {
	e := geometry(map[string][]float32{
		// Sources: mutually orthogonal — as different as three notes can be.
		"s1": {1, 0, 0}, "s2": {0, 1, 0}, "s3": {0, 0, 1},
		// Rewrites: nearly collinear — the pass flattened them.
		"r1": {1, 0.9, 0.9}, "r2": {0.9, 1, 0.9}, "r3": {0.9, 0.9, 1},
	})
	d, err := Measure(context.Background(), e,
		[]string{"a.md", "b.md", "c.md"},
		[]string{"s1", "s2", "s3"}, []string{"r1", "r2", "r3"})
	if err != nil {
		t.Fatalf("Measure: %v", err)
	}
	if d.SourceConvergence >= 0.01 {
		t.Errorf("orthogonal sources measured %v convergence", d.SourceConvergence)
	}
	if d.RewriteConvergence <= 0.9 {
		t.Errorf("near-collinear rewrites measured %v convergence",
			d.RewriteConvergence)
	}
	if !d.Homogenized() {
		t.Errorf("a batch that collapsed three orthogonal notes onto one line was "+
			"not flagged: delta %v against a %v ceiling", d.Delta, ConvergenceCeiling)
	}
}

// And the case that must not fire: a real enrichment changes every note
// substantially without making them more alike.
//
// The first version of this fixture used mutually orthogonal sources and failed,
// correctly. Orthogonal is *maximum* dispersion — from there every possible
// rewrite either holds the angle or closes it, so delta can only be >= 0 and a
// fixture built that way can never demonstrate "changed without converging". See
// the note on Measure: the metric is only two-sided on a corpus whose notes
// already overlap, which every real one does.
//
// So the sources here share structure, the way real notes do, and each rewrite
// gains a dimension nobody else uses — which is exactly what a good enrichment
// does when it makes a note more specific rather than more generic.
func TestAFaithfulRewriteIsNotFlagged(t *testing.T) {
	e := geometry(map[string][]float32{
		"s1": {1, 0.5, 0, 0, 0, 0},
		"s2": {0.5, 1, 0, 0, 0, 0},
		"s3": {0, 0.5, 1, 0, 0, 0},
		"r1": {1, 0.5, 0, 1, 0, 0},
		"r2": {0.5, 1, 0, 0, 1, 0},
		"r3": {0, 0.5, 1, 0, 0, 1},
	})
	d, err := Measure(context.Background(), e,
		[]string{"a.md", "b.md", "c.md"},
		[]string{"s1", "s2", "s3"}, []string{"r1", "r2", "r3"})
	if err != nil {
		t.Fatalf("Measure: %v", err)
	}
	if d.Homogenized() {
		t.Errorf("a batch that changed every note without flattening them was "+
			"flagged: %s", d.Report())
	}
	// The notes did change — otherwise this proves nothing about the test's
	// ability to tell change from convergence.
	if d.MeanFidelity > 0.9 {
		t.Fatalf("the fixture barely changed the notes (fidelity %v), so it cannot "+
			"distinguish a faithful rewrite from an untouched one", d.MeanFidelity)
	}
	// And dispersion went the right way: adding something private to each note
	// spreads them apart rather than together.
	if d.Delta >= 0 {
		t.Errorf("notes that each gained a unique dimension did not become more "+
			"distinct: delta %+.4f", d.Delta)
	}
}

// The corpus number cannot say *which* note went wrong. Fidelity can, and it
// reports the worst one by name — a mean with no example is a number nobody can
// act on.
func TestTheWorstNoteIsNamed(t *testing.T) {
	e := geometry(map[string][]float32{
		"s1": {1, 0, 0}, "s2": {0, 1, 0}, "s3": {0, 0, 1},
		"r1": {1, 0, 0},     // untouched
		"r2": {0, 1, 0},     // untouched
		"r3": {0, 0.2, 0.1}, // rewritten into something else entirely
	})
	d, err := Measure(context.Background(), e,
		[]string{"a.md", "b.md", "wandered.md"},
		[]string{"s1", "s2", "s3"}, []string{"r1", "r2", "r3"})
	if err != nil {
		t.Fatal(err)
	}
	if d.LeastFaithful != "wandered.md" {
		t.Errorf("the least faithful rewrite was reported as %q, want wandered.md",
			d.LeastFaithful)
	}
	if d.MinFidelity >= FidelityFloor {
		t.Errorf("a note rewritten into something else scored %v, above the %v "+
			"floor", d.MinFidelity, FidelityFloor)
	}
	if len(d.BelowFloor) != 1 || d.BelowFloor[0] != "wandered.md" {
		t.Errorf("BelowFloor = %v, want just wandered.md", d.BelowFloor)
	}
}

// One note has nothing to be similar to, so convergence is undefined — but
// fidelity is not, and an earlier version threw it away by returning early on
// both. The negative pass caught that: the quantities have different
// requirements and are computed separately now.
func TestASingleNoteStillReportsFidelity(t *testing.T) {
	e := geometry(map[string][]float32{"s": {1, 0}, "r": {0.6, 0.8}})
	d, err := Measure(context.Background(), e, []string{"a.md"},
		[]string{"s"}, []string{"r"})
	if err != nil {
		t.Fatalf("a one-note batch was an error: %v", err)
	}
	if d.Homogenized() {
		t.Error("a one-note batch was flagged as homogenized")
	}
	if d.SourceConvergence != 0 || d.RewriteConvergence != 0 {
		t.Errorf("convergence was computed for a single note: %+v", d)
	}
	// cos({1,0}, {0.6,0.8}) = 0.6, and it must be reported rather than dropped.
	// The tolerance is float32-sized because the vectors are: 0.8 is not exactly
	// representable, so the product lands about 1e-8 off and a 1e-9 bound would
	// be asserting the float width rather than the arithmetic.
	if math.Abs(d.MeanFidelity-0.6) > 1e-6 {
		t.Errorf("fidelity = %v, want 0.6 — a one-note batch still has one",
			d.MeanFidelity)
	}
	if d.LeastFaithful != "a.md" {
		t.Errorf("the single note was not named: %q", d.LeastFaithful)
	}
	rep := d.Report()
	if !strings.Contains(rep, "undefined") {
		t.Errorf("the report does not say convergence is undefined: %s", rep)
	}
	if !strings.Contains(rep, "fidelity") {
		t.Errorf("the report drops the number it does have: %s", rep)
	}
}

// An empty batch measures nothing and says so, without calling the embedder.
func TestAnEmptyBatchCallsNothing(t *testing.T) {
	called := false
	e := func(context.Context, []string) ([][]float32, error) {
		called = true
		return nil, nil
	}
	d, err := Measure(context.Background(), e, nil, nil, nil)
	if err != nil {
		t.Fatalf("an empty batch was an error: %v", err)
	}
	if called {
		t.Error("an empty batch called the embedder")
	}
	if !strings.Contains(d.Report(), "nothing was rewritten") {
		t.Errorf("the report does not say the batch was empty: %s", d.Report())
	}
}

// Mismatched inputs are an error rather than a silently-truncated measurement.
// A dispersion computed over the wrong pairs would read as a finding.
func TestMismatchedInputsAreRefused(t *testing.T) {
	e := geometry(map[string][]float32{"s1": {1, 0}, "s2": {0, 1}, "r1": {1, 0}})
	if _, err := Measure(context.Background(), e,
		[]string{"a.md", "b.md"}, []string{"s1", "s2"}, []string{"r1"}); err == nil {
		t.Error("two sources and one rewrite were measured against each other")
	}
	if _, err := Measure(context.Background(), e,
		[]string{"a.md"}, []string{"s1", "s2"}, []string{"r1", "r1"}); err == nil {
		t.Error("one path for two notes was accepted")
	}
}

// An embedder that returns the wrong number of vectors is a broken embedder, and
// pairing the ones it did return against the wrong notes would produce a
// confident, meaningless number.
func TestAShortEmbedderResponseIsRefused(t *testing.T) {
	short := func(_ context.Context, texts []string) ([][]float32, error) {
		return [][]float32{{1, 0}}, nil
	}
	if _, err := Measure(context.Background(), short,
		[]string{"a.md", "b.md"}, []string{"s1", "s2"}, []string{"r1", "r2"}); err == nil {
		t.Error("an embedder that returned one vector for two notes was trusted")
	}
}

func TestAnEmbedderFailureIsReported(t *testing.T) {
	boom := func(context.Context, []string) ([][]float32, error) {
		return nil, errors.New("embedder is cold")
	}
	_, err := Measure(context.Background(), boom, []string{"a.md", "b.md"},
		[]string{"s1", "s2"}, []string{"r1", "r2"})
	if err == nil {
		t.Fatal("a failed embedding produced a measurement")
	}
	if !strings.Contains(err.Error(), "cold") {
		t.Errorf("the error loses why: %v", err)
	}
}

// The arithmetic itself, against values anyone can check by hand.
func TestCosineIsTheCosine(t *testing.T) {
	for _, tc := range []struct {
		name string
		a, b []float32
		want float64
	}{
		{"identical", []float32{1, 0}, []float32{1, 0}, 1},
		{"orthogonal", []float32{1, 0}, []float32{0, 1}, 0},
		{"opposite", []float32{1, 0}, []float32{-1, 0}, -1},
		{"forty-five degrees", []float32{1, 0}, []float32{1, 1}, math.Sqrt2 / 2},
		// Unnormalized inputs give the same answer as normalized ones, because
		// this normalizes rather than assuming. A silently-wrong similarity is
		// the kind of bug that reads as a finding.
		{"scale invariant", []float32{3, 0}, []float32{7, 0}, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := cosine(tc.a, tc.b); math.Abs(got-tc.want) > 1e-9 {
				t.Errorf("cosine(%v, %v) = %v, want %v", tc.a, tc.b, got, tc.want)
			}
		})
	}
	if got := cosine([]float32{1, 0}, []float32{1, 0, 0}); got != 0 {
		t.Errorf("mismatched dimensions gave %v, want 0", got)
	}
	if got := cosine([]float32{0, 0}, []float32{1, 0}); got != 0 {
		t.Errorf("a zero vector gave %v, want 0", got)
	}
}

// Every distinct pair, not a sample: at these batch sizes the full computation
// is a few thousand dot products, and sampling would put noise into the one
// number the decision turns on.
func TestMeanPairwiseCoversEveryPair(t *testing.T) {
	// Four vectors, six pairs. Three pairs at cosine 1 and three at 0 averages
	// to 0.5 — a value that is wrong under any sampling.
	vs := [][]float32{{1, 0}, {1, 0}, {0, 1}, {0, 1}}
	if got := meanPairwise(vs); math.Abs(got-1.0/3.0) > 1e-9 {
		t.Errorf("meanPairwise = %v, want 1/3 (two pairs at 1, four at 0, over "+
			"six pairs)", got)
	}
	if got := meanPairwise([][]float32{{1, 0}}); got != 0 {
		t.Errorf("a single vector has no pairs but measured %v", got)
	}
}

// The report is what a person reads, so it has to carry the numbers and the
// verdict rather than one or the other.
func TestTheReportCarriesTheNumbersAndTheVerdict(t *testing.T) {
	e := geometry(map[string][]float32{
		"s1": {1, 0, 0}, "s2": {0, 1, 0},
		"r1": {1, 0.95, 0}, "r2": {0.95, 1, 0},
	})
	d, err := Measure(context.Background(), e, []string{"a.md", "b.md"},
		[]string{"s1", "s2"}, []string{"r1", "r2"})
	if err != nil {
		t.Fatal(err)
	}
	rep := d.Report()
	for _, want := range []string{"convergence", "fidelity", "delta", "ceiling"} {
		if !strings.Contains(rep, want) {
			t.Errorf("the report omits %q:\n%s", want, rep)
		}
	}
	if d.Homogenized() && !strings.Contains(rep, "HOMOGENIZED") {
		t.Errorf("a homogenized batch does not say so:\n%s", rep)
	}
}
