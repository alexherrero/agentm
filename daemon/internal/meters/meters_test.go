package meters

import (
	"errors"
	"math"
	"strings"
	"testing"
)

// Every meter is a claim about a *direction*, so every test here compares two
// corpora rather than checking one number against a constant. A meter asserted
// against a hardcoded value would pass while pointing the wrong way, and pointing
// the wrong way is the only failure that matters: a converging corpus reported as
// healthy is worse than no meter at all.

// varied and templated are the same subject matter written two ways.
func varied() []string {
	return []string{
		"The compute buffers page-fault above roughly two thousand tokens.",
		"Fridays suit the weekly review because the week is still legible.",
		"Seventy-five per cent hydration gives an open crumb and a slack dough.",
		"Most of the deploy wall clock is the container build, not the tests.",
		"Spare keys live in the drawer under the kettle, not by the door.",
	}
}

// The same five facts after a pass that has settled into one phrasing. Same
// subjects, same lengths, one voice.
func templated() []string {
	return []string{
		"It is worth noting that the compute buffers fail above two thousand tokens.",
		"It is worth noting that the weekly review works better on a Friday.",
		"It is worth noting that the hydration level governs the crumb structure.",
		"It is worth noting that the container build dominates the deploy time.",
		"It is worth noting that the spare keys are kept under the kettle.",
	}
}

func TestTrigramConcentrationRisesWithTemplateLanguage(t *testing.T) {
	loose := TrigramConcentration(varied(), 5)
	tight := TrigramConcentration(templated(), 5)
	if tight <= loose {
		t.Errorf("templated corpus concentrates at %.3f and a varied one at "+
			"%.3f — the meter does not see house phrasing", tight, loose)
	}
}

// scrambled uses exactly the templated corpus's words, in orders that repeat no
// phrase. Its unigram profile is identical and its trigram profile is not.
//
// This is what separates a phrase meter from a word meter, and it had to be
// written: without it, counting single words scored the templated corpus just as
// concentrated, because a corpus that repeats a phrase also repeats the words in
// it. Measured — the mutation that replaced trigrams with unigrams passed every
// other test here.
func scrambled() []string {
	return []string{
		"Noting the tokens, it is worth that two thousand compute buffers fail above.",
		"Better on a Friday: the weekly review works, it is worth noting that.",
		"Governs the crumb structure, that the hydration level — it is worth noting.",
		"The deploy time dominates the container build, it is worth noting that.",
		"Kept under the kettle are the spare keys, noting that it is worth.",
	}
}

func TestConcentrationMeasuresPhrasesRatherThanWords(t *testing.T) {
	// The premise first. If the two corpora do not hold the same words, this
	// test proves nothing about phrases — measured, because two sentences
	// originally dropped a `the` and the comparison quietly became a word
	// comparison again.
	count := func(bodies []string) map[string]int {
		out := map[string]int{}
		for _, b := range bodies {
			for _, w := range Words(b) {
				out[w]++
			}
		}
		return out
	}
	a, b := count(templated()), count(scrambled())
	if len(a) != len(b) {
		t.Fatalf("the two corpora hold %d and %d distinct words; this test only "+
			"means something when they hold the same ones", len(a), len(b))
	}
	for w, n := range a {
		if b[w] != n {
			t.Fatalf("%q appears %d times templated and %d scrambled; the "+
				"corpora differ in words, so a difference in the meter says "+
				"nothing about phrasing", w, n, b[w])
		}
	}

	same := TrigramConcentration(scrambled(), 5)
	phrased := TrigramConcentration(templated(), 5)
	if phrased <= same {
		t.Errorf("the same words phrased identically concentrate at %.3f and "+
			"scrambled at %.3f — the meter is counting words, not phrases",
			phrased, same)
	}
}

func TestLexicalDiversityFallsWithTemplateLanguage(t *testing.T) {
	loose := MovingAverageTTR(varied(), DefaultTTRWindow)
	tight := MovingAverageTTR(templated(), DefaultTTRWindow)
	if tight >= loose {
		t.Errorf("templated corpus scores %.3f and a varied one %.3f — the "+
			"meter does not see a narrowing vocabulary", tight, loose)
	}
}

// A plain type-token ratio falls as text lengthens for arithmetic reasons alone,
// which would make a growing corpus look like a narrowing one. The moving
// average exists to survive that, so it is checked directly.
func TestDiversityDoesNotFallJustBecauseTheCorpusGrew(t *testing.T) {
	small := varied()
	large := append(append([]string{}, varied()...), varied()...)
	large = append(large, varied()...)

	a, b := MovingAverageTTR(small, DefaultTTRWindow), MovingAverageTTR(large, DefaultTTRWindow)
	if math.Abs(a-b) > 0.05 {
		t.Errorf("the same writing measured %.3f at five notes and %.3f at "+
			"fifteen; length is moving the meter, not vocabulary", a, b)
	}
}

