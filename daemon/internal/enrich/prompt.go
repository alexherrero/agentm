package enrich

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"
)

// A note on the depth's words: `Depth.String()` is "deep" or "light", and the
// last line of the prompt uses it, so renaming a depth changes what the model
// is told. The prompt's shape names are exactly those two words.

// The prompt, and why its hash is part of the pass version.
//
// This is where the contract reaches the model. The type enum is rendered in
// from whatever `storage-rules.md` says at the moment of the call, so retiring a
// type stops it being *offered* as well as stopping it being accepted — the gate
// alone would reject the model's answer after paying for it, which is correct
// and wasteful.
//
// It also carries the voice specification, because the model's writing becomes
// the corpus. That is not a stylistic preference: a note is read back years
// later by whoever asks the right question, and prose that reads like a summary
// of a note is worse at answering than prose that reads like the answer.
//
// # The hash
//
// `PromptHash` folds into the fingerprint's idempotency key. Change a word here
// and every note's key changes, so the corpus re-queues rather than splitting
// into notes written by two different voices. That is the whole mechanism behind
// "a voice change is a version bump", and it is why this file is deliberately
// one string rather than a template assembled at several call sites.

const voiceSpec = `Write the body as plain, warm prose. Complete sentences with real
predicates. Not telegraphic colon-led fragments ("The problem: X. The fix: Y."),
not marketing register, not hedging. Prefer concrete nouns over abstractions:
say what happened, not what "occurred". Keep the operator's own words for
anything technical — a term they chose is a term they will search for.`

// The prompt is one string with two shapes (agentm-vault § Dreaming). The deep
// pass is owed once — no stamp, or a stamp from an older pass — and returns
// every field; the light pass is owed when a judged card's text has moved, and
// may move only what ranks nothing. The shape is named on the last line of the
// message, so both share this string and one hash.
//
// The card's text is never rewritten. It is the evidence, kept byte for byte,
// and what the model writes in `body` is added below it under a dated heading
// (compose.go). That is the reversal from the pass this replaced, which asked
// for "the distilled prose" and got a rewrite that read well — which is how
// residue survived the last purge.
const instructions = `You are enriching one card from a personal memory vault so that it
answers well when someone asks the right question years from now.

The card's own text is the evidence, and it is kept exactly as it was written.
You do not rewrite it. You return the card's fields and, on a deep pass, any
prose worth adding below it.

Return a single JSON object and nothing else. No preamble, no code fence, no
commentary. These fields exactly, no others:

  title                a short, specific title. Correct the existing one; do not
                       invent a new subject.
  slug                 OPTIONAL lower-case hyphenated filename stem, only if the
                       current one is wrong.
  type                 one of the values listed below, and nothing else.
  summary              one sentence saying what the card is for.
  tags                 OPTIONAL, at most 8.
  aliases              OPTIONAL, at most 6. See the alias rule below.
  related              OPTIONAL, at most 5: the ids of the neighbours below that
                       bear on this card. Only ids from that list.
  importance_proposed  a whole number from 1 to 10, against the rubric below.
  body                 OPTIONAL prose to add below the card: a connection it does
                       not make, a decision it bears on, what it means for later
                       work. Empty when there is nothing worth adding, which is
                       the right answer for most short cards.
  confidence           0.0 to 1.0 — your honest estimate that this card is a
                       durable memory worth filing and that your fields are right.
                       A low number is not a failure; it routes the card for review.

The card is judged in one of two shapes, and the last line of this message says
which:

  deep    the card has not been judged under this prompt. Return every field.
  light   the card was judged before and its text has changed since. Only
          summary, tags, related and confidence may move: keep the title and type
          unless you are sure they are now wrong, return importance_proposed as
          the card states it, and leave body empty.

Rules that are not negotiable:

  - Never return a why field. Why a card was kept is written by whoever kept it.
  - Every claim in body must be traceable to the card or to its neighbours. Do
    not add knowledge they do not contain.
  - body adds to the card. It never restates the card, never quotes its
    Evidence, and uses no heading larger than ###.`

