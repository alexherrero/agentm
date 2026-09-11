package rules

import (
	"strings"
	"testing"
)

// The importance rubric is the paragraph under `## Importance`, read from the
// contract's prose at load (agentm-vault § Dreaming, Q8). It is prose, not a
// key in the block, so editing it moves what the next deep pass proposes and
// leaves the rules hash — and so the whole corpus's idempotency keys — alone.
func TestTheImportanceRubricIsReadFromTheContractsProse(t *testing.T) {
	clearEnv(t)
	def, err := Load("")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(def.ImportanceRubric, "1 to 10") ||
		strings.Contains(def.ImportanceRubric, "## ") {
		t.Fatalf("the packaged rubric did not read as one section: %q", def.ImportanceRubric)
	}

	edited := strings.Replace(Default(), "A 1 is residue, kept by accident.",
		"A 1 is residue.", 1)
	if edited == Default() {
		t.Fatal("the fixture edit did not apply; the rubric's wording moved")
	}
	r, err := parse(edited, "edited", false)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(r.ImportanceRubric, "A 1 is residue.") {
		t.Errorf("the edited rubric was not read: %q", r.ImportanceRubric)
	}
	if r.Hash != def.Hash {
		t.Error("editing the rubric changed the rules hash, which would re-owe every card a pass")
	}

	none, err := parse(strings.Replace(Default(), ImportanceHeading+"\n", "## Something else\n", 1),
		"none", false)
	if err != nil {
		t.Fatal(err)
	}
	if none.ImportanceRubric != "" {
		t.Errorf("a contract without the heading read a rubric: %q", none.ImportanceRubric)
	}
}
