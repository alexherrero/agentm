package dreaming

import (
	"fmt"
	"strings"
	"testing"
	"time"
)

const facetBody = "---\nkind: calendar-facet\n---\n\n09:00 — an entry\n"

func yearMapText(plan CalendarPlan, year string) string {
	for _, in := range plan.Intents {
		if strings.HasSuffix(in.Rel, "Calendar/"+calendarMapPrefix+year+".md") {
			return string(in.After)
		}
	}
	return ""
}

func TestYearMapListsEveryFacetNoteOfTheYearAndNothingElse(t *testing.T) {
	root, vault := rootMapVault(t)
	for _, name := range []string{"2026-08-10-diary.md", "2026-08-10-meetings.md", "2026-09-01-docs.md"} {
		writeAt(t, vault, "Calendar/2026/"+name, facetBody)
	}
	for _, name := range []string{"2026-W36-review.md", "2026-09-review.md", "notes.md", "2025-12-31-diary.md",
		"2026-08-11-lunch.md", "2026-13-40-diary.md"} {
		writeAt(t, vault, "Calendar/2026/"+name, "---\ntitle: not a facet note of the year\n---\n")
	}
	plan, err := PlanCalendar(root, nil, time.Date(2026, 9, 13, 9, 0, 0, 0, time.UTC), DefaultRollupWeeks)
	if err != nil {
		t.Fatal(err)
	}
	text := yearMapText(plan, "2026")
	want := "# 2026\n\n[[moc-root]]\n\n3 facet notes in 2026, newest first. Generated from the files under `2026/`; not edited by hand.\n\n" +
		"## 2026-09\n\n- [[2026-09-01-docs]] — docs\n\n" +
		"## 2026-08\n\n- [[2026-08-10-meetings]] — meetings\n- [[2026-08-10-diary]] — diary\n"
	if !strings.HasSuffix(text, want) {
		t.Errorf("year map:\n%s\nwant it to end with:\n%s", text, want)
	}
	for _, line := range []string{"kind: moc\n", "slug: moc-calendar-2026\n", "created: 2026-09-01\n", "updated: 2026-09-01\n"} {
		if !strings.Contains(text, line) {
			t.Errorf("year map frontmatter lacks %q:\n%s", line, text)
		}
	}
	for _, unwanted := range []string{"W36", "2026-09-review", "[[notes]]", "2025-12-31", "lunch", "2026-13-40", "group:"} {
		if strings.Contains(text, unwanted) {
			t.Errorf("year map carries %q:\n%s", unwanted, text)
		}
	}
	if len(plan.YearMaps) != 1 || plan.YearMaps[0] != "../Calendar/moc-calendar-2026.md" {
		t.Errorf("the pass reports the year map it keeps, memory-root relative: %v", plan.YearMaps)
	}
	if len(plan.MapsWritten) != 1 || plan.MapsWritten[0] != "moc-calendar-2026.md" {
		t.Errorf("the rewritten year map is named apart from the reviews: %v", plan.MapsWritten)
	}
	for _, name := range plan.Written {
		if strings.HasPrefix(name, calendarMapPrefix) {
			t.Errorf("Written is the reviews alone, and it names a map: %v", plan.Written)
		}
	}
}

func TestYearMapRegeneratesByteIdentically(t *testing.T) {
	root, vault := rootMapVault(t)
	writeAt(t, vault, "Calendar/2026/2026-08-10-diary.md", facetBody)
	now := time.Date(2026, 9, 13, 9, 0, 0, 0, time.UTC)
	first, _ := PlanCalendar(root, nil, now, DefaultRollupWeeks)
	if yearMapText(first, "2026") == "" {
		t.Fatalf("the first pass writes the year map: %v", first.MapsWritten)
	}
	j, _ := OpenJournal(t.TempDir())
	for i, in := range first.Intents {
		if _, err := j.Commit(root, "c", fmt.Sprintf("c-%d", i), in, now); err != nil {
			t.Fatal(err)
		}
	}
	again, _ := PlanCalendar(root, nil, now.AddDate(0, 0, 1), DefaultRollupWeeks)
	if text := yearMapText(again, "2026"); text != "" {
		t.Errorf("an unchanged year regenerates nothing, got:\n%s", text)
	}
	if len(again.YearMaps) != 1 {
		t.Errorf("a kept map is still reported for the root map: %v", again.YearMaps)
	}
}

func TestNoYearMapForAYearWithoutAFacetNote(t *testing.T) {
	root, vault := rootMapVault(t)
	writeAt(t, vault, "Calendar/2027/2027-W01-review.md", "---\nkind: calendar-review\n---\n")
	plan, _ := PlanCalendar(root, nil, time.Date(2027, 1, 13, 9, 0, 0, 0, time.UTC), DefaultRollupWeeks)
	if text := yearMapText(plan, "2027"); text != "" || len(plan.YearMaps) != 0 {
		t.Errorf("a year with no facet note gets no map: %v\n%s", plan.YearMaps, text)
	}
}
