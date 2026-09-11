package enrich

import (
	"strings"
	"testing"
)

// The enum reaches the model, so a retired type stops being *offered* as well as
// stopping being accepted. The gate alone would reject the answer after paying
// for it, which is correct and wasteful.
func TestThePromptCarriesTheContractsTypes(t *testing.T) {
	got := BuildPrompt(Request{Raw: "a note"}, []string{"convention", "fact"}, "")
	for _, want := range []string{"convention", "fact"} {
		if !strings.Contains(got, want) {
			t.Errorf("the prompt does not offer %q", want)
		}
	}
	if strings.Contains(got, "workflow") {
		t.Error("the prompt offers a type the contract did not list")
	}
}

// No contract is a different state from an empty list, and the prompt has to say
// which. An empty enum reads to a model as "any string will do".
// flat collapses the prompt's wrapping so an assertion can name a phrase the
// instruction breaks across lines.
func flat(s string) string { return strings.Join(strings.Fields(s), " ") }

func TestAnUnresolvedContractSaysSoRatherThanOfferingNothing(t *testing.T) {
	got := BuildPrompt(Request{Raw: "a note"}, nil, "")
	if !strings.Contains(got, "did not resolve") {
		t.Errorf("the prompt offers an empty enum without saying why:\n%s",
			got[:min(600, len(got))])
	}
}

// The alias rule requires derivation and carries its evidence, which is the
// half of the old two-trigger difference that was measured: invented aliases
// cost 3.85 points of R@5.
func TestTheAliasRuleRequiresDerivationAndSaysWhatItCosts(t *testing.T) {
	got := BuildPrompt(Request{Raw: "n", Trigger: TriggerBatch}, []string{"fact"}, "")

	if !strings.Contains(got, "unless the note itself contains the other name") {
		t.Error("the prompt does not require derivation")
	}
	// The bar the supervised batch's first sample forced (2026-09-11): the
	// model offered the filename stem as an alias and the gate refused the
	// write, so the rule now names that case.
	if !strings.Contains(flat(got), "neither is the filename") {
		t.Error("the prompt leaves the filename readable as an alias")
	}
	// A future reader loosening it should know what it costs.
	if !strings.Contains(got, "3.85") {
		t.Error("the rule states a ban without its evidence")
	}
	// The retired permission is gone, not merely unreachable: a prompt that
	// still offered it would invite the model to write invented aliases and
	// leave the post-gate to refuse every one of them.
	if strings.Contains(got, "phrasing the person actually used") {
		t.Error("the prompt still permits asker phrasing, with no asker to take it from")
	}
}

// The body field asks for what the grounding judge accepts, and no more.
//
// The first supervised batch (2026-09-11) refused three of four cards on body
// sentences: a consequence, a cost and a circumstance the card and its
// neighbours never stated. The prompt had asked for "a connection it does not
// make ... what it means for later work", which is an invitation to infer, and
// the judge refuses inference. The two have to ask for the same thing.
func TestTheBodyFieldAsksOnlyForWhatTheJudgeAccepts(t *testing.T) {
	got := BuildPrompt(Request{Raw: "n", Trigger: TriggerBatch}, []string{"fact"}, "")

	for _, want := range []string{
		"empty on almost every card",
		"traceable to a sentence in the card or in a neighbour",
		"No inference",
	} {
		if !strings.Contains(flat(got), want) {
			t.Errorf("the body instruction does not say %q, so it invites what the "+
				"judge refuses:\n%s", want, got[:min(900, len(got))])
		}
	}
	// The retired invitation is gone rather than merely balanced by a rule
	// below it: the model followed it and the night wrote nothing.
	for _, gone := range []string{
		"a connection it does not make",
		"what it means for later work",
	} {
		if strings.Contains(flat(got), gone) {
			t.Errorf("the prompt still invites inference: %q", gone)
		}
	}
}

// The prompt carries the voice specification, because the model's writing
// becomes the corpus.
func TestThePromptCarriesTheVoice(t *testing.T) {
	got := BuildPrompt(Request{Raw: "n"}, []string{"fact"}, "")
	for _, want := range []string{"plain, warm prose", "Complete sentences"} {
		if !strings.Contains(got, want) {
			t.Errorf("the prompt does not carry the voice spec (%q missing)", want)
		}
	}
}

