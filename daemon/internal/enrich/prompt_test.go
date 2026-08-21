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

// The alias rule is the one thing that differs by trigger, and the difference is
// measured rather than preferred: invented aliases cost 3.85 points of R@5.
func TestTheAliasRuleDiffersByTrigger(t *testing.T) {
	eager := BuildPrompt(Request{Raw: "n", Trigger: TriggerEager}, []string{"fact"})
	batch := BuildPrompt(Request{Raw: "n", Trigger: TriggerBatch}, []string{"fact"})

	if !strings.Contains(eager, "phrasing the person actually used") {
		t.Error("the eager prompt does not permit asker phrasing")
	}
	if strings.Contains(batch, "phrasing the person actually used") {
		t.Error("the batch prompt permits asker phrasing, with no asker to take " +
			"it from")
	}
	if !strings.Contains(batch, "derivable from the note itself") {
		t.Error("the batch prompt does not require derivation")
	}
	// And the batch rule carries the measurement, so a future reader loosening
	// it knows what it costs.
	if !strings.Contains(batch, "3.85") {
		t.Error("the batch rule states a ban without its evidence")
	}
}

// The asking session's words are only ever sent at the eager trigger, because
// only then is there an asker. Sending them at batch would be sending whatever
// happened to be in a struct.
func TestAskerPhrasingOnlyTravelsAtTheEagerTrigger(t *testing.T) {
	req := Request{Raw: "n", AskerPhrasing: "remember how the staging gate works"}

	req.Trigger = TriggerEager
	if !strings.Contains(BuildPrompt(req, []string{"fact"}), "staging gate works") {
		t.Error("the eager prompt dropped the asker's phrasing")
	}
	req.Trigger = TriggerBatch
	if strings.Contains(BuildPrompt(req, []string{"fact"}), "staging gate works") {
		t.Error("the batch prompt carried asker phrasing")
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
