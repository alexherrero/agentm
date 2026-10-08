package enrich

import (
	"context"
	"errors"
	"testing"
)

func TestSameButStampsMasksOnlyTheStampsARejudgmentMayLeave(t *testing.T) {
	base := "---\ntitle: A\nsummary: One line.\nupdated: \"2026-09-01\"\nenriched_by: enrich/1\n" +
		"enriched_at: \"2026-09-01T09:00:00Z\"\nrules_hash: aaaa\n---\n\nThe body.\n"
	for _, tc := range []struct {
		name string
		next string
		same bool
	}{
		{"identical", base, true},
		{"only the masked stamps moved",
			"---\ntitle: A\nsummary: One line.\nupdated: \"2026-10-02\"\nenriched_by: enrich/1\n" +
				"enriched_at: \"2026-10-02T09:00:00Z\"\nrules_hash: bbbb\n---\n\nThe body.\n", true},
		{"the pass version moved: written, so PassDepth sees it",
			"---\ntitle: A\nsummary: One line.\nupdated: \"2026-09-01\"\nenriched_by: enrich/2\n" +
				"enriched_at: \"2026-09-01T09:00:00Z\"\nrules_hash: aaaa\n---\n\nThe body.\n", false},
		{"a real field moved",
			"---\ntitle: A\nsummary: Another line.\nupdated: \"2026-10-02\"\nenriched_by: enrich/1\n" +
				"enriched_at: \"2026-10-02T09:00:00Z\"\nrules_hash: bbbb\n---\n\nThe body.\n", false},
		{"the body moved",
			"---\ntitle: A\nsummary: One line.\nupdated: \"2026-09-01\"\nenriched_by: enrich/1\n" +
				"enriched_at: \"2026-09-01T09:00:00Z\"\nrules_hash: aaaa\n---\n\nThe body, edited.\n", false},
		{"a body line that reads like a stamp is not masked",
			base + "updated: in the body\n", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := SameButStamps(base, tc.next); got != tc.same {
				t.Errorf("SameButStamps = %v, want %v", got, tc.same)
			}
		})
	}
	// A note with no front matter compares whole.
	if SameButStamps("updated: x\nbody\n", "updated: y\nbody\n") {
		t.Error("lines outside any front matter were masked")
	}
}

// A project record with no frontmatter block is declined before any call:
// ComposeRecord would refuse it only after the model was paid, and on
// 2026-10-07 that cost a whole night (task 181).
func TestARecordWithNoFrontmatterIsDeclinedBeforeTheCall(t *testing.T) {
	g := DefaultEligibility(func(string) bool { return true })
	g.ProjectRecord = IsProjectRecord
	rel := "projects/agentm/decisions/no-front.md"
	if !IsProjectRecord(rel) {
		t.Fatalf("%s is not a project record; the test tests nothing", rel)
	}
	err := g.Check(context.Background(), Request{Rel: rel}, "# A decision\n\nNo frontmatter here.\n")
	if !errors.Is(err, ErrNotEligible) {
		t.Errorf("a record with no frontmatter was offered to the model: %v", err)
	}
	if err := g.Check(context.Background(), Request{Rel: rel},
		"---\nkind: decision\n---\n\n# A decision\n"); err != nil {
		t.Errorf("a record with frontmatter was declined: %v", err)
	}
}
