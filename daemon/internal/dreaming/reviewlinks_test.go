package dreaming

import (
	"path/filepath"
	"strings"
	"testing"
)

// A review is built from the day's facet notes, so its day lines link them.
// It never links the bare-date day index: the design dropped it, and a day
// whose note arrived by a move has none, so the link would dangle.
func TestReviewDayLinesLinkTheFacetNotesNeverABareDate(t *testing.T) {
	_, vault := rootMapVault(t)
	writeAt(t, vault, "Calendar/2026/2026-08-25-meetings.md", "---\nkind: calendar-facet\n---\n\n09:00 — standup\n10:00 — review\n")
	writeAt(t, vault, "Calendar/2026/2026-08-25-diary.md", "---\nkind: calendar-facet\n---\n\n21:00 — a quiet evening\n")
	cal := filepath.Join(vault, "Calendar")
	want := "- 2026-08-25 — [[2026-08-25-meetings|meetings]] (2), [[2026-08-25-diary|diary]] (1)\n"
	for name, text := range map[string]string{
		"week":  RenderWeek(cal, Facets(nil), 2026, 35),
		"month": RenderMonth(cal, Facets(nil), 2026, 8),
	} {
		if !strings.Contains(text, want) {
			t.Errorf("the %s review lacks %q:\n%s", name, want, text)
		}
		if strings.Contains(text, "[[2026-08-25]]") || strings.Contains(text, "day indexes") {
			t.Errorf("the %s review still points at the day index:\n%s", name, text)
		}
	}
}
