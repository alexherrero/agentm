package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// Job "calendar" — the rollup takeover, the port of calendar_rollups.py
// (and the slice of calendar_facets.py / calendar_index.py it reads). The
// register's weekly and monthly reviews are generated whether or not anyone
// remembered to want them: every closed ISO week in the last eight, the
// running month and the one before. A review carries the period's own last
// day as `created`/`updated`, so a regeneration on an unchanged period is
// byte-identical and the pass writes nothing; the Python layer renders the
// same bytes while the two overlap, which is what the recorded parity
// fixture asserts.

const (
	JobCalendar         = "calendar"
	CalendarSpace       = "Calendar"
	ReviewKind          = "calendar-review"
	DefaultRollupWeeks  = 8
	calendarPhraseChars = 120
)

var (
	calendarEntryRe = regexp.MustCompile(`(?m)^\d{2}:\d{2} — (.*)$`)
	defaultFacets   = []string{"meetings", "correspondence", "docs", "diary"}
	dowNames        = []string{"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
)

// CalendarRoot is calendar_facets.calendar_root: the `Calendar/` space
// beside the memory root when the memory root is nested inside a vault
// (`.obsidian/` at the parent, none at the root), else directly under the
// root; discovered never conjured — "" when no register exists.
func CalendarRoot(root string) string {
	if isDirExact(filepath.Join(root, CalendarSpace)) {
		return filepath.Join(root, CalendarSpace)
	}
	parent := filepath.Dir(root)
	if isDir(filepath.Join(parent, ".obsidian")) && !isDir(filepath.Join(root, ".obsidian")) {
		if isDirExact(filepath.Join(parent, CalendarSpace)) {
			return filepath.Join(parent, CalendarSpace)
		}
	}
	return ""
}

func isDir(p string) bool {
	st, err := os.Stat(p)
	return err == nil && st.IsDir()
}

// isDirExact is calendar_facets._is_dir_exact: the directory exists with
// exactly this spelling, so a case-insensitive disk does not conjure one.
func isDirExact(p string) bool {
	if !isDir(p) {
		return false
	}
	entries, err := os.ReadDir(filepath.Dir(p))
	if err != nil {
		return false
	}
	for _, e := range entries {
		if e.Name() == filepath.Base(p) {
			return true
		}
	}
	return false
}

// Facets is the contract's registry, in its order; the packaged default
// for a contract without one.
func Facets(r *rules.Rules) []string {
	if r != nil && len(r.Facets) > 0 {
		return append([]string(nil), r.Facets...)
	}
	return append([]string(nil), defaultFacets...)
}

// DayNote is one facet note that exists for a day.
type DayNote struct {
	Facet string
	Path  string
}

// NotesForDay is calendar_facets.notes_for_day: the facet notes that exist,
// in registry order — exactly the files, never a facet without one.
func NotesForDay(calendarRoot string, facets []string, day time.Time) []DayNote {
	if calendarRoot == "" {
		return nil
	}
	var out []DayNote
	for _, f := range facets {
		p := filepath.Join(calendarRoot, fmt.Sprintf("%04d", day.Year()), day.Format("2006-01-02")+"-"+f+".md")
		if st, err := os.Stat(p); err == nil && !st.IsDir() {
			out = append(out, DayNote{Facet: f, Path: p})
		}
	}
	return out
}

// phraseOf is calendar_index._phrase: the first entry's words cut on a
// word boundary, and the entry count.
func phraseOf(path string) (string, int) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return "", 0
	}
	_, body := ParseFrontmatter(string(raw))
	entries := calendarEntryRe.FindAllStringSubmatch(body, -1)
	if len(entries) == 0 {
		return "", 0
	}
	first := strings.TrimSpace(entries[0][1])
	if len(first) > calendarPhraseChars {
		cut := first[:calendarPhraseChars]
		if i := strings.LastIndex(cut, " "); i >= 0 {
			cut = cut[:i]
		}
		first = cut + " …"
	}
	return first, len(entries)
}

// Correction is a correction note dated a day: (facet, corrected day, stem).
type Correction struct {
	Facet     string
	Corrected string
	Stem      string
}

// correctionsWrittenOn is calendar_index._corrections_written_on.
func correctionsWrittenOn(calendarRoot string, day time.Time) []Correction {
	if calendarRoot == "" {
		return nil
	}
	dir := filepath.Join(calendarRoot, fmt.Sprintf("%04d", day.Year()))
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	prefix := day.Format("2006-01-02") + "-"
	var names []string
	for _, e := range entries {
		n := e.Name()
		if strings.HasPrefix(n, prefix) && strings.Contains(n, "-corrects-") && strings.HasSuffix(n, ".md") {
			names = append(names, n)
		}
	}
	sort.Strings(names)
	var out []Correction
	for _, n := range names {
		stem := strings.TrimSuffix(n, ".md")
		rest := stem[11:] // YYYY-MM-DD-<facet>-corrects-YYYY-MM-DD
		i := strings.LastIndex(rest, "-corrects-")
		if i < 0 {
			continue
		}
		out = append(out, Correction{Facet: rest[:i], Corrected: rest[i+len("-corrects-"):], Stem: stem})
	}
	return out
}

