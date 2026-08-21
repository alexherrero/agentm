package enrich

import (
	"context"
	"fmt"
	"regexp"
	"sort"
	"strings"
	"unicode"
)

// Distinctive-token preservation: the completeness floor.
//
// It is a floor rather than a sample, and it is mechanical rather than judged,
// and both of those are the point. A model asked to distil prose will happily
// drop the one thing that made the note findable — the flag name, the error
// string, the date — because those read as detail and the surrounding sentence
// reads as meaning. But the sentence is not what anyone searches for.
//
// This is the cheapest gate that catches the most damaging failure, so it runs
// on every note rather than on a sample of them. A sampled version would let
// through exactly the note whose identifier was dropped, and nobody would find
// out until they went looking for it years later and it was not there.
//
// # What counts as distinctive
//
// Five classes, and each is a shape a regex can recognize without knowing what
// the note is about: code-shaped identifiers, capitalized names, numbers, dates,
// and URLs. Deliberately not "important words" — that is a judgment, and a
// judgment is what this gate exists to avoid depending on.
//
// # What it does not do
//
// It does not require the token to appear in the same sentence, the same order,
// or the same case for a name. Preservation is presence: the enriched note must
// still contain the thing, so a later search still finds it. Anything stricter
// would be enforcing a writing style rather than a floor.

// Tokens is the post-gate.
type Tokens struct {
	// Extra are additional patterns a caller wants preserved.
	Extra []*regexp.Regexp
}

// DefaultTokens is the shipped gate.
func DefaultTokens() *Tokens { return &Tokens{} }

func (g *Tokens) Name() string { return "token-preservation" }

var (
	// A code-shaped identifier: snake_case, kebab inside backticks, dotted
	// paths, CamelCase with an inner capital. The common thread is a word that
	// no English sentence would produce by accident.
	identRe = regexp.MustCompile(`\b[A-Za-z_][A-Za-z0-9_]*(?:[_.][A-Za-z0-9_]+)+\b`)
	// A backticked span is distinctive because the author marked it so.
	backtickRe = regexp.MustCompile("`([^`\n]+)`")
	// CamelCase with an interior capital — `SetDampenedSpaces`, `AgentM`.
	camelRe = regexp.MustCompile(`\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+\b`)
	// A number with two or more digits, or any number with a decimal point.
	// Single digits are excluded: "one of the 3 gates" survives a rewrite as
	// "one of the three gates", and failing that would be enforcing style.
	numberRe = regexp.MustCompile(`\b\d+(?:[.,]\d+)+\b|\b\d{2,}\b`)
	// A date in any of the shapes this corpus writes.
	dateRe2 = regexp.MustCompile(`\b\d{4}-\d{2}-\d{2}\b|\b\d{4}/\d{2}/\d{2}\b`)
	// A URL, path and query included.
	urlRe = regexp.MustCompile(`https?://[^\s)>\]"']+`)
)

// Distinctive extracts the tokens a rewrite must preserve.
//
// Exported because a rejection quotes what was dropped, and a reader who cannot
// reproduce the list cannot tell a real drop from a bad extractor.
func Distinctive(text string) []string {
	seen := map[string]bool{}
	add := func(s string) {
		s = strings.TrimSpace(s)
		if s == "" {
			return
		}
		seen[s] = true
	}

	for _, m := range backtickRe.FindAllStringSubmatch(text, -1) {
		add(m[1])
	}
	for _, re := range []*regexp.Regexp{urlRe, dateRe2, identRe, camelRe, numberRe} {
		for _, m := range re.FindAllString(text, -1) {
			add(m)
		}
	}
	// Capitalized names, minus the ones that are only capitals because they
	// started a sentence. A word after a full stop is not evidence of a name,
	// and treating it as one would make every rewritten opening a failure.
	for _, m := range capitalizedNames(text) {
		add(m)
	}

	out := make([]string, 0, len(seen))
	for s := range seen {
		out = append(out, s)
	}
	sort.Strings(out)
	return out
}

// capitalizedNames finds capitalized words that are not sentence openers.
func capitalizedNames(text string) []string {
	var out []string
	words := strings.FieldsFunc(text, func(r rune) bool {
		return unicode.IsSpace(r)
	})
	prevEndedSentence := true
	for _, w := range words {
		trimmed := strings.Trim(w, `.,;:!?()[]{}"'`+"`")
		if trimmed != "" && !prevEndedSentence {
			r := []rune(trimmed)
			// Three characters, not two. A two-letter capitalized word is
			// almost never a name and very often ordinary English mid-sentence
			// — the first live batch rejected a note for dropping `No`, from
			// `**No "I'll fix this next session"**`. Acronyms are handled by the
			// all-caps rule below and keep their two-character floor, because
			// `AI` and `ML` genuinely are distinctive.
			if unicode.IsUpper(r[0]) && len(r) > 2 && !allUpper(trimmed) {
				out = append(out, trimmed)
			}
			// An all-caps word is an acronym wherever it sits, including at the
			// start of a sentence — those are exactly the tokens searches use.
			if allUpper(trimmed) && len(r) > 1 {
				out = append(out, trimmed)
			}
		} else if trimmed != "" && allUpper(trimmed) && len([]rune(trimmed)) > 1 {
			out = append(out, trimmed)
		}
		prevEndedSentence = strings.HasSuffix(w, ".") || strings.HasSuffix(w, "!") ||
			strings.HasSuffix(w, "?") || strings.HasSuffix(w, ":")
	}
	return out
}

