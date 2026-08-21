package enrich

import (
	"context"
	"fmt"
	"strings"
	"unicode"
)

// Alias vocabulary, and the one rule this arc paid to learn.
//
// Model-written aliases were measured and they cost recall: −3.85 R@5 at
// p = 0.0411 over six replicates, across three separately pre-registered
// strategies that all produced the same null-or-worse. The finding is not that
// aliases are useless — the alias column ranks above body and a real alias is
// real surface — but that a model inventing *what someone might search for* is
// guessing at a distribution it cannot see, and the guesses crowd out the terms
// the note actually contains.
//
// So the rule is derivation, and it varies by trigger for one reason: at the
// eager trigger there is an asker, and the words they used are evidence rather
// than invention. At the batch trigger there is nobody, and an alias that cannot
// be derived from the note is the exact thing that was measured and rejected.
//
// The cold scheduled backfill — a pass over the whole corpus writing aliases
// with no note-level trigger at all — is banned outright, and the ban is
// structural: there are two triggers and neither is it.

// Aliases is the post-gate.
type Aliases struct {
	// MinLen ignores very short aliases when deriving. A two-letter token is
	// noise in a corpus this size, and demanding derivation for one would reject
	// legitimate rewrites over nothing.
	MinLen int
}

// DefaultAliases is the shipped gate.
func DefaultAliases() *Aliases { return &Aliases{MinLen: 3} }

func (g *Aliases) Name() string { return "alias-vocabulary" }

func (g *Aliases) Check(_ context.Context, req Request, body string) error {
	r, err := ParseResponse(body)
	if err != nil {
		return fmt.Errorf("%w: %v", ErrNotEligible, err)
	}
	if len(r.Aliases) == 0 {
		return nil
	}

	// What counts as derivable. At the eager trigger the asking session's words
	// join the note as evidence; at the batch trigger they do not exist and must
	// not be substituted for by invention.
	source := sourceBody(req.Raw)
	if req.Trigger == TriggerEager {
		source += "\n" + req.AskerPhrasing
	}

	var invented []string
	for _, a := range r.Aliases {
		if !Derivable(a, source, g.MinLen) {
			invented = append(invented, a)
		}
	}
	if len(invented) == 0 {
		return nil
	}
	if req.Trigger == TriggerEager {
		// Should not happen — the asker's words are already folded in above — so
		// if it does, the alias came from neither the note nor the person.
		return fmt.Errorf("%w: %s came from neither the note nor the asker",
			ErrNotEligible, strings.Join(quoteAll(invented), ", "))
	}
	return fmt.Errorf("%w: %s cannot be derived from the note, and there is no "+
		"asker at the batch trigger; invented aliases were measured at "+
		"−3.85 R@5 (p=0.0411, six replicates)", ErrNotEligible,
		strings.Join(quoteAll(invented), ", "))
}

// Derivable reports whether an alias can be traced to the source.
//
// Three routes, all mechanical. Presence, because an alias the note contains is
// the note's own vocabulary. Acronym expansion in both directions, because
// "RRF" and "reciprocal rank fusion" are the same term and a searcher will use
// either. And word-subset, because a multi-word alias whose words all appear is
// a rearrangement of the note's terms rather than a new one.
//
// A compound-decomposition route was written and removed — see the body for why
// it could never decide anything.
func Derivable(alias, source string, minLen int) bool {
	alias = strings.TrimSpace(alias)
	if alias == "" {
		return false
	}
	if minLen > 0 && len([]rune(alias)) < minLen {
		// Too short to be worth a derivation argument either way.
		return true
	}
	lowSrc := strings.ToLower(source)
	lowAlias := strings.ToLower(alias)

	// 1. The note contains it outright.
	if strings.Contains(lowSrc, lowAlias) {
		return true
	}
	// 2. It is an acronym of a phrase in the note, or a phrase whose acronym
	//    the note contains.
	if acronymOf(lowAlias, lowSrc) || expansionOf(lowAlias, lowSrc) {
		return true
	}
	// A compound-decomposition route used to sit here and it was decoration: a
	// part of `idx_timestamp_desc` is a literal substring of it, so the presence
	// check above always matched first and this could never be the deciding
	// route. The negative pass caught it — removing the route left every test
	// green — and it is gone rather than propped up with a contrived fixture.
	//
	// 3. Every word of it appears in the note.
	words := strings.FieldsFunc(lowAlias, func(r rune) bool {
		return !unicode.IsLetter(r) && !unicode.IsDigit(r)
	})
	if len(words) > 1 {
		all := true
		for _, w := range words {
			if len(w) >= minLen && !strings.Contains(lowSrc, w) {
				all = false
				break
			}
		}
		if all {
			return true
		}
	}
	return false
}

// acronymOf reports whether `alias` is the initials of some run of words in the
// source.
func acronymOf(alias, source string) bool {
	letters := []rune(strings.Map(func(r rune) rune {
		if unicode.IsLetter(r) {
			return unicode.ToLower(r)
		}
		return -1
	}, alias))
	if len(letters) < 2 || len(letters) > 6 {
		return false
	}
	words := strings.Fields(source)
	for i := 0; i+len(letters) <= len(words); i++ {
		match := true
		for j, want := range letters {
			w := strings.TrimLeftFunc(words[i+j], func(r rune) bool {
				return !unicode.IsLetter(r)
			})
			if w == "" || unicode.ToLower([]rune(w)[0]) != want {
				match = false
				break
			}
		}
		if match {
			return true
		}
	}
	return false
}

// expansionOf reports whether the source contains an acronym that `alias`
// spells out — the same relationship as acronymOf, read the other way.
func expansionOf(alias, source string) bool {
	words := strings.Fields(alias)
	if len(words) < 2 || len(words) > 6 {
		return false
	}
	var initials strings.Builder
	for _, w := range words {
		for _, r := range w {
			if unicode.IsLetter(r) {
				initials.WriteRune(unicode.ToLower(r))
				break
			}
		}
	}
	acr := initials.String()
	if len(acr) < 2 {
		return false
	}
	for _, w := range strings.Fields(source) {
		trimmed := strings.ToLower(strings.TrimFunc(w, func(r rune) bool {
			return !unicode.IsLetter(r)
		}))
		if trimmed == acr {
			return true
		}
	}
	return false
}
