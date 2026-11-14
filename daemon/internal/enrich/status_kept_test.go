package enrich

import "testing"

// Task 204: a judgment never rewrites a status a note has from its state.
func TestAJudgmentKeepsAStatusTheNotesStateGaveIt(t *testing.T) {
	for _, status := range []string{"merged", "archived", "superseded"} {
		card := "---\nstatus: " + status + "\nenriched_at: \"2026-01-01T00:00:00Z\"\n---\n\nBody.\n"
		high := VerdictFor(card, Response{Confidence: 0.9}, 0.65)
		if high.Status != status || high.FilingConfidence != "high" || high.Sank {
			t.Errorf("%s at 0.9: %+v", status, high)
		}
		low := VerdictFor(card, Response{Confidence: 0.2}, 0.65)
		if low.Status != status || low.FilingConfidence != "low" || low.Sank {
			t.Errorf("%s at 0.2: %+v, want the status kept and no sinking", status, low)
		}
	}
	// Every other status is judged as before.
	if v := VerdictFor("---\nstatus: unfiled\n---\n", Response{Confidence: 0.9}, 0.65); v.Status != "active" {
		t.Errorf("an unfiled card judged above the floor: %+v", v)
	}
	if v := VerdictFor("---\nstatus: active\n---\n", Response{Confidence: 0.2}, 0.65); v.Status != "unfiled" {
		t.Errorf("an active card judged below the floor: %+v", v)
	}
}
