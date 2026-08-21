package enrich

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"
)

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

const instructions = `You are rewriting one note from a personal memory vault so that it
answers well when someone asks the right question years from now.

Return a single JSON object and nothing else. No preamble, no code fence, no
commentary. These fields exactly, no others:

  title       a short, specific title. Correct the existing one; do not invent a
              new subject.
  slug        OPTIONAL lower-case hyphenated filename stem, only if the current
              one is wrong.
  type        one of the values listed below, and nothing else.
  altitude    "canonical" if the note states something durable — a convention, a
              decided rule, a reference fact. "artifact" if it records a moment —
              session exhaust, a distilled meeting, a one-off observation.
              Default to "artifact": canonical is earned, not assumed.
  tags        OPTIONAL, at most 8.
  aliases     OPTIONAL, at most 6. See the alias rule below.
  summary     OPTIONAL one sentence, only when the note is long enough to want one.
  body        the distilled prose. This is the product.
  confidence  0.0 to 1.0 — your own honest estimate that this rewrite is right.
              A low number is not a failure; it routes the note for review.

Rules that are not negotiable:

  - Every identifier, name, number, date, URL and code symbol in the source must
    appear in your body. Preserving them is more important than concision.
  - Every claim in your body must be traceable to the source. Do not add
    knowledge, context, or implication the note does not contain.
  - Do not summarize a note that is already short. If the source is already good
    prose, return it close to unchanged and say so with a high confidence.`

const aliasRuleEager = `  - Aliases may include the phrasing the person actually used when
    capturing this, as well as terms derived from the note.`

const aliasRuleBatch = `  - Aliases must be derivable from the note itself: acronyms it spells
    out, compound identifiers it contains, alternative names it uses. Do not
    invent phrasing a reader might hypothetically search for. This is measured:
    invented aliases cost 3.85 points of recall at p=0.04.`

// BuildPrompt renders the instruction, the enum, the voice and the note.
func BuildPrompt(req Request, types []string) string {
	var b strings.Builder
	b.WriteString(instructions)
	b.WriteString("\n\n")

	if req.Trigger == TriggerEager {
		b.WriteString(aliasRuleEager)
	} else {
		b.WriteString(aliasRuleBatch)
	}
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
	b.WriteString("\nVoice:\n\n")
	b.WriteString(voiceSpec)

	if req.Trigger == TriggerEager && strings.TrimSpace(req.AskerPhrasing) != "" {
		b.WriteString("\n\nThe person captured this by saying:\n\n")
		b.WriteString(strings.TrimSpace(req.AskerPhrasing))
	}

	b.WriteString("\n\nThe note:\n\n")
	b.WriteString(req.Raw)
	return b.String()
}

// PromptHash identifies the prompt's wording, for the pass version.
//
// It covers the instructions, the voice and both alias rules — everything whose
// change means a note enriched before it was enriched by a different pass. It
// deliberately does *not* cover the type enum: the contract changing is already
// in the key separately as the rules hash, and folding it in twice would make
// the two indistinguishable in a bug report.
func PromptHash() string {
	h := sha256.New()
	fmt.Fprint(h, instructions, voiceSpec, aliasRuleEager, aliasRuleBatch)
	return hex.EncodeToString(h.Sum(nil))[:12]
}
