package enrich

import (
	"context"
	"fmt"
	"math"
	"sort"
)

// Measuring whether enrichment made the corpus more alike.
//
// This is the risk the whole part is shaped around, and until now nothing
// measured it. The guards that exist — token preservation, per-note grounding,
// the prompt's instruction not to rewrite good prose — all push against
// homogenization without ever reporting whether they succeeded.
//
// # Why process isolation does not answer this
//
// Every note already gets its own OS process, its own session and its own
// working directory, so nothing from one note can reach another's rewrite. That
// prevents cross-contamination and does nothing at all about homogenization,
// which is a different failure: N notes independently rewritten by the *same*
// prompt toward the *same* target voice converge exactly as hard as N notes
// rewritten together. Isolation arguably makes it marginally worse, since a
// model with no memory of the previous note cannot even try to vary.
//
// So it has to be measured after the fact, on the output.
//
// # The two numbers
//
// **Convergence** is the mean pairwise cosine similarity within a set. Computed
// over the sources and again over the rewrites; the difference is the answer. If
// the rewrites resemble each other more than the sources did, the pass flattened
// them, and by how much is the number.
//
// **Fidelity** is each rewrite's cosine similarity to *its own* source. A low
// one means the model rewrote rather than distilled, and it localizes the
// problem to a note somebody can open — which the corpus-level number cannot do.
//
// Both ride the embedder the daemon already runs. Nothing new is trained,
// downloaded or tuned; this is arithmetic over vectors that already exist.
//
// # What the convergence number cannot tell you
//
// It is only two-sided on a corpus whose notes already overlap. A set of
// mutually orthogonal sources is at maximum dispersion, so every possible
// rewrite either holds the angle or closes it and the delta can only come back
// >= 0. Real corpora are nowhere near that bound — this one's notes share a
// vocabulary, a voice and a subject — so the number moves in both directions in
// practice. But a batch of unusually unrelated notes will bias the delta upward,
// and a delta just over the ceiling on a small, diverse batch is weaker evidence
// than the same delta on a large one.

// Embedder turns texts into vectors. Supplied rather than imported so this
// package does not depend on the embedding client, and so a test can state the
// geometry it is testing instead of standing up a model server.
type Embedder func(ctx context.Context, texts []string) ([][]float32, error)

// Dispersion is what one batch did to the corpus's variety.
type Dispersion struct {
	// N is how many source/rewrite pairs were measured.
	N int `json:"n"`

	// SourceConvergence is the mean pairwise cosine similarity among the
	// sources — how alike the notes were before.
	SourceConvergence float64 `json:"source_convergence"`
	// RewriteConvergence is the same number after.
	RewriteConvergence float64 `json:"rewrite_convergence"`
	// Delta is Rewrite minus Source. Positive means the pass made the notes more
	// alike, which is the failure this measures.
	Delta float64 `json:"delta"`

	// MeanFidelity is the average cosine similarity between each rewrite and its
	// own source.
	MeanFidelity float64 `json:"mean_fidelity"`
	// MinFidelity is the worst one, and LeastFaithful is which note it was —
	// reported together because a mean with no example is a number nobody can
	// act on.
	MinFidelity   float64 `json:"min_fidelity"`
	LeastFaithful string  `json:"least_faithful,omitempty"`

	// BelowFloor lists the notes whose rewrite drifted further from its source
	// than FidelityFloor allows.
	BelowFloor []string `json:"below_floor,omitempty"`
}

// ConvergenceCeiling is the delta above which a batch is treated as having
// homogenized the corpus.
//
// **This is a first measurement, not a validated bar.** Nothing has measured
// this quantity on this corpus before, so the number is a judgment about what
// size of shift would be worth stopping for rather than a threshold derived
// from data. It is written down before the first run so the run cannot talk us
// into whatever it produces, and it should be replaced by a measured one once
// there are two batches to compare.
const ConvergenceCeiling = 0.05

// FidelityFloor is the similarity below which a rewrite is treated as a rewrite
// rather than a distillation.
//
// Same caveat: a pre-registered guess. Deliberately generous — enrichment is
// *supposed* to change a note substantially, and a floor tight enough to catch
// every over-rewrite would flag the pass working correctly.
const FidelityFloor = 0.55

// Homogenized reports whether the batch crossed the pre-registered ceiling.
func (d Dispersion) Homogenized() bool { return d.Delta > ConvergenceCeiling }

