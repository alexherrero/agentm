package meters

import (
	"regexp"
	"sort"
	"strings"
)

// The slop detector's deterministic half.
//
// The design's sentence is the specification: "Templates are fine. What is not
// fine is a note that fills a template and says nothing. The detector reads
// content and ignores shape."
//
// Four signals, and the fourth is not allowed to speak on its own:
//
//   - **Template residual** — how much body exists beyond the skeleton. The
//     cheapest signal and the safest, because a heading with nothing under it is
//     an absence anyone can agree on.
//   - **Shingle novelty** — overlap against the nearest neighbour already filed.
//     First stage of two; the embedding check runs only on what this flags, which
//     is what bounds the cost.
//   - **Drift** — a trailing window against a frozen baseline, answering a
//     different question: whether the agent has gone formulaic, rather than
//     whether one note is thin.
//   - **Length** — an AND-gate, never alone, "because this vault's best notes are
//     often its shortest". Measured on the live corpus: the tenth percentile body
//     is 115 characters, and among them are complete reference notes carrying a
//     title, a URL and the line that says why it matters.
//
// # What this file does not decide
//
// Bands. The design puts a review band and a narrow auto-expire band on top of
// these numbers, and both are confirm-gated for a supervised pass. That belongs
// with the staging machinery in Python, and putting it here would let a scoring
// change alter what gets deleted without anyone reviewing the band.

// Signals is what one note scores, before any threshold is applied.
//
// Every field is a number with a direction, and none of them is a verdict. The
// caller decides what to do; this says what is true.
type Signals struct {
	Rel string `json:"rel"`

	// TemplateResidual is the share of the body that is not skeleton: headings,
	// horizontal rules, empty list markers, and the placeholder words a template
	// leaves behind. **Low is bad.** 1.0 is a note that is all content.
	TemplateResidual float64 `json:"template_residual"`

	// Novelty is one minus the Jaccard overlap of word trigrams against the most
	// similar note it was compared with. **Low is bad**, and 1.0 means nothing
	// else in the corpus shares a trigram with it.
	Novelty float64 `json:"novelty"`
	// NearestRel names what it was closest to, so a low score is checkable
	// rather than something a reader has to take on faith.
	NearestRel string `json:"nearest_rel,omitempty"`

	// Words is the body's word count. Reported, never a verdict on its own.
	Words int `json:"words"`
}

// skeleton matches what a template leaves behind when nobody fills it in.
//
// Deliberately narrow. Anything ambiguous — a short sentence, a bare URL, a
// single word that happens to be a heading elsewhere — is content until proven
// otherwise, because the failure that matters here is calling a real note empty.
// heading is checked apart from `skeleton`, because whether it counts as
// structure depends on what follows it.
var heading = regexp.MustCompile(`^\s*#{1,6}\s`)

var skeleton = regexp.MustCompile(`(?m)^\s*(?:` +
	`[-*+]\s*$` + // an empty list marker
	`|\d+\.\s*$` + // an empty numbered marker
	`|[-*_]{3,}\s*$` + // a horizontal rule
	`|>\s*$` + // an empty quote
	`|\|[\s|:-]*\|\s*$` + // a table rule or an empty row
	`|(?:TODO|TBD|FIXME|XXX|N/?A)[\s:.-]*$` + // an unfilled placeholder
	`|\[[^\]]*\]\s*$` + // a bare bracketed placeholder
	`)`)

// placeholderWord catches a template's own prompt surviving into the filled note.
//
// Anchored to the whole line, and that is the whole point. An unfilled template
// leaves a line that is *nothing but* the prompt; a filled one leaves prose that
// may happen to use the same words. The first version matched these anywhere and
// called "Placeholder addressing is how the daemon resolves a port it does not
// own" an empty line — which is the failure this signal must not have, since a
// note wrongly called empty is a note in the auto-expire band.
//
// `describe the …`, `one-line summary` and `fill in` were in that first version
// too, and all three are ordinary English mid-sentence.
var placeholderWord = regexp.MustCompile(`(?i)\A\s*(?:` +
	`lorem ipsum.*` +
	`|your \w+ here` +
	`|describe the \w+` +
	`|one-line summary` +
	`|fill (?:this|in)(?: \w+)?` +
	`|placeholder` +
	`)[\s:.…-]*\z`)