// isoWeekMonday is date.fromisocalendar(year, week, 1).
func isoWeekMonday(year, week int) time.Time {
	jan4 := time.Date(year, 1, 4, 0, 0, 0, 0, time.UTC)
	wd := int(jan4.Weekday())
	if wd == 0 {
		wd = 7
	}
	monday1 := jan4.AddDate(0, 0, -(wd - 1))
	return monday1.AddDate(0, 0, (week-1)*7)
}

// WeekDays is calendar_rollups.week_days.
func WeekDays(year, week int) []time.Time {
	monday := isoWeekMonday(year, week)
	out := make([]time.Time, 7)
	for i := range out {
		out[i] = monday.AddDate(0, 0, i)
	}
	return out
}

// MonthDays is calendar_rollups.month_days.
func MonthDays(year, month int) []time.Time {
	first := time.Date(year, time.Month(month), 1, 0, 0, 0, 0, time.UTC)
	next := first.AddDate(0, 1, 0)
	var out []time.Time
	for d := first; d.Before(next); d = d.AddDate(0, 0, 1) {
		out = append(out, d)
	}
	return out
}

func dayLine(calendarRoot string, facets []string, day time.Time) string {
	notes := NotesForDay(calendarRoot, facets, day)
	if len(notes) == 0 {
		return ""
	}
	var parts []string
	for _, n := range notes {
		_, count := phraseOf(n.Path)
		parts = append(parts, fmt.Sprintf("%s (%d)", n.Facet, count))
	}
	return fmt.Sprintf("- [[%s]] — %s", day.Format("2006-01-02"), strings.Join(parts, ", "))
}

func reviewFrontmatter(kindTag, period, key string, stamp time.Time, extra []string) []string {
	lines := []string{"---", "kind: " + ReviewKind, "status: active", "altitude: artifact",
		"created: " + stamp.Format("2006-01-02"), "updated: " + stamp.Format("2006-01-02"),
		"tags: [calendar, review, " + kindTag + "]", "group: calendar", "slug: " + key + "-review",
		"period: " + period, period + ": " + key}
	lines = append(lines, extra...)
	return append(lines, "generated_by: calendar_rollups.py", "---", "")
}

func joinReview(lines []string) string {
	return strings.TrimRight(strings.Join(lines, "\n"), "\n") + "\n"
}

// RenderWeek is calendar_rollups.render_week, byte for byte.
func RenderWeek(calendarRoot string, facets []string, year, week int) string {
	days := WeekDays(year, week)
	key := fmt.Sprintf("%04d-W%02d", year, week)
	lines := reviewFrontmatter("week", "week", key, days[6],
		[]string{"from: " + days[0].Format("2006-01-02"), "to: " + days[6].Format("2006-01-02")})
	lines = append(lines, fmt.Sprintf("# Week %s — %s to %s", key, days[0].Format("2006-01-02"), days[6].Format("2006-01-02")), "",
		"Generated from the week's day indexes; the facet notes are the source.", "")
	var filled, empty []string
	type dated struct {
		day time.Time
		c   Correction
	}
	var corrections []dated
	for _, d := range days {
		if line := dayLine(calendarRoot, facets, d); line != "" {
			filled = append(filled, line)
		} else {
			wd := int(d.Weekday()+6) % 7 // Monday = 0
			empty = append(empty, dowNames[wd])
		}
		for _, c := range correctionsWrittenOn(calendarRoot, d) {
			corrections = append(corrections, dated{d, c})
		}
	}
	if len(filled) > 0 {
		lines = append(lines, "## Days", "")
		lines = append(lines, filled...)
		lines = append(lines, "")
	}
	if len(empty) > 0 {
		if len(empty) < 7 {
			lines = append(lines, "Nothing recorded on "+strings.Join(empty, ", ")+".", "")
		} else {
			lines = append(lines, "Nothing recorded this week.", "")
		}
	}
	if len(corrections) > 0 {
		lines = append(lines, "## Corrections", "")
		for _, c := range corrections {
			lines = append(lines, fmt.Sprintf("- [[%s]] — corrects %s (%s)", c.c.Stem, c.c.Corrected, c.c.Facet))
		}
		lines = append(lines, "")
	}
	lines = append(lines, fmt.Sprintf("%d of 7 days with entries.", len(filled)), "")
	return joinReview(lines)
}

// RenderMonth is calendar_rollups.render_month, byte for byte, against the
// week reviews on disk.
func RenderMonth(calendarRoot string, facets []string, year, month int) string {
	return renderMonth(calendarRoot, facets, year, month, nil)
}