// Concentration is a share, so it stays inside its own bounds however odd the
// input.
func TestConcentrationStaysAShare(t *testing.T) {
	for name, corpus := range map[string][]string{
		"empty":        {},
		"one word":     {"hello"},
		"two words":    {"hello there"},
		"all the same": {"a b c", "a b c", "a b c"},
	} {
		got := TrigramConcentration(corpus, 5)
		if got < 0 || got > 1 {
			t.Errorf("%s: concentration = %v, outside [0,1]", name, got)
		}
	}
}

// A corpus of one repeated sentence is the extreme case, and it must read as
// fully concentrated rather than as an error or a zero.
func TestOneSentenceRepeatedIsTotalConcentration(t *testing.T) {
	same := []string{"the same three words", "the same three words", "the same three words"}
	if got := TrigramConcentration(same, 5); got != 1 {
		t.Errorf("a corpus of one repeated sentence concentrates at %v, want 1", got)
	}
}

func TestWordsKeepsContractionsAndDropsDigits(t *testing.T) {
	got := strings.Join(Words("It doesn't matter — 2026 was fine, wasn't it?"), " ")
	want := "it doesn't matter was fine wasn't it"
	if got != want {
		t.Errorf("Words = %q, want %q", got, want)
	}
}

// ── the dense meters ────────────────────────────────────────────────────────

// spread and clustered are two corpora in embedding space: one where notes point
// in different directions, one where they have converged.
// Magnitudes deliberately vary by two orders. An embedder does not return unit
// vectors, and a fixture that happened to be unit length made the normalisation
// step untestable — measured: deleting it changed no result.
func spread() [][]float32 {
	return [][]float32{
		{10, 0, 0}, {0, 0.1, 0}, {0, 0, 5}, {-3, 0, 0}, {0, -20, 0},
	}
}

func clustered() [][]float32 {
	return [][]float32{
		{10, 0.2, 0}, {0.5, 0.005, 0}, {30, 0.9, 0}, {2, 0, 0.02}, {7, 0.14, 0.07},
	}
}

func TestPairwiseSimilarityRisesAsTheCorpusConverges(t *testing.T) {
	loose, err := PairwiseSimilarity(spread())
	if err != nil {
		t.Fatal(err)
	}
	tight, err := PairwiseSimilarity(clustered())
	if err != nil {
		t.Fatal(err)
	}
	if tight.Median <= loose.Median {
		t.Errorf("a clustered corpus reads %.3f and a spread one %.3f — the "+
			"meter is pointing the wrong way", tight.Median, loose.Median)
	}
}

func TestDispersionFallsAsTheCorpusConverges(t *testing.T) {
	loose, err := NearestNeighbourDispersion(spread())
	if err != nil {
		t.Fatal(err)
	}
	tight, err := NearestNeighbourDispersion(clustered())
	if err != nil {
		t.Fatal(err)
	}
	if tight.Median >= loose.Median {
		t.Errorf("a clustered corpus disperses at %.4f and a spread one at "+
			"%.4f — the meter is pointing the wrong way", tight.Median, loose.Median)
	}
}

// The bar this pair is written against: no vectors is a refusal, never a number.
//
// Zero dispersion is what a fully converged corpus looks like and zero
// similarity is what a fully diverse one looks like, so a missing embedder would
// report a crisis or a clean bill depending which meter was read.
func TestTheDenseMetersRefuseRatherThanReportZero(t *testing.T) {
	for name, in := range map[string][][]float32{
		"nothing":  nil,
		"one note": {{1, 0, 0}},
	} {
		if _, err := PairwiseSimilarity(in); !errors.Is(err, ErrNoVectors) {
			t.Errorf("%s: PairwiseSimilarity returned %v, want ErrNoVectors", name, err)
		}
		if _, err := NearestNeighbourDispersion(in); !errors.Is(err, ErrNoVectors) {
			t.Errorf("%s: NearestNeighbourDispersion returned %v, want ErrNoVectors", name, err)
		}
	}
}

// A note that failed to embed must not poison the distribution with NaN.
func TestAZeroVectorDoesNotPoisonTheDistribution(t *testing.T) {
	withZero := append(spread(), []float32{0, 0, 0})
	d, err := PairwiseSimilarity(withZero)
	if err != nil {
		t.Fatal(err)
	}
	for name, v := range map[string]float64{
		"min": d.Min, "median": d.Median, "max": d.Max, "mean": d.Mean,
	} {
		if math.IsNaN(v) || math.IsInf(v, 0) {
			t.Errorf("%s is %v after one un-embedded note", name, v)
		}
	}
}

