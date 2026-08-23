// Package meters computes the four corpus-level diversity numbers.
//
// They exist because a corpus written by a model drifts toward itself, and the
// drift is invisible note by note: every individual memory reads fine while the
// set of them slowly converges on one voice, one shape, one set of phrases. No
// single note is wrong, so nothing catches it except a number over the whole
// corpus that moves.
//
// Each meter is deliberately cheap and deterministic. None of them calls a
// model — a drift detector that needed a judgment would be measuring the same
// machinery it is watching.
//
// # Direction
//
// Every meter is written so that *falling means converging*, which is the
// direction that matters, and every doc comment says which way is bad. A meter
// whose direction the reader has to work out is a meter that gets misread at
// three in the morning.
package meters

import (
	"sort"
	"strings"
	"unicode"
)

// Trigram concentration: what share of the corpus's word-trigrams is carried by
// its most common ones.
//
// **Rising is bad.** Template language shows up here first — a distillation pass
// that has settled into a house phrasing emits the same three-word runs over and
// over, and the top slice of the distribution swells while the tail thins. It
// catches this earlier than a similarity meter does, because two notes can share
// a great deal of phrasing and still sit far apart in embedding space when their
// subjects differ.
//
// `top` is how many of the most frequent trigrams count toward the share. The
// caller passes it rather than the package fixing it, because the right number
// depends on corpus size and nobody has measured it yet.
func TrigramConcentration(bodies []string, top int) float64 {
	if top < 1 {
		return 0
	}
	counts := map[string]int{}
	total := 0
	for _, body := range bodies {
		for _, tri := range trigrams(Words(body)) {
			counts[tri]++
			total++
		}
	}
	if total == 0 {
		return 0
	}

	freq := make([]int, 0, len(counts))
	for _, c := range counts {
		freq = append(freq, c)
	}
	// Descending. Sorted rather than partially selected because the corpora this
	// runs over are thousands of notes, not millions, and a full sort is honest
	// about ties in a way a selection algorithm is not.
	sort.Sort(sort.Reverse(sort.IntSlice(freq)))

	head := 0
	for i := 0; i < top && i < len(freq); i++ {
		head += freq[i]
	}
	return float64(head) / float64(total)
}

// MovingAverageTTR is the lexical-diversity meter.
//
// **Falling is bad.** A corpus whose vocabulary is narrowing is one where the
// pass has stopped reaching for the word the source used and started reaching
// for the word it always uses.
//
// A moving-average type-token ratio rather than a plain one, because plain TTR
// falls as text gets longer for purely arithmetic reasons — a corpus that grew
// would look like a corpus that narrowed, which is the exact false alarm a drift
// meter cannot afford. Averaging the ratio over a fixed window makes the number
// comparable between a month with forty notes and a month with four hundred.
//
// Text shorter than one window is measured as a single window, so a small corpus
// gets a real number rather than a zero that reads as total collapse.
//
// # The window has a measured floor
//
// A window shorter than a note never crosses a note boundary, so it measures
// diversity *within* one memory and is blind to a phrase repeated across many —
// which is the drift this meter exists to catch. Measured against a templated
// corpus and a varied one saying the same five things:
//
//	window   varied  templated
//	     5   0.9821     0.9932   blind — scores the templated corpus higher
//	    12   0.9371     0.9583   blind
//	    25   0.9144     0.7159   sees it
//	    50   0.8600     0.5971   sees it
//	   100   0.8333     0.5714   sees it, and has become plain global TTR
//
// So `window` must clear roughly 25 to mean anything, and much past 50 the
// moving average collapses into the plain ratio and brings back the length
// dependence it was chosen to avoid. `DefaultTTRWindow` sits between.
func MovingAverageTTR(bodies []string, window int) float64 {
	if window < 1 {
		return 0
	}
	var all []string
	for _, body := range bodies {
		all = append(all, Words(body)...)
	}
	if len(all) == 0 {
		return 0
	}
	if len(all) <= window {
		return ttr(all)
	}

	var sum float64
	n := 0
	for i := 0; i+window <= len(all); i++ {
		sum += ttr(all[i : i+window])
		n++
	}
	return sum / float64(n)
}

// DefaultTTRWindow is wide enough to span note boundaries and narrow enough to
// stay a moving average. See MovingAverageTTR's table.
const DefaultTTRWindow = 50

func ttr(words []string) float64 {
	if len(words) == 0 {
		return 0
	}
	seen := make(map[string]struct{}, len(words))
	for _, w := range words {
		seen[w] = struct{}{}
	}
	return float64(len(seen)) / float64(len(words))
}

// Words splits text into lower-cased word tokens.
//
// Exported because both lexical meters and their tests need to agree on what a
// word is, and two definitions of that would make the two numbers describe
// slightly different corpora.
//
// Apostrophes stay inside words, so `doesn't` is one token rather than two —
// splitting it would inflate the type count with a `t` that is not a word.
// Everything else non-letter is a boundary, digits included: a corpus of dated
// notes would otherwise show its diversity rising every time a new year began.
func Words(text string) []string {
	fields := strings.FieldsFunc(strings.ToLower(text), func(r rune) bool {
		return !unicode.IsLetter(r) && r != '\''
	})
	out := fields[:0]
	for _, f := range fields {
		if f = strings.Trim(f, "'"); f != "" {
			out = append(out, f)
		}
	}
	return out
}

func trigrams(words []string) []string {
	if len(words) < 3 {
		return nil
	}
	out := make([]string, 0, len(words)-2)
	for i := 0; i+3 <= len(words); i++ {
		out = append(out, strings.Join(words[i:i+3], " "))
	}
	return out
}
