package rules

import (
	"reflect"
	"testing"
)

// The list the operator ruled on 2026-10-30, kind by kind. A change here is a
// change to that ruling.
func TestTheShippedContractNamesWhatFades(t *testing.T) {
	r, err := Load("")
	if err != nil {
		t.Fatalf("the shipped contract does not parse: %v", err)
	}
	want := []string{
		"agent/memory/episodic",
		"agent/memory/entities/issues",
		"agent/memory/entities/releases",
		"agent/diagnostics",
		"calendar",
		"resources/briefs",
		"projects/*/cleanup",
	}
	if !reflect.DeepEqual(r.FadeAreas, want) {
		t.Errorf("fade_areas = %v, want %v", r.FadeAreas, want)
	}
	if v, ok := r.Threshold("project_activity_ranks"); !ok || v != 0 {
		t.Errorf("project_activity_ranks = %v (%v), want 0", v, ok)
	}
	dormant, _ := r.Threshold("dormant_after_days")
	archive, _ := r.Threshold("archive_after_days")
	if dormant != archive {
		t.Errorf("a card sinks at %v and archives at %v; the ruling is that it does not sink first",
			dormant, archive)
	}
}

func TestAStarInAnAreaIsAnyOneSegment(t *testing.T) {
	areas := []string{"projects/*/cleanup"}
	for rel, want := range map[string]bool{
		"projects/a/cleanup/run.md":           true,
		"projects/B/Cleanup/deep/run.md":      true,
		"projects/cleanup/run.md":             false,
		"projects/a/b/cleanup/run.md":         false,
		"projects/a/cleanup.md":               false,
		"projects/a/completed/cleanup/run.md": false,
	} {
		if got := InArea(rel, areas); got != want {
			t.Errorf("InArea(%q) = %v, want %v", rel, got, want)
		}
	}
}