// The meters do not modify what they are handed. They read the index's own
// vectors, and quietly rescaling those would be a bug that surfaced in whatever
// ran next rather than here.
func TestTheMetersLeaveTheirInputAlone(t *testing.T) {
	in := spread()
	before := in[0][0]
	if _, err := PairwiseSimilarity(in); err != nil {
		t.Fatal(err)
	}
	if in[0][0] != before {
		t.Errorf("the caller's vector was rewritten: %v then %v", before, in[0][0])
	}
}

// Percentiles come off the sorted sample by nearest rank, so every reported
// value is one that was actually observed.
//
// Asserted against a hand-computed sample rather than only checked for ordering:
// an off-by-two in the rank calculation keeps the distribution perfectly ordered
// while reporting the wrong observations, and that is what an earlier version of
// this test let through.
func TestPercentilesAreTheObservationsTheyClaimToBe(t *testing.T) {
	// Five observations, so nearest rank puts P10 on the first, the median on
	// the third and P90 on the fifth.
	got := describe([]float64{5, 1, 4, 2, 3})
	for _, c := range []struct {
		name string
		got  float64
		want float64
	}{
		{"min", got.Min, 1}, {"P10", got.P10, 1}, {"median", got.Median, 3},
		{"P90", got.P90, 5}, {"max", got.Max, 5}, {"mean", got.Mean, 3},
	} {
		if c.got != c.want {
			t.Errorf("%s = %v, want %v", c.name, c.got, c.want)
		}
	}
	if got.N != 5 {
		t.Errorf("N = %d, want 5", got.N)
	}
}

// And over real vectors the shape holds and there is one observation per note.
func TestDispersionReportsOneObservationPerNote(t *testing.T) {
	d, err := NearestNeighbourDispersion(spread())
	if err != nil {
		t.Fatal(err)
	}
	if d.P10 < d.Min || d.P90 > d.Max || d.Median < d.P10 || d.Median > d.P90 {
		t.Errorf("the distribution is not ordered: %+v", d)
	}
	if d.N != len(spread()) {
		t.Errorf("N = %d, want one observation per note", d.N)
	}
}

// Vectors of very different magnitudes pointing the same way are the same
// direction, which is what a cosine meter has to see. Without normalisation the
// longer vector dominates every dot product and the numbers stop meaning
// similarity at all.
func TestMagnitudeDoesNotChangeDirection(t *testing.T) {
	// Not orthogonal. Two perpendicular vectors have a dot product of zero at
	// every scale, so normalisation cannot change it and a fixture built from
	// them cannot tell whether normalisation happened — measured.
	short, err := PairwiseSimilarity([][]float32{{1, 1, 0}, {1, 0, 0}})
	if err != nil {
		t.Fatal(err)
	}
	long, err := PairwiseSimilarity([][]float32{{100, 100, 0}, {0.01, 0, 0}})
	if err != nil {
		t.Fatal(err)
	}
	if math.Abs(short.Median-long.Median) > 1e-9 {
		t.Errorf("the same two directions read %.6f at one scale and %.6f at "+
			"another — magnitude is moving the meter", short.Median, long.Median)
	}
}

// Same corpus, same numbers — the determinism the nightly trend rests on.
func TestTheMetersAreDeterministic(t *testing.T) {
	a, _ := PairwiseSimilarity(spread())
	b, _ := PairwiseSimilarity(spread())
	if a != b {
		t.Errorf("two runs differ: %+v then %+v", a, b)
	}
	if TrigramConcentration(varied(), 5) != TrigramConcentration(varied(), 5) {
		t.Error("trigram concentration differs between runs")
	}
	if MovingAverageTTR(varied(), DefaultTTRWindow) != MovingAverageTTR(varied(), DefaultTTRWindow) {
		t.Error("lexical diversity differs between runs")
	}
}

// The window's floor, held as a test because it is the reason the default is
// what it is. A window under it reads a templated corpus as *more* diverse than
// a varied one, which is the meter pointing backwards.
func TestATooNarrowWindowIsBlindToTemplating(t *testing.T) {
	narrow := 12
	if MovingAverageTTR(templated(), narrow) <= MovingAverageTTR(varied(), narrow) {
		t.Skip("a 12-word window now distinguishes them; the floor in " +
			"MovingAverageTTR's table has moved and should be re-measured")
	}
	if DefaultTTRWindow < 25 {
		t.Errorf("DefaultTTRWindow is %d, under the measured floor of about 25 "+
			"where the window starts crossing note boundaries", DefaultTTRWindow)
	}
}
