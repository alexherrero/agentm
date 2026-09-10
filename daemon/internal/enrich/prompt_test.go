package enrich

import (
	"strings"
	"testing"
)

// The enum reaches the model, so a retired type stops being *offered* as well as
// stopping being accepted. The gate alone would reject the answer after paying
// for it, which is correct and wasteful.
func TestThePromptCarriesTheContractsTypes(t *testing.T) {
	got := BuildPrompt(Request{Raw: "a note"}, []string{"convention", "fact"})
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
func TestAnUnresolvedContractSaysSoRatherThanOfferingNothing(t *testing.T) {
	got := BuildPrompt(Request{Raw: "a note"}, nil)
	if !strings.Contains(got, "did not resolve") {
		t.Errorf("the prompt offers an empty enum without saying why:\n%s",
			got[:min(600, len(got))])
	}
}

// The alias rule requires derivation and carries its evidence, which is the
// half of the old two-trigger difference that was measured: invented aliases
// cost 3.85 points of R@5.
func TestTheAliasRuleRequiresDerivationAndSaysWhatItCosts(t *testing.T) {
	got := BuildPrompt(Request{Raw: "n", Trigger: TriggerBatch}, []string{"fact"})

	if !strings.Contains(got, "derivable from the note itself") {
		t.Error("the prompt does not require derivation")
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

// The prompt carries the voice specification, because the model's writing
// becomes the corpus.
func TestThePromptCarriesTheVoice(t *testing.T) {
	got := BuildPrompt(Request{Raw: "n"}, []string{"fact"})
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
	got := strings.Join(strings.Fields(BuildPrompt(Request{Raw: "n"}, []string{"fact"})), " ")
	if !strings.Contains(got, "must appear in your body") {
		t.Error("the prompt does not ask for distinctive-token preservation, " +
			"which a post-gate then enforces")
	}
	if !strings.Contains(got, "traceable to the source") {
		t.Error("the prompt does not ask for grounding, which a post-gate " +
			"then enforces")
	}
}

// The note itself is in there, which sounds obvious and is exactly the kind of
// thing a refactor drops.
func TestThePromptContainsTheNote(t *testing.T) {
	raw := "---\ntitle: X\n---\n\nThe staging gate runs first.\n"
	if !strings.Contains(BuildPrompt(Request{Raw: raw}, []string{"fact"}), raw) {
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
	_ = BuildPrompt(Request{Raw: "n"}, []string{"a", "b", "c"})
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