const aliasRuleBatch = `  - Aliases must be derivable from the note itself: acronyms it spells
    out, compound identifiers it contains, alternative names it uses. Do not
    invent phrasing a reader might hypothetically search for. This is measured:
    invented aliases cost 3.85 points of recall at p=0.04.`

// Neighbour is one note the deep pass is shown beside the card: the daemon's
// own search, title and summary only.
type Neighbour struct {
	// ID is what a wikilink to the note names — its filename stem, which is
	// how Obsidian resolves a link.
	ID      string `json:"id"`
	Rel     string `json:"rel"`
	Title   string `json:"title"`
	Summary string `json:"summary,omitempty"`
}

// BuildPrompt renders the instruction, the enum, the voice, the rubric, the
// neighbours, the card, and last, the shape.
//
// The rubric and the neighbours are inputs, like the card: they are not in the
// prompt hash. The rubric lives in the contract's prose on the operator's
// ruling, so editing it changes what the next pass proposes without re-owing
// every card a pass.
func BuildPrompt(req Request, types []string, rubric string) string {
	var b strings.Builder
	b.WriteString(instructions)
	b.WriteString("\n\n")

	b.WriteString(aliasRuleBatch)
	b.WriteString("\n\nThe `type` field must be exactly one of:\n\n")
	if len(types) == 0 {
		// No contract resolved. Say so rather than offering nothing, which reads
		// to a model as "any string will do".
		b.WriteString("  (the filing contract did not resolve; do not guess a type)\n")
	}
	for _, t := range types {
		b.WriteString("  - ")
		b.WriteString(t)
		b.WriteString("\n")
	}
	b.WriteString("\nVoice, for body:\n\n")
	b.WriteString(voiceSpec)

	b.WriteString("\n\nThe importance rubric, from the filing contract:\n\n")
	if strings.TrimSpace(rubric) == "" {
		// Said rather than left out, for the same reason as the type enum.
		b.WriteString("(the contract has no importance rubric; propose 5 unless the card " +
			"is plainly residue or plainly a standing rule)")
	} else {
		b.WriteString(strings.TrimSpace(rubric))
	}

	b.WriteString("\n\nNeighbours — the nearest notes by the vault's own search, as id, " +
		"title and summary. related may name only these ids:\n\n")
	if len(req.Neighbours) == 0 {
		b.WriteString("  (none found)\n")
	}
	for _, n := range req.Neighbours {
		fmt.Fprintf(&b, "  - id: %s\n    title: %s\n", n.ID, oneLine(n.Title))
		if n.Summary != "" {
			fmt.Fprintf(&b, "    summary: %s\n", oneLine(n.Summary))
		}
	}

	b.WriteString("\nThe card:\n\n")
	b.WriteString(req.Raw)
	fmt.Fprintf(&b, "\n\nThis is a %s pass.", req.Depth)
	return b.String()
}

// oneLine flattens a neighbour's field so it cannot break the list it sits in.
func oneLine(s string) string {
	return strings.Join(strings.Fields(s), " ")
}

// PromptHash identifies the prompt's wording, for the pass version.
//
// It covers the instructions, the voice and the alias rule — everything whose
// change means a note enriched before it was enriched by a different pass. It
// deliberately does *not* cover the type enum: the contract changing is already
// in the key separately as the rules hash, and folding it in twice would make
// the two indistinguishable in a bug report.
//
// Dropping the retired eager rule changed this hash, and the two-shape prompt
// changes it again (agentm-vault plan 04), which is correct and is the point of
// the mechanism: every note enriched under an older prompt is owed the deep
// pass under this one.
func PromptHash() string {
	h := sha256.New()
	fmt.Fprint(h, instructions, voiceSpec, aliasRuleBatch)
	return hex.EncodeToString(h.Sum(nil))[:12]
}
