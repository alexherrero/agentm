package meters

import (
	"regexp"
	"strings"
	"unicode"
)

// Claim splitting: the deterministic half of the completeness score.
//
// The question the score answers is "what did the source say that the rewrite no
// longer says", and answering it claim by claim rather than as one impression is
// what makes the number mean anything. A model asked "is this complete?" returns
// a feeling. A model asked "which of these eleven claims survive?" returns
// something a person can check by reading eleven lines.
//
// So the split happens here, in Go, deterministically, before any model sees the
// note. Two runs over the same source produce the same claims, which is what lets
// the replicates measure the *judge's* variance rather than the splitter's.

// MinClaimWords is the floor for a fragment to count as a claim.
//
// Three words or fewer is a heading, a label, or the tail of a sentence the
// capture bug cut in half. Counting those as claims would put a stack of
// unanswerable fragments in front of the judge and score every note that has
// them as incomplete — which measures the capture bug, not the rewrite.
const MinClaimWords = 4

var (
	// A fenced block. Code is not a prose claim, and a rewrite is not expected
	// to carry it.
	fenceRe = regexp.MustCompile("(?s)```.*?```|~~~.*?~~~")
	// Leading list markers, block quotes and heading marks.
	leadRe = regexp.MustCompile(`^\s*(?:[-*+]|\d+[.)]|>|#{1,6})\s+`)
	// Inline markdown that carries no words of its own.
	linkRe = regexp.MustCompile(`\[([^\]]*)\]\([^)]*\)`)
	// A sentence end: terminator, then space, then something that starts a new
	// sentence. The lookahead is done by hand below because Go's regexp has no
	// lookahead, which is also why this is a scanner rather than a Split.
	spaceRe = regexp.MustCompile(`\s+`)
)

// abbreviations that end in a period and do not end a sentence.
var abbreviations = map[string]bool{
	"e.g": true, "i.e": true, "etc": true, "cf": true, "vs": true,
	"approx": true, "no": true, "fig": true, "al": true, "ca": true,
	"mr": true, "ms": true, "dr": true, "st": true, "jr": true,
}

// Claims splits a note body into the claims a rewrite can be checked against.
//
// Order is preserved and is load-bearing: the judge is asked about claim numbers,
// so the numbering has to mean the same thing on both sides of the call.
func Claims(body string) []string {
	body = fenceRe.ReplaceAllString(body, " ")
	body = linkRe.ReplaceAllString(body, "$1")

	var out []string
	// Lines first. A list is a list of claims, and running sentence-splitting
	// across a line break would glue the last word of one item to the first of
	// the next.
	for _, line := range strings.Split(body, "\n") {
		line = leadRe.ReplaceAllString(line, "")
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		for _, s := range sentences(line) {
			s = strings.TrimSpace(spaceRe.ReplaceAllString(s, " "))
			if len(strings.Fields(s)) >= MinClaimWords {
				out = append(out, s)
			}
		}
	}
	return out
}

// sentences splits one line on sentence boundaries.
//
// Hand-written rather than a regexp because the two cases that matter are both
// negative — a period inside a number, and a period ending an abbreviation — and
// expressing "split here unless" is what Go's regexp cannot do.
func sentences(line string) []string {
	var out []string
	start := 0
	runes := []rune(line)
	for i := 0; i < len(runes); i++ {
		if runes[i] != '.' && runes[i] != '!' && runes[i] != '?' {
			continue
		}
		// A terminator has to be followed by whitespace to end a sentence.
		if i+1 >= len(runes) || !unicode.IsSpace(runes[i+1]) {
			continue
		}
		// …and by something that starts one. A lowercase word after a period is
		// far more often a version string or a path than a new sentence.
		j := i + 1
		for j < len(runes) && unicode.IsSpace(runes[j]) {
			j++
		}
		if j < len(runes) && !startsSentence(runes[j]) {
			continue
		}
		if runes[i] == '.' && endsAbbreviation(runes[:i]) {
			continue
		}
		out = append(out, string(runes[start:i+1]))
		start = j
	}
	if start < len(runes) {
		out = append(out, string(runes[start:]))
	}
	return out
}

func startsSentence(r rune) bool {
	return unicode.IsUpper(r) || unicode.IsDigit(r) || r == '`' || r == '"' || r == '\''
}

// endsAbbreviation reports whether the text before a period is one.
func endsAbbreviation(before []rune) bool {
	// Walk back over the token the period is attached to.
	k := len(before)
	for k > 0 && !unicode.IsSpace(before[k-1]) {
		k--
	}
	tok := strings.ToLower(strings.Trim(string(before[k:]), "(\"'`"))
	if abbreviations[tok] {
		return true
	}
	// A single letter with a period is an initial, not a sentence end.
	return len([]rune(tok)) == 1 && unicode.IsLetter([]rune(tok)[0])
}