// The two floors the post-gates enforce are also asked for up front. A gate that
// rejects something the prompt never requested is a gate that mostly rejects.
func TestThePromptAsksForWhatTheGatesEnforce(t *testing.T) {
	// Whitespace-normalized, because the prompt is hard-wrapped prose and a
	// substring check against it would be asserting the line width rather than
	// the instruction.
	got := strings.Join(strings.Fields(BuildPrompt(Request{Raw: "n"}, []string{"fact"}, "")), " ")
	// Token preservation is no longer asked for: the card's text is carried
	// byte for byte and the gate that enforced it retired (plan 04). What the
	// gates enforce now is asked for instead.
	for want, gate := range map[string]string{
		"traceable to the card or to its neighbours": "grounding, which the judge enforces",
		"Never return a why field":                   "no `why`, which the schema strips",
		"Only ids from that list":                    "`related` from the offer, which Compose enforces",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("the prompt does not ask for %s (%q missing)", gate, want)
		}
	}
}

// One string, two shapes: the shape is the last line, and both shapes' rules
// are in the one string the hash covers.
func TestThePromptNamesItsShapeOnTheLastLine(t *testing.T) {
	for _, d := range []Depth{DepthDeep, DepthLight} {
		got := BuildPrompt(Request{Raw: "n", Depth: d}, []string{"fact"}, "")
		if !strings.HasSuffix(got, "This is a "+d.String()+" pass.") {
			t.Errorf("a %s pass does not say so on its last line", d)
		}
	}
	got := strings.Join(strings.Fields(instructions), " ")
	for _, shape := range []string{"deep the card has not been judged", "light the card was judged before"} {
		if !strings.Contains(got, shape) {
			t.Errorf("the one prompt string does not carry the %q shape", shape)
		}
	}
}

// The rubric and the neighbours reach the model, and the neighbours as the
// ids `related` may name.
func TestThePromptCarriesTheRubricAndTheNeighbours(t *testing.T) {
	got := BuildPrompt(Request{Raw: "n", Neighbours: []Neighbour{
		{ID: "keep-git-out-of-drive", Title: "Keep git out of Drive", Summary: "Drive churns .git."},
	}}, []string{"fact"}, "A 9 is a standing rule.")
	for _, want := range []string{"A 9 is a standing rule.", "id: keep-git-out-of-drive",
		"title: Keep git out of Drive", "summary: Drive churns .git."} {
		if !strings.Contains(got, want) {
			t.Errorf("the prompt is missing %q", want)
		}
	}
	if !strings.Contains(BuildPrompt(Request{Raw: "n"}, nil, ""), "(none found)") {
		t.Error("a card with no neighbours is not told so")
	}
}

// No altitude is asked for — the deep pass drops it.
func TestThePromptNoLongerAsksForAltitude(t *testing.T) {
	if strings.Contains(BuildPrompt(Request{Raw: "n"}, []string{"fact"}, ""), "altitude") {
		t.Error("the prompt still asks for altitude")
	}
}

// The note itself is in there, which sounds obvious and is exactly the kind of
// thing a refactor drops.
func TestThePromptContainsTheNote(t *testing.T) {
	raw := "---\ntitle: X\n---\n\nThe staging gate runs first.\n"
	if !strings.Contains(BuildPrompt(Request{Raw: raw}, []string{"fact"}, ""), raw) {
		t.Error("the prompt does not contain the note it is about")
	}
}

// Change a word of the prompt and every note's idempotency key changes, so the
// corpus re-queues rather than splitting into notes written by two voices.
func TestThePassVersionMovesWithThePrompt(t *testing.T) {
	if !strings.Contains(PassVersion, PromptHash()) {
		t.Errorf("PassVersion %q does not carry the prompt hash %q, so a voice "+
			"change would apply to new notes only", PassVersion, PromptHash())
	}
	if len(PromptHash()) < 8 {
		t.Errorf("the prompt hash is %d characters — too few to be distinctive",
			len(PromptHash()))
	}
}

// The enum is deliberately *not* in the prompt hash: the contract changing is
// already in the idempotency key as the rules hash, and folding it in twice
// makes the two indistinguishable in a bug report.
func TestTheEnumIsNotInThePromptHash(t *testing.T) {
	before := PromptHash()
	_ = BuildPrompt(Request{Raw: "n"}, []string{"a", "b", "c"}, "")
	if PromptHash() != before {
		t.Error("the prompt hash moved with the enum; the rules hash already " +
			"covers that, and covering it twice makes a bug report ambiguous")
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
