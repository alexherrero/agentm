package rules

import (
	"reflect"
	"strings"
	"testing"
)

// notJudgment names every contract field the judgment hash leaves out, and
// why. A field in neither this table nor judgmentFields fails the test below,
// so a field added to the contract has to be placed on one side before it
// ships — the guard the plan's risk section asked for (task 181, #784).
var notJudgment = map[string]string{
	"Classes":              "which class directories the batch walks — the population, not the answer",
	"Routing":              "which class directory a type's cards live in — the population, not the answer",
	"RecordKinds":          "eligibility: a record is merged, never judged as a card",
	"DampenedSpaces":       "ranking only",
	"ModelExemptSpaces":    "eligibility: whether a model may read the note at all",
	"ContractExemptSpaces": "the shape gates only",
	"RecallExemptAreas":    "eligibility: the wall",
	"AlwaysLoadAreas":      "ranking only",
	"LifecycleOverrides":   "the lifecycle job only",
	"Retention":            "the night's own diagnostics only",
	"Warrants":             "the taxonomy growth rule only",
	"Thresholds":           "the confidence floor applies at each note's next judgment",
	"Lifecycles":           "the lifecycle job and the capture writers",
	"DefaultLifecycle":     "the capture writers",
	"Sources":              "trust stamping at capture",
	"Facets":               "the calendar",
}

func TestEveryContractFieldIsClassified(t *testing.T) {
	ty := reflect.TypeOf(block{})
	for i := 0; i < ty.NumField(); i++ {
		name := ty.Field(i).Name
		in, out := judgmentFields[name], notJudgment[name] != ""
		switch {
		case in && out:
			t.Errorf("%s is classified on both sides", name)
		case !in && !out:
			t.Errorf("contract field %s is not classified: decide whether an "+
				"enrichment judgment reads it, then add it to judgmentFields "+
				"(judgment.go) or to notJudgment here", name)
		}
	}
	for name := range judgmentFields {
		if _, ok := ty.FieldByName(name); !ok {
			t.Errorf("judgmentFields names %s, which the contract no longer has", name)
		}
	}
	// And the struct the hash marshals carries exactly those fields, so the
	// table and the hash cannot part.
	jt := reflect.TypeOf(judgment{})
	if jt.NumField() != len(judgmentFields) {
		t.Errorf("the judgment struct has %d fields, judgmentFields names %d",
			jt.NumField(), len(judgmentFields))
	}
	for i := 0; i < jt.NumField(); i++ {
		if !judgmentFields[jt.Field(i).Name] {
			t.Errorf("the judgment struct hashes %s, which judgmentFields does not name",
				jt.Field(i).Name)
		}
	}
}

// The verification the plan names: an edit to what enrichment reads moves the
// judgment hash, and an edit to anything else leaves it, while the whole
// contract's hash moves with every one of them.
func TestTheJudgmentHashMovesOnlyWithWhatAJudgmentReads(t *testing.T) {
	base, err := LoadFile(writeRules(t, t.TempDir(), validBlock))
	if err != nil {
		t.Fatal(err)
	}
	if base.JudgmentHash == "" || base.JudgmentHash == base.Hash {
		t.Fatalf("judgment hash %q beside contract hash %q", base.JudgmentHash, base.Hash)
	}
	for _, tc := range []struct {
		name, from, to string
		moves          bool
	}{
		{"a memory type added", "idea]\ndefault_type: preference\nrouting:\n",
			"idea, recipe]\ndefault_type: preference\nrouting:\n  recipe: memory/procedural\n", true},
		{"a deprecation added", "insight: idea}", "insight: idea, tip: fix}", true},
		{"the default type changed", "default_type: preference", "default_type: convention", true},
		{"dampened spaces", "facets: [meetings, diary]\n", "facets: [meetings, diary]\ndampened_spaces: [resources]\n", false},
		{"walled areas", "facets: [meetings, diary]\n", "facets: [meetings, diary]\nrecall_exempt_areas: [personal/Important Docs]\n", false},
		{"a threshold", "low_confidence: 0.65", "low_confidence: 0.5", false},
		{"routing", "fix: memory/procedural", "fix: memory/semantic", false},
		{"record kinds", "record_kinds: [brief, telemetry]", "record_kinds: [brief, telemetry, scorecard]", false},
		{"the type list reordered", "[preference, convention, reference, workflow, fix, idea]",
			"[convention, preference, reference, workflow, fix, idea]", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if !strings.Contains(validBlock, tc.from) {
				t.Fatalf("the fixture has no %q to edit", tc.from)
			}
			edited, err := LoadFile(writeRules(t, t.TempDir(),
				strings.Replace(validBlock, tc.from, tc.to, 1)))
			if err != nil {
				t.Fatal(err)
			}
			if edited.Hash == base.Hash {
				t.Fatalf("the edit did not change the whole contract's hash; the " +
					"case tests nothing")
			}
			if moved := edited.JudgmentHash != base.JudgmentHash; moved != tc.moves {
				t.Errorf("judgment hash moved = %v, want %v", moved, tc.moves)
			}
		})
	}
}

// Prose edits move neither hash; the rubric in particular stays out of both,
// as the session-3 ruling keeps it out of the contract's.
func TestTheRubricMovesNeitherHash(t *testing.T) {
	block := "```storage-rules\n" + validBlock + "```\n"
	a, err := ParseText("# Rules\n\n"+block+"\n## Importance\n\nOne way.\n", "a")
	if err != nil {
		t.Fatal(err)
	}
	b, err := ParseText("# Rules\n\n"+block+"\n## Importance\n\nAnother way entirely.\n", "b")
	if err != nil {
		t.Fatal(err)
	}
	if a.ImportanceRubric == b.ImportanceRubric {
		t.Fatal("the fixture's rubrics are the same; the case tests nothing")
	}
	if a.Hash != b.Hash || a.JudgmentHash != b.JudgmentHash {
		t.Error("editing the importance rubric moved a hash")
	}
}
