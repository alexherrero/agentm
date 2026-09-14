package note

import "testing"

// agentm-vault § Projects and tasks: the index keeps a project's `completed/`
// and ranks it at x0.30 by its path segment, penalized rather than excluded.
func TestAProjectsCompletedRecordEarnsTheClass(t *testing.T) {
	cases := map[string]bool{
		"projects/agentm/completed/research-note.md":     true,
		"projects/agentm/completed/deep/nested/brief.md": true,
		"projects/agentm/Completed/brief.md":             true,
		"projects/agentm/decisions/a-ruling.md":          false,
		"projects/agentm/completed.md":                   false,
		"projects/completed/x.md":                        false,
		"agent/memory/semantic/completed/x.md":           false,
	}
	for rel, want := range cases {
		got := false
		for _, f := range classify(rel, "", "a body", "", "") {
			if f == ClassCompleted {
				got = true
			}
		}
		if got != want {
			t.Errorf("classify(%q) completed = %v, want %v", rel, got, want)
		}
	}
}

func TestACompletedRecordIsWeightedLikeTheOtherDemotions(t *testing.T) {
	if w := Weights[ClassCompleted]; w != 0.30 {
		t.Errorf("Weights[ClassCompleted] = %v, want 0.30", w)
	}
	if m := Multiplier([]string{ClassCompleted}); m != 0.30 {
		t.Errorf("Multiplier(completed) = %v, want 0.30", m)
	}
}
