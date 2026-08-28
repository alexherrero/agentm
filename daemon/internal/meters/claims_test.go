package meters

import (
	"reflect"
	"strings"
	"testing"
)

func TestASentenceIsAClaim(t *testing.T) {
	got := Claims("The daemon reads the index. The writer never touches it.")
	want := []string{
		"The daemon reads the index.",
		"The writer never touches it.",
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestEachListItemIsItsOwnClaim(t *testing.T) {
	got := Claims("- the first thing that happened\n- the second thing that happened")
	if len(got) != 2 {
		t.Fatalf("got %d claims, want 2: %q", len(got), got)
	}
	if strings.Contains(got[0], "second") {
		t.Fatalf("the line break was crossed: %q", got[0])
	}
}

func TestAListMarkerIsNotPartOfTheClaim(t *testing.T) {
	got := Claims("* the flag defaults to false here")
	if len(got) != 1 || strings.HasPrefix(got[0], "*") {
		t.Fatalf("marker survived: %q", got)
	}
}

func TestADecimalDoesNotEndASentence(t *testing.T) {
	// "novelty 0. 70" would be two claims and one of them nonsense.
	got := Claims("The novelty threshold is 0.70 for this corpus.")
	if len(got) != 1 {
		t.Fatalf("split inside a number: %q", got)
	}
}

func TestAnAbbreviationDoesNotEndASentence(t *testing.T) {
	got := Claims("Some gates are advisory, e.g. The kind taxonomy is report-only.")
	if len(got) != 1 {
		t.Fatalf("split after an abbreviation: %q", got)
	}
}

func TestALowercaseContinuationDoesNotEndASentence(t *testing.T) {
	// A version string mid-sentence: "vllm 0.8.5+cu118 requires transformers".
	got := Claims("The wheel is vllm-0.8.5. whl and it requires transformers.")
	if len(got) != 1 {
		t.Fatalf("split before a lowercase word: %q", got)
	}
}

func TestAFragmentIsNotAClaim(t *testing.T) {
	// Headings and the tails the capture bug left behind.
	if got := Claims("## Release\n\nok\n\nthree word tail"); len(got) != 0 {
		t.Fatalf("a fragment became a claim: %q", got)
	}
}

func TestFencedCodeIsNotAClaim(t *testing.T) {
	body := "The installer needs a flag.\n\n```bash\npip install the thing here\n```\n"
	got := Claims(body)
	for _, c := range got {
		if strings.Contains(c, "pip install") {
			t.Fatalf("code became a claim: %q", got)
		}
	}
	if len(got) != 1 {
		t.Fatalf("got %d claims, want the one sentence: %q", len(got), got)
	}
}

func TestALinkKeepsItsWordsAndLosesItsURL(t *testing.T) {
	got := Claims("It is supported in [vLLM](https://docs.vllm.ai/x) already now.")
	if len(got) != 1 || strings.Contains(got[0], "docs.vllm.ai") {
		t.Fatalf("URL survived into a claim: %q", got)
	}
	if !strings.Contains(got[0], "vLLM") {
		t.Fatalf("the link's words were lost: %q", got)
	}
}

func TestTheOrderIsPreserved(t *testing.T) {
	// The judge is asked about claim numbers, so the numbering has to mean the
	// same thing on both sides of the call.
	got := Claims("First the alpha happened here. Then the beta happened here. " +
		"Finally the gamma happened here.")
	if len(got) != 3 {
		t.Fatalf("got %d claims, want 3: %q", len(got), got)
	}
	for i, want := range []string{"alpha", "beta", "gamma"} {
		if !strings.Contains(got[i], want) {
			t.Fatalf("claim %d is %q, wanted the one with %q", i, got[i], want)
		}
	}
}

func TestAStructuredLineIsAClaimBelowTheWordFloor(t *testing.T) {
	// The v1 defect. A mining-metadata block is four assertions, each under the
	// word floor, so all four were dropped before the judge saw them — and the
	// notes whose rewrites lost exactly these were the ones the operator graded
	// down. See fixtures/completeness-v1/DIAGNOSIS.md.
	body := "- **Category**: `idea`\n- **Confidence**: `LOW`\n" +
		"- **Occurrences**: 5\n- **Rationale**: follow-up marker\n"
	got := Claims(body)
	if len(got) != 4 {
		t.Fatalf("got %d claims, want 4: %q", len(got), got)
	}
	for _, want := range []string{"Confidence", "LOW", "Occurrences", "5"} {
		if !strings.Contains(strings.Join(got, " "), want) {
			t.Fatalf("%q was dropped: %q", want, got)
		}
	}
}

func TestAPlainKeyValueLineIsAClaim(t *testing.T) {
	got := Claims("Status: done\n")
	if len(got) != 1 {
		t.Fatalf("got %d claims, want 1: %q", len(got), got)
	}
}

func TestAStructuredValueIsNotSplitAtItsPeriods(t *testing.T) {
	// `**Version**: 1.2. See below.` is one assertion; sentence-splitting it
	// would file half a version number as a claim of its own.
	got := Claims("- **Model**: claude-opus-4. Something else entirely here.\n")
	if len(got) != 1 {
		t.Fatalf("a structured line was split: %q", got)
	}
	// On content, not on the count: splitting this line also yields one claim,
	// because the structured half then falls under the word floor and only the
	// trailing sentence survives. Counting alone could not tell the two apart.
	if !strings.Contains(got[0], "Model") || !strings.Contains(got[0], "opus-4") {
		t.Fatalf("the structured half was dropped: %q", got)
	}
}

func TestAHeadingWithAColonIsStillNotAClaim(t *testing.T) {
	// The exemption requires a value. Admitting a bare `## Contents:` would
	// readmit exactly the headings the word floor exists to exclude.
	if got := Claims("## Contents:\n\nNote:\n"); len(got) != 0 {
		t.Fatalf("a valueless heading became a claim: %q", got)
	}
}

func TestAnEmptyBodyHasNoClaims(t *testing.T) {
	if got := Claims("   \n\n  "); len(got) != 0 {
		t.Fatalf("got %q, want none", got)
	}
}
