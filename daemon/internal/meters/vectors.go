package meters

import (
	"errors"
	"math"
	"sort"
)

// The two meters that need the dense arm.
//
// Both refuse rather than return zero when there are no vectors. Zero
// dispersion is what a perfectly converged corpus looks like, and zero
// similarity is what a perfectly diverse one looks like — so a missing embedder
// would report either "everything is fine" or "the corpus has collapsed",
// depending which meter you read, and neither would be true. Refusing is the
// only answer that cannot be misread.

// ErrNoVectors means the dense arm had nothing to measure.
var ErrNoVectors = errors.New("meters: no vectors to measure — the embedding " +
	"meters cannot run without the dense arm, and a zero here would read as a " +
	"finding rather than as an absence")

// Distribution is a meter's shape, rather than one number.
//
// The median moves slowly and the tails move first, so a single average would
// hide the early signal this is built to catch: a corpus starts converging at
// its densest edge long before its middle notices.
type Distribution struct {
	N      int     `json:"n"`
	Min    float64 `json:"min"`
	P10    float64 `json:"p10"`
	Median float64 `json:"median"`
	P90    float64 `json:"p90"`
	Max    float64 `json:"max"`
	Mean   float64 `json:"mean"`
}

// PairwiseSimilarity is the distribution of cosine similarity over every pair.
//
// **Rising is bad.** A corpus converging on itself has every note drifting
// toward every other, and this is the most direct statement of that.
//
// Every pair rather than a sample of pairs, because the caller has already
// sampled the notes and sampling twice would make the number depend on two
// choices instead of one. n(n-1)/2 over a few hundred vectors is milliseconds.
func PairwiseSimilarity(vecs [][]float32) (Distribution, error) {
	if len(vecs) < 2 {
		return Distribution{}, ErrNoVectors
	}
	unit := normalizeAll(vecs)
	sims := make([]float64, 0, len(unit)*(len(unit)-1)/2)
	for i := range unit {
		for j := i + 1; j < len(unit); j++ {
			sims = append(sims, dot(unit[i], unit[j]))
		}
	}
	return describe(sims), nil
}

// NearestNeighbourDispersion is the distribution of each note's distance to its
// closest neighbour.
//
// **Falling is bad**, and this is the meter the design singles out: "a
// tightening nearest-neighbour distribution being the earliest signature of a
// corpus converging on itself." It moves before pairwise similarity does,
// because convergence starts locally — clusters tighten while the corpus-wide
// average is still steady.
//
// Distance rather than similarity, so that the direction matches the name: these
// are 1 - cosine, and smaller means closer.
func NearestNeighbourDispersion(vecs [][]float32) (Distribution, error) {
	if len(vecs) < 2 {
		return Distribution{}, ErrNoVectors
	}
	unit := normalizeAll(vecs)
	out := make([]float64, len(unit))
	for i := range unit {
		best := math.Inf(1)
		for j := range unit {
			if i == j {
				continue
			}
			if d := 1 - dot(unit[i], unit[j]); d < best {
				best = d
			}
		}
		out[i] = best
	}
	return describe(out), nil
}

// normalizeAll returns unit-length copies, so every later comparison is a plain
// dot product.
//
// Copies rather than in-place: the caller's vectors belong to the caller, and a
// meter that quietly rescaled the index's own data would be a bug that only
// showed up in whatever ran next.
func normalizeAll(vecs [][]float32) [][]float64 {
	out := make([][]float64, len(vecs))
	for i, v := range vecs {
		u := make([]float64, len(v))
		var norm float64
		for j, f := range v {
			u[j] = float64(f)
			norm += u[j] * u[j]
		}
		norm = math.Sqrt(norm)
		// A zero vector has no direction. Left as zeros, which makes every
		// similarity to it zero rather than NaN — a note that failed to embed
		// should not poison the whole distribution.
		if norm > 0 {
			for j := range u {
				u[j] /= norm
			}
		}
		out[i] = u
	}
	return out
}

func dot(a, b []float64) float64 {
	n := len(a)
	if len(b) < n {
		n = len(b)
	}
	var sum float64
	for i := 0; i < n; i++ {
		sum += a[i] * b[i]
	}
	return sum
}

// describe summarises a sample. Sorted first, so the percentiles are read off
// positions rather than estimated.
func describe(xs []float64) Distribution {
	sort.Float64s(xs)
	d := Distribution{N: len(xs), Min: xs[0], Max: xs[len(xs)-1]}
	var sum float64
	for _, x := range xs {
		sum += x
	}
	d.Mean = sum / float64(len(xs))
	d.P10 = at(xs, 0.10)
	d.Median = at(xs, 0.50)
	d.P90 = at(xs, 0.90)
	return d
}

// at reads a percentile off a sorted slice by nearest rank.
//
// Nearest rank rather than interpolated, because these numbers are compared
// against their own history rather than against a textbook, and a rule that
// never invents a value between two observations is easier to reason about when
// one of them moves.
func at(sorted []float64, q float64) float64 {
	if len(sorted) == 0 {
		return 0
	}
	i := int(math.Ceil(q*float64(len(sorted)))) - 1
	if i < 0 {
		i = 0
	}
	if i >= len(sorted) {
		i = len(sorted) - 1
	}
	return sorted[i]
}
