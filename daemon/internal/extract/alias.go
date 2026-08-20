// Package extract holds the deterministic half of ingestion — everything that
// can be pulled out of a note with a regex, done inside the capture transaction
// where it is free and cannot fail on a network.
//
// Nothing here invents vocabulary. That distinction is the whole reason this
// package exists rather than a model call: a model reading a note paraphrases
// the note, and the gap that hurts retrieval is between the note and the
// operator's future question, not between the note and a restatement of itself.
// Model-written aliases were measured at −3.85 R@5 (p = 0.0411, six replicates)
// and are banned. What is left is surfacing vocabulary the note already
// contains, in a form the indexes can actually match.
package extract

import (
	"regexp"
	"sort"
	"strings"
	"unicode"
)

// MaxAliases caps what one note contributes.
//
// The alias column is weighted above body in ranking, so it is a scarce resource
// rather than a free one: a note that contributes forty aliases has diluted the
// column for itself and for everything it competes with. The cap is generous
// against what real notes produce and exists to bound the pathological case — a
// changelog full of identifiers, a table of API names.
const MaxAliases = 24

// minPart is the shortest decomposed fragment worth keeping. One-character
// fragments are never a query term and always noise: `idx_a_b` should contribute
// `idx`, not `a` and `b`.
const minPart = 2

var (
	// A parenthesised acronym: two to eight characters, upper-case with digits
	// allowed, so `OKF`, `BM25` and `FTS5` all match while `(the)` and `(A)` do
	// not. The lower bound of two is what keeps ordinary parenthetical asides out.
	acronymRe = regexp.MustCompile(`\(([A-Z][A-Z0-9]{1,7})\)`)

	// The reverse form — `OKF (Open Knowledge Format)`.
	reverseAcronymRe = regexp.MustCompile(`\b([A-Z][A-Z0-9]{1,7})\s*\(([^)]{2,80})\)`)

	// snake_case: unambiguously an identifier. Hyphenated words are deliberately
	// NOT matched here — a hyphen is ordinary English punctuation, and decomposing
	// `well-known` into `well` and `known` is exactly the noise the cap exists to
	// prevent, produced on purpose.
	snakeRe = regexp.MustCompile(`\b[a-zA-Z][a-zA-Z0-9]*(?:_[a-zA-Z0-9]+)+\b`)

	// camelCase and PascalCase with at least two humps, so `noteType` and
	// `StorageRules` match while `The` and `Go` do not.
	camelRe = regexp.MustCompile(`\b[a-z]+(?:[A-Z][a-z0-9]+)+\b|\b(?:[A-Z][a-z0-9]+){2,}\b`)

	// Code spans. An identifier inside backticks is an identifier whatever its
	// shape, which is what lets kebab-case be decomposed there and nowhere else.
	codeSpanRe = regexp.MustCompile("`([^`\n]{1,80})`")

	kebabIdentRe = regexp.MustCompile(`^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$`)

	wordRe = regexp.MustCompile(`[A-Za-z][A-Za-z0-9]*`)
)

// stopParts are fragments that carry no retrieval signal on their own. Kept
// small on purpose: an over-long stoplist starts removing real query terms, and
// the cost of one noise alias is far lower than the cost of dropping the word
// somebody actually searches for.
var stopParts = map[string]bool{
	"the": true, "and": true, "for": true, "with": true, "from": true,
	"that": true, "this": true, "not": true, "are": true, "was": true,
	"has": true, "had": true, "its": true, "into": true, "than": true,
	"then": true, "when": true, "what": true, "which": true, "will": true,
}

// Aliases derives the alias set for a note from its title and body.
//
// Two channels, both structural:
//
//   - **Acronyms**, in both directions. `Open Knowledge Format (OKF)` and
//     `OKF (Open Knowledge Format)` each contribute both forms, matched by
//     checking that the expansion's word initials actually spell the acronym —
//     without that check, `(see below)`-shaped text produces confident garbage.
//     Both forms are already in the body, so what this buys is *weight*: the
//     alias column ranks above body, and the acronym is usually the rarer term.
//
//   - **Compound identifiers**, decomposed. `idx_timestamp_desc` also indexes as
//     `idx`, `timestamp` and `desc`. This is the class of token an embedder
//     mangles and a tokenizer splits differently from how a question asks for it.
//
// Deterministic by construction — same input, same output, sorted — because a
// derived field that varied between runs would make every rebuild a diff.
func Aliases(title, body string) []string {
	text := title + "\n" + body
	seen := map[string]bool{}
	var out []string

	add := func(s string) {
		s = strings.TrimSpace(s)
		if len(s) < minPart {
			return
		}
		key := strings.ToLower(s)
		if stopParts[key] || seen[key] {
			return
		}
		seen[key] = true
		out = append(out, s)
	}

	for _, pair := range acronymPairs(text) {
		add(pair.acronym)
		add(pair.expansion)
	}
	for _, ident := range identifiers(text) {
		add(ident)
		for _, part := range decompose(ident) {
			add(part)
		}
	}

	// Sorted, then capped. Sorting before the cap makes *which* aliases survive a
	// property of the note rather than of the order the regexes happened to run
	// in — otherwise adding a channel would silently change what an unrelated
	// note contributes.
	sort.Slice(out, func(i, j int) bool {
		li, lj := strings.ToLower(out[i]), strings.ToLower(out[j])
		if li != lj {
			return li < lj
		}
		return out[i] < out[j]
	})
	if len(out) > MaxAliases {
		out = out[:MaxAliases]
	}
	return out
}