// TemplateResidualOf is the share of a body that is not skeleton.
//
// Measured over lines rather than characters. A heading is a line's worth of
// structure whatever its length, and weighting by characters would make a note
// with one long heading look emptier than one with six short ones.
//
// An empty body scores 0 — there is no content beyond the skeleton because there
// is nothing at all. That is the honest reading and it is also the useful one:
// the auto-expire band the design describes is for exactly this note.
func TemplateResidualOf(body string) float64 {
	lines := strings.Split(strings.TrimSpace(body), "\n")
	var total, content int
	for i, ln := range lines {
		if strings.TrimSpace(ln) == "" {
			continue
		}
		total++
		if heading.MatchString(ln) {
			// A heading is skeleton only when nothing follows it. With content
			// underneath it is the note's title, and counting it as structure
			// inverted this signal on exactly the stratum the design protects:
			// measured live, every one of the twelve thinnest notes was a
			// complete reference note scoring 0.50 because half its lines were
			// its own heading.
			if !headingIsEmpty(lines, i) {
				content++
			}
			continue
		}
		if skeleton.MatchString(ln) || placeholderWord.MatchString(ln) {
			continue
		}
		content++
	}
	if total == 0 {
		return 0
	}
	return float64(content) / float64(total)
}

// headingIsEmpty reports whether nothing but blank lines and further headings
// follow this one before the next — the unfilled-section case.
func headingIsEmpty(lines []string, i int) bool {
	for _, ln := range lines[i+1:] {
		t := strings.TrimSpace(ln)
		if t == "" {
			continue
		}
		if heading.MatchString(ln) {
			return true
		}
		return false
	}
	return true
}

// Shingles is the set of word trigrams in a body, lowercased.
//
// Words rather than characters, and three rather than five, because the thing
// being caught is a sentence reused with a noun swapped — which shares long word
// runs and would share character shingles even between unrelated notes.
func Shingles(body string) map[string]bool {
	w := Words(body)
	out := map[string]bool{}
	for i := 0; i+2 < len(w); i++ {
		out[w[i]+" "+w[i+1]+" "+w[i+2]] = true
	}
	return out
}

// Jaccard is the overlap of two shingle sets.
//
// Zero when either is empty rather than one: two notes too short to shingle have
// not been shown to be similar, and returning 1.0 would flag every pair of them
// as copies of each other.
func Jaccard(a, b map[string]bool) float64 {
	if len(a) == 0 || len(b) == 0 {
		return 0
	}
	inter := 0
	small, large := a, b
	if len(b) < len(a) {
		small, large = b, a
	}
	for k := range small {
		if large[k] {
			inter++
		}
	}
	union := len(a) + len(b) - inter
	if union == 0 {
		return 0
	}
	return float64(inter) / float64(union)
}

// Scorable is one member of the corpus, as the detector reads it.
//
// Not `Note` — this package already has one, and it is the clusterer's: a rel, a
// vector and a provenance list. Two things called Note in one package would make
// every call site ambiguous to a reader even where the compiler is happy.
type Scorable struct {
	Rel  string
	Body string
}

// Score computes every deterministic signal for every note.
//
// Novelty is measured against the rest of the corpus, so this takes the whole set
// rather than one note: "how novel is this" has no answer without something to be
// novel against. A single note scores 1.0 novelty, which is true — there is
// nothing it repeats.
//
// Sorted output, because two runs over one corpus have to produce the same
// report or every number downstream moves on its own.
func Score(notes []Scorable) []Signals {
	shingles := make([]map[string]bool, len(notes))
	for i, n := range notes {
		shingles[i] = Shingles(n.Body)
	}

	out := make([]Signals, 0, len(notes))
	for i, n := range notes {
		s := Signals{
			Rel:              n.Rel,
			TemplateResidual: TemplateResidualOf(n.Body),
			Words:            len(Words(n.Body)),
			Novelty:          1,
		}
		best := 0.0
		for j := range notes {
			if i == j {
				continue
			}
			if v := Jaccard(shingles[i], shingles[j]); v > best {
				best, s.NearestRel = v, notes[j].Rel
			}
		}
		// With nothing to compare against, the loop above never runs, `best`
		// stays zero and this is already 1 — which is the true answer for a lone
		// note, since there is nothing it repeats. An explicit guard for that
		// case was tried and no mutation could turn it red.
		s.Novelty = 1 - best
		out = append(out, s)
	}
	sort.Slice(out, func(a, b int) bool { return out[a].Rel < out[b].Rel })
	return out
}
