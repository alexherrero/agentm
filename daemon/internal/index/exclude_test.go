package index

import (
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// The recall_excludes wall, at the Index: every arm drops the rows before the k
// are chosen, so a walled row leaves its slot to a real hit, and nothing a
// caller sets lifts it.
func TestExcludedProjectsAreWalledOnEveryMode(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "projects/port/one.md", "one", "floodplain zoning")
	addNote(t, x, "projects/port/two.md", "two", "floodplain zoning")
	addNote(t, x, "projects/completed/port/old.md", "old", "floodplain zoning")
	stamped := note.Note{Rel: "memory/semantic/stamped.md", Title: "stamped", Body: "floodplain zoning",
		Project: "port", Captured: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), CapturedSource: "mtime"}
	if err := x.Upsert(stamped, 1, 17); err != nil {
		t.Fatal(err)
	}
	addNote(t, x, "memory/semantic/keeper.md", "keeper", "floodplain zoning")

	for _, mode := range []string{ModeAnd, ModeFusion, ModeHybrid} {
		q := Query{Text: "floodplain zoning", K: 1, Mode: mode, Surface: note.SurfaceMeasure,
			ExcludeProjects: []string{"port"}, IncludeArchived: true}
		out, err := x.Search(q)
		if err != nil {
			t.Fatal(err)
		}
		if len(out.Results) != 1 || out.Results[0].Path != "memory/semantic/keeper.md" {
			t.Errorf("%s: an excluded session got %+v", mode, out.Results)
		}
		if out.ExcludedHidden != 4 {
			t.Errorf("%s: the outcome counted %d walled rows, want 4", mode, out.ExcludedHidden)
		}
		q.ExcludeProjects, q.K = nil, 10
		if out, err = x.Search(q); err != nil {
			t.Fatal(err)
		}
		if len(out.Results) != 5 || out.ExcludedHidden != 0 {
			t.Errorf("%s: with no rule the search served %d rows and walled %d", mode, len(out.Results), out.ExcludedHidden)
		}
	}
}

// The named-entity page is fetched outside the ranking, so it never passed the
// arms' wall.
func TestWithoutExcludedDropsARowNoArmWalled(t *testing.T) {
	rows := []Result{{Path: "projects/port/entity.md"}, {Path: "memory/a.md"}, {Path: "memory/b.md", project: "port"}}
	got := WithoutExcluded(rows, []string{"port"})
	if len(got) != 1 || got[0].Path != "memory/a.md" {
		t.Fatalf("got %+v", got)
	}
	if len(WithoutExcluded(rows, nil)) != 3 {
		t.Fatalf("an empty rule dropped a row")
	}
}