// renderMonth renders with `planned` counting as existing — the pass writes
// the week reviews before the month ones, as catch_up does, so a month
// rendered in the same pass links them.
func renderMonth(calendarRoot string, facets []string, year, month int, planned map[string]bool) string {
	days := MonthDays(year, month)
	key := fmt.Sprintf("%04d-%02d", year, month)
	last := days[len(days)-1]
	lines := reviewFrontmatter("month", "month", key, last,
		[]string{"from: " + days[0].Format("2006-01-02"), "to: " + last.Format("2006-01-02")})
	lines = append(lines, "# "+key, "", "Generated from the month's day indexes and week reviews; the facet notes are the source.", "")
	type yw struct{ y, w int }
	var weeks []yw
	seen := map[yw]bool{}
	for _, d := range days {
		y, w := d.ISOWeek()
		if !seen[yw{y, w}] {
			seen[yw{y, w}] = true
			weeks = append(weeks, yw{y, w})
		}
	}
	lines = append(lines, "## Weeks", "")
	for _, k := range weeks {
		wkey := fmt.Sprintf("%04d-W%02d", k.y, k.w)
		exists := planned[wkey]
		if !exists && calendarRoot != "" {
			if st, err := os.Stat(filepath.Join(calendarRoot, fmt.Sprintf("%04d", k.y), wkey+"-review.md")); err == nil && !st.IsDir() {
				exists = true
			}
		}
		var inMonth []time.Time
		for _, d := range WeekDays(k.y, k.w) {
			if int(d.Month()) == month && d.Year() == year {
				inMonth = append(inMonth, d)
			}
		}
		n := 0
		for _, d := range inMonth {
			if len(NotesForDay(calendarRoot, facets, d)) > 0 {
				n++
			}
		}
		label := wkey
		if exists {
			label = "[[" + wkey + "-review]]"
		}
		lines = append(lines, fmt.Sprintf("- %s — %d of %d days with entries", label, n, len(inMonth)))
	}
	lines = append(lines, "")
	var filled []string
	for _, d := range days {
		if line := dayLine(calendarRoot, facets, d); line != "" {
			filled = append(filled, line)
		}
	}
	if len(filled) > 0 {
		lines = append(lines, "## Days", "")
		lines = append(lines, filled...)
		lines = append(lines, "")
	}
	lines = append(lines, fmt.Sprintf("%d of %d days with entries.", len(filled), len(days)), "")
	return joinReview(lines)
}

// CalendarPlan is what the rollup takeover would (or did) write.
type CalendarPlan struct {
	Intents   []Intent `json:"-"`
	Written   []string `json:"written"`
	Refreshed int      `json:"refreshed"`
	Skipped   string   `json:"skipped,omitempty"`
}

// PlanCalendar is calendar_rollups.catch_up as intents: a review is an
// intent only when its text differs from the file (or the file is missing);
// an unchanged period costs nothing. `today` bounds the closed weeks.
func PlanCalendar(root string, r *rules.Rules, today time.Time, weeks int) (CalendarPlan, error) {
	var plan CalendarPlan
	calendarRoot := CalendarRoot(root)
	if calendarRoot == "" {
		plan.Skipped = "no Calendar/ space"
		return plan, nil
	}
	if weeks <= 0 {
		weeks = DefaultRollupWeeks
	}
	facets := Facets(r)
	today = time.Date(today.Year(), today.Month(), today.Day(), 0, 0, 0, 0, time.UTC)
	relOf := func(p string) string {
		rel, err := filepath.Rel(root, p)
		if err != nil {
			return p
		}
		return filepath.ToSlash(rel)
	}
	consider := func(target, text string) {
		plan.Refreshed++
		cur, err := os.ReadFile(target)
		if err == nil && string(cur) == text {
			return
		}
		var before []byte
		if err == nil {
			before = cur
		}
		plan.Written = append(plan.Written, filepath.Base(target))
		plan.Intents = append(plan.Intents, Intent{Job: JobCalendar, Rel: relOf(target), Before: before, After: []byte(text),
			Summary: "calendar review " + strings.TrimSuffix(filepath.Base(target), ".md") + " regenerated"})
	}
	y, w := today.ISOWeek()
	cursor := isoWeekMonday(y, w)
	planned := map[string]bool{}
	for i := 1; i <= weeks; i++ {
		monday := cursor.AddDate(0, 0, -7*i)
		wy, ww := monday.ISOWeek()
		if !WeekDays(wy, ww)[6].Before(today) {
			continue // still open
		}
		key := fmt.Sprintf("%04d-W%02d", wy, ww)
		planned[key] = true
		consider(filepath.Join(calendarRoot, fmt.Sprintf("%04d", wy), key+"-review.md"), RenderWeek(calendarRoot, facets, wy, ww))
	}
	prev := time.Date(today.Year(), today.Month(), 1, 0, 0, 0, 0, time.UTC).AddDate(0, 0, -1)
	for _, m := range [][2]int{{prev.Year(), int(prev.Month())}, {today.Year(), int(today.Month())}} {
		key := fmt.Sprintf("%04d-%02d", m[0], m[1])
		consider(filepath.Join(calendarRoot, fmt.Sprintf("%04d", m[0]), key+"-review.md"), renderMonth(calendarRoot, facets, m[0], m[1], planned))
	}
	return plan, nil
}