func allUpper(s string) bool {
	hasLetter := false
	for _, r := range s {
		if unicode.IsLetter(r) {
			hasLetter = true
			if !unicode.IsUpper(r) {
				return false
			}
		}
	}
	return hasLetter
}

// metadataVocabulary is every token the source's frontmatter already carries.
//
// Frontmatter is excluded from the comparison because the rewrite *replaces* it.
// The same has to be true of frontmatter values echoed into the body, and this
// corpus echoes them constantly: a mined note repeats its own `type`,
// `mining_confidence` and category inside a "## Mining metadata" block, so the
// first live run of this gate rejected 26 of 30 notes for dropping `LOW`,
// `Mining`, `Supporting`, `preferences` and `idea` — every one of them
// scaffolding the rewrite was right to throw away.
//
// Matching is prefix-based in both directions so `preference` in the frontmatter
// accounts for `preferences` in the body. A stemmer would be more precise and
// would be a dependency earning its keep on one case.
func metadataVocabulary(raw string) []string {
	if !strings.HasPrefix(raw, "---") {
		return nil
	}
	i := strings.Index(raw[3:], "\n---")
	if i < 0 {
		return nil
	}
	var out []string
	for _, f := range strings.FieldsFunc(strings.ToLower(raw[3:3+i]),
		func(r rune) bool {
			return !unicode.IsLetter(r) && !unicode.IsDigit(r)
		}) {
		if len(f) >= 3 {
			out = append(out, f)
		}
	}
	return out
}

// isMetadata reports whether a token is frontmatter vocabulary rather than
// content.
func isMetadata(tok string, vocab []string) bool {
	low := strings.ToLower(tok)
	for _, v := range vocab {
		if strings.HasPrefix(low, v) || strings.HasPrefix(v, low) {
			return true
		}
	}
	return false
}

// stripHeadings removes markdown heading lines from the text a comparison reads.
//
// A heading is a structural label rather than a claim, and enrichment
// restructures — so requiring the words of "## Supporting excerpts" to survive
// is requiring the rewrite to keep a section it was asked to absorb. The same
// reasoning already excludes a sentence opener from counting as a name.
func stripHeadings(text string) string {
	var keep []string
	for _, line := range strings.Split(text, "\n") {
		if strings.HasPrefix(strings.TrimSpace(line), "#") {
			continue
		}
		keep = append(keep, line)
	}
	return strings.Join(keep, "\n")
}

// Check compares the source's distinctive tokens against the enriched body.
func (g *Tokens) Check(_ context.Context, req Request, body string) error {
	r, err := ParseResponse(body)
	if err != nil {
		return fmt.Errorf("%w: %v", ErrNotEligible, err)
	}
	// The whole response is the haystack, not only the body: a token that moved
	// from the prose into the title or an alias is still present, still indexed,
	// and still findable. Requiring it in the body specifically would be
	// enforcing where the writer put it.
	hay := strings.Join(append([]string{r.Title, r.Body, r.Summary, r.Slug},
		append(r.Tags, r.Aliases...)...), "\n")

	vocab := metadataVocabulary(req.Raw)
	var dropped []string
	for _, tok := range Distinctive(stripHeadings(sourceBody(req.Raw))) {
		if isMetadata(tok, vocab) {
			continue
		}
		if !containsToken(hay, tok) {
			dropped = append(dropped, tok)
		}
	}
	if len(dropped) == 0 {
		return nil
	}
	// Quote them. A rejection saying "dropped 3 tokens" sends the reader to
	// diff two blobs by hand.
	shown := dropped
	if len(shown) > 6 {
		shown = shown[:6]
	}
	more := ""
	if len(dropped) > len(shown) {
		more = fmt.Sprintf(" (and %d more)", len(dropped)-len(shown))
	}
	return fmt.Errorf("%w: the rewrite dropped %s%s — preservation is the "+
		"completeness floor, because the identifier is what someone searches for "+
		"and the sentence around it is not", ErrNotEligible,
		strings.Join(quoteAll(shown), ", "), more)
}

// sourceBody strips frontmatter, so the gate compares prose to prose. A
// frontmatter key is not a distinctive token the rewrite has to carry — the
// rewrite *replaces* the frontmatter.
func sourceBody(raw string) string {
	if !strings.HasPrefix(raw, "---") {
		return raw
	}
	if i := strings.Index(raw[3:], "\n---"); i >= 0 {
		rest := raw[3+i+4:]
		if j := strings.IndexByte(rest, '\n'); j >= 0 {
			return rest[j+1:]
		}
		return ""
	}
	return raw
}

// containsToken is case-sensitive for code-shaped tokens and case-insensitive
// for words.
//
// The split matters both ways. `SetDampenedSpaces` and `setdampenedspaces` are
// different identifiers and a rewrite that changed the case broke the code
// reference; but "Antigravity" reappearing as "antigravity" mid-sentence is
// ordinary prose and failing it would be enforcing capitalization.
func containsToken(hay, tok string) bool {
	if isCodeShaped(tok) {
		return strings.Contains(hay, tok)
	}
	return strings.Contains(strings.ToLower(hay), strings.ToLower(tok))
}

func isCodeShaped(tok string) bool {
	return identRe.MatchString(tok) || camelRe.MatchString(tok) ||
		urlRe.MatchString(tok) || strings.ContainsAny(tok, "_/:")
}

func quoteAll(in []string) []string {
	out := make([]string, len(in))
	for i, s := range in {
		out[i] = "`" + s + "`"
	}
	return out
}