// Measure computes both numbers for a batch.
//
// Sources and rewrites are embedded in one call each rather than per note: the
// embedder batches, and a per-note call would make the measurement cost more
// than the enrichment it is measuring.
func Measure(ctx context.Context, embed Embedder, rels, sources, rewrites []string) (
	Dispersion, error) {
	d := Dispersion{N: len(sources)}
	if len(sources) != len(rewrites) || len(rels) != len(sources) {
		return d, fmt.Errorf("enrich: %d rels, %d sources and %d rewrites do not "+
			"pair up", len(rels), len(sources), len(rewrites))
	}
	if d.N == 0 {
		return d, nil
	}

	sv, err := embed(ctx, sources)
	if err != nil {
		return d, fmt.Errorf("embedding the sources: %w", err)
	}
	rv, err := embed(ctx, rewrites)
	if err != nil {
		return d, fmt.Errorf("embedding the rewrites: %w", err)
	}
	if len(sv) != d.N || len(rv) != d.N {
		return d, fmt.Errorf("enrich: embedder returned %d and %d vectors for %d "+
			"notes", len(sv), len(rv), d.N)
	}

	// Convergence needs a pair to be about; fidelity does not.
	//
	// An earlier version returned early on a one-note batch and skipped both,
	// which threw away a number that was perfectly well defined. The two
	// quantities have different requirements, so they are computed separately —
	// a batch of one still reports how far its single rewrite moved from its
	// source, which is exactly what somebody running one note wants to know.
	//
	// There is no `if d.N >= 2` around these three lines. There was, and the
	// negative pass showed it could never fail: `meanPairwise` already returns 0
	// below two vectors, so the guard was a second copy of a rule that has one
	// home.
	d.SourceConvergence = meanPairwise(sv)
	d.RewriteConvergence = meanPairwise(rv)
	d.Delta = d.RewriteConvergence - d.SourceConvergence

	d.MinFidelity = math.Inf(1)
	total := 0.0
	for i := range sv {
		c := cosine(sv[i], rv[i])
		total += c
		if c < d.MinFidelity {
			d.MinFidelity, d.LeastFaithful = c, rels[i]
		}
		if c < FidelityFloor {
			d.BelowFloor = append(d.BelowFloor, rels[i])
		}
	}
	d.MeanFidelity = total / float64(d.N)
	sort.Strings(d.BelowFloor)
	return d, nil
}

// meanPairwise is the average cosine similarity over every distinct pair.
//
// Every pair rather than a sample. At the batch sizes this runs on the full
// computation is a few thousand dot products, and sampling would put noise into
// the one number the decision turns on.
func meanPairwise(vs [][]float32) float64 {
	if len(vs) < 2 {
		return 0
	}
	total, pairs := 0.0, 0
	for i := 0; i < len(vs); i++ {
		for j := i + 1; j < len(vs); j++ {
			total += cosine(vs[i], vs[j])
			pairs++
		}
	}
	return total / float64(pairs)
}

// cosine is the similarity between two vectors.
//
// Normalized here rather than assumed. The embedding client normalizes what it
// returns, but this function is also handed hand-written vectors by tests and by
// anything that ever computes one locally, and a silently-wrong similarity is
// the kind of bug that reads as a finding.
func cosine(a, b []float32) float64 {
	if len(a) != len(b) || len(a) == 0 {
		return 0
	}
	var dot, na, nb float64
	for i := range a {
		x, y := float64(a[i]), float64(b[i])
		dot += x * y
		na += x * x
		nb += y * y
	}
	if na == 0 || nb == 0 {
		return 0
	}
	return dot / (math.Sqrt(na) * math.Sqrt(nb))
}

// Report renders the measurement for a human.
func (d Dispersion) Report() string {
	if d.N == 0 {
		return "dispersion: nothing was rewritten"
	}
	if d.N < 2 {
		return fmt.Sprintf("dispersion over %d note: convergence is undefined "+
			"below two\n  fidelity     %.4f (%s, floor %.2f)",
			d.N, d.MeanFidelity, d.LeastFaithful, FidelityFloor)
	}
	verdict := "no homogenization"
	if d.Homogenized() {
		verdict = fmt.Sprintf("HOMOGENIZED — delta %+.4f is over the %.2f ceiling",
			d.Delta, ConvergenceCeiling)
	}
	out := fmt.Sprintf(
		"dispersion over %d notes: %s\n"+
			"  convergence  sources %.4f → rewrites %.4f  (delta %+.4f, ceiling %.2f)\n"+
			"  fidelity     mean %.4f · min %.4f (%s, floor %.2f)",
		d.N, verdict,
		d.SourceConvergence, d.RewriteConvergence, d.Delta, ConvergenceCeiling,
		d.MeanFidelity, d.MinFidelity, d.LeastFaithful, FidelityFloor)
	if len(d.BelowFloor) > 0 {
		out += fmt.Sprintf("\n  below the fidelity floor (%d): %v",
			len(d.BelowFloor), d.BelowFloor)
	}
	return out
}
