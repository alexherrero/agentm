package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// A period with nothing in it gets no review.
//
// Every weekly and monthly review under Calendar/2026/ — all ten of them — said
// "Nothing recorded this week. 0 of 7 days with entries." and nothing else. No
// facet file existed anywhere in the register, so the rollup had produced ten
// notes and zero records. A register whose only contents are notes saying it is
// empty reads, at a glance, like a register that is being kept.

// emptyRegister is a vault with a Calendar/ space and nothing in it.
func emptyRegister(t *testing.T) (root, calendarRoot string) {
	t.Helper()
	root = t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "Calendar", "2026"), 0o755); err != nil {
		t.Fatal(err)
	}
	calendarRoot = CalendarRoot(root)
	if calendarRoot == "" {
		t.Fatal("CalendarRoot found no register in the fixture")
	}
	return root, calendarRoot
}

// writeDayFacet plants one facet file with one timed entry — the shape
// NotesForDay looks for and phraseOf parses.
func writeDayFacet(t *testing.T, calendarRoot, day, facet, text string) {
	t.Helper()
	dir := filepath.Join(calendarRoot, day[:4])
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	body := "---\ntitle: " + day + "-" + facet + "\nkind: calendar-facet\n---\n\n09:00 — " + text + "\n"
	if err := os.WriteFile(filepath.Join(dir, day+"-"+facet+".md"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

// writeCorrection plants a correction note dated `day` that corrects an
// earlier one, named the way correctionsWrittenOn parses.
func writeCorrection(t *testing.T, calendarRoot, day, corrected, facet string) {
	t.Helper()
	dir := filepath.Join(calendarRoot, day[:4])
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	name := day + "-" + facet + "-corrects-" + corrected + ".md"
	body := "---\ntitle: correction\nkind: calendar-facet\n---\n\nThe earlier entry was wrong.\n"
	if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

var planDay = time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC)

func TestAnEmptyRegisterProducesNoReviews(t *testing.T) {
	root, _ := emptyRegister(t)

	plan, err := PlanCalendar(root, nil, planDay, 8)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Written) != 0 {
		t.Errorf("an empty register produced %d review(s): %v", len(plan.Written), plan.Written)
	}
	if plan.Refreshed != 0 {
		t.Errorf("refreshed %d periods that hold nothing", plan.Refreshed)
	}
}

func TestAWeekWithAnEntryIsStillWritten(t *testing.T) {
	// The other half of the claim: empty periods are skipped, not all of them.
	root, calendarRoot := emptyRegister(t)
	writeDayFacet(t, calendarRoot, "2026-08-26", "diary", "Something happened.")

	plan, err := PlanCalendar(root, nil, planDay, 8)
	if err != nil {
		t.Fatal(err)
	}
	var week, month bool
	for _, name := range plan.Written {
		if strings.Contains(name, "2026-W35") {
			week = true
		}
		if strings.Contains(name, "2026-08") {
			month = true
		}
	}
	if !week {
		t.Errorf("the week holding the entry was not written: %v", plan.Written)
	}
	if !month {
		t.Errorf("the month holding the entry was not written: %v", plan.Written)
	}
	for _, name := range plan.Written {
		if strings.Contains(name, "2026-W34") || strings.Contains(name, "2026-09") {
			t.Errorf("wrote %s, a period with nothing in it", name)
		}
	}
}

func TestAWeekWhoseOnlyContentIsACorrectionCounts(t *testing.T) {
	// A week whose only content is a correction to an earlier day is a week
	// something happened in, and the correction appears nowhere else.
	_, calendarRoot := emptyRegister(t)
	f := Facets(nil)

	if WeekHasContent(calendarRoot, f, 2026, 35) {
		t.Fatal("the fixture is not empty to begin with")
	}
	writeCorrection(t, calendarRoot, "2026-08-26", "2026-08-20", "diary")
	if !WeekHasContent(calendarRoot, f, 2026, 35) {
		t.Error("a week holding a correction reads as empty")
	}
}

func TestAMonthLinksOnlyWeeksThatWereWritten(t *testing.T) {
	// The failure this ordering prevents: a month written while its empty
	// weeks were skipped would carry rows linking review files that do not
	// exist. `planned` is set only for a week actually being written.
	root, calendarRoot := emptyRegister(t)
	writeDayFacet(t, calendarRoot, "2026-08-26", "diary", "Something happened.")
	f := Facets(nil)

	plan, err := PlanCalendar(root, nil, planDay, 8)
	if err != nil {
		t.Fatal(err)
	}
	for _, in := range plan.Intents {
		if !strings.Contains(in.Rel, "2026-08-review") {
			continue
		}
		for _, line := range strings.Split(string(in.After), "\n") {
			if !strings.HasPrefix(line, "- [[") {
				continue
			}
			end := strings.Index(line, "]]")
			if end < 0 {
				continue
			}
			key := strings.TrimSuffix(line[4:end], "-review")
			if !strings.Contains(key, "-W") {
				continue // a day row, not a week row
			}
			var y, w int
			if _, err := parseWeekKey(key, &y, &w); err != nil {
				t.Errorf("unparseable week link %q", key)
				continue
			}
			if !WeekHasContent(calendarRoot, f, y, w) {
				t.Errorf("the month links %s, a week that was never written", key)
			}
		}
	}
}

// parseWeekKey reads "2026-W35" into year and week.
func parseWeekKey(key string, y, w *int) (int, error) {
	return fmt.Sscanf(key, "%d-W%d", y, w)
}