type acronymPair struct {
	acronym   string
	expansion string
}

// acronymPairs finds both parenthesised forms, keeping only those where the
// expansion's initials actually spell the acronym.
func acronymPairs(text string) []acronymPair {
	var pairs []acronymPair

	for _, m := range reverseAcronymRe.FindAllStringSubmatchIndex(text, -1) {
		acronym := text[m[2]:m[3]]
		inner := text[m[4]:m[5]]
		if expansionMatches(inner, acronym) {
			pairs = append(pairs, acronymPair{acronym: acronym, expansion: strings.TrimSpace(inner)})
		}
	}

	for _, m := range acronymRe.FindAllStringSubmatchIndex(text, -1) {
		acronym := text[m[2]:m[3]]
		// The words immediately before the paren. Twice the acronym's length is
		// enough slack for the small words an expansion may contain — "Open
		// Knowledge Format" is three words for three letters, but "Department of
		// Motor Vehicles" is four for three.
		before := text[:m[0]]
		if expansion := expansionBefore(before, acronym); expansion != "" {
			pairs = append(pairs, acronymPair{acronym: acronym, expansion: expansion})
		}
	}
	return pairs
}

// expansionMatches reports whether the words in `phrase` spell `acronym` by
// their initials, ignoring case. Small connecting words may be skipped, because
// an expansion is allowed to contain them and an acronym is allowed to omit them.
func expansionMatches(phrase, acronym string) bool {
	words := wordRe.FindAllString(phrase, -1)
	if len(words) == 0 {
		return false
	}
	target := strings.ToLower(acronym)
	// Strip digits from the target: `BM25` is spelled by `Best Match`, and the
	// number is part of the name rather than an initial.
	var letters []rune
	for _, r := range target {
		if unicode.IsLetter(r) {
			letters = append(letters, r)
		}
	}
	if len(letters) == 0 {
		return false
	}

	li := 0
	for _, w := range words {
		if li >= len(letters) {
			break
		}
		if rune(strings.ToLower(w)[0]) == letters[li] {
			li++
		}
	}
	return li == len(letters)
}

// expansionBefore takes the shortest trailing run of words before the paren
// whose initials spell the acronym.
func expansionBefore(before, acronym string) string {
	words := wordRe.FindAllStringIndex(before, -1)
	if len(words) == 0 {
		return ""
	}
	letters := 0
	for _, r := range acronym {
		if unicode.IsLetter(r) {
			letters++
		}
	}
	if letters == 0 {
		return ""
	}

	// Try progressively longer suffixes, shortest first, so the tightest phrase
	// that spells the acronym wins rather than the longest one that happens to.
	maxWords := letters * 2
	if maxWords > len(words) {
		maxWords = len(words)
	}
	for n := letters; n <= maxWords; n++ {
		start := words[len(words)-n][0]
		phrase := strings.TrimSpace(before[start:])
		if expansionMatches(phrase, acronym) {
			return phrase
		}
	}
	return ""
}

// identifiers finds compound identifiers: snake_case and camelCase anywhere, and
// kebab-case only inside a code span, where a hyphen is unambiguously part of a
// name rather than English punctuation.
func identifiers(text string) []string {
	seen := map[string]bool{}
	var out []string
	push := func(s string) {
		if s != "" && !seen[s] {
			seen[s] = true
			out = append(out, s)
		}
	}

	for _, s := range snakeRe.FindAllString(text, -1) {
		push(s)
	}
	for _, s := range camelRe.FindAllString(text, -1) {
		push(s)
	}
	for _, m := range codeSpanRe.FindAllStringSubmatch(text, -1) {
		inner := strings.TrimSpace(m[1])
		if kebabIdentRe.MatchString(inner) {
			push(inner)
		}
	}
	return out
}

// decompose splits a compound identifier into its parts.
func decompose(ident string) []string {
	var parts []string
	for _, chunk := range strings.FieldsFunc(ident, func(r rune) bool {
		return r == '_' || r == '-' || r == '.'
	}) {
		parts = append(parts, splitCamel(chunk)...)
	}
	if len(parts) < 2 {
		// A single part is the identifier itself, already added by the caller.
		return nil
	}
	return parts
}

// splitCamel breaks `noteType` into `note`, `Type` and leaves `timestamp` alone.
func splitCamel(s string) []string {
	if s == "" {
		return nil
	}
	var parts []string
	start := 0
	runes := []rune(s)
	for i := 1; i < len(runes); i++ {
		if unicode.IsUpper(runes[i]) && !unicode.IsUpper(runes[i-1]) {
			parts = append(parts, string(runes[start:i]))
			start = i
		}
	}
	parts = append(parts, string(runes[start:]))
	return parts
}
