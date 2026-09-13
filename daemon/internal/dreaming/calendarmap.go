package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

// The year map — `moc-calendar-YYYY.md` beside each year directory at the
// calendar root: every facet note of the year and nothing else, newest day
// first under a heading per month. A facet note is `YYYY/YYYY-MM-DD-<facet>.md`
// for a day in that year and a facet the contract registers. The page carries
// the year's newest facet day as `updated`, so regenerating an unchanged year
// is byte-identical and writes nothing, and a year with no facet note gets no
// map.

var (
	yearDirRe   = regexp.MustCompile(`^\d{4}$`)
	facetNoteRe = regexp.MustCompile(`^(\d{4}-\d{2}-\d{2})-([a-z0-9][a-z0-9-]*)\.md$`)
)

// yearDirs is the year directories at the calendar root, sorted.
func yearDirs(calendarRoot string) []string {
	if calendarRoot == "" {
		return nil
	}
	entries, err := os.ReadDir(calendarRoot)
	if err != nil {
		return nil
	}
	var out []string
	for _, e := range entries {
		if e.IsDir() && yearDirRe.MatchString(e.Name()) {
			out = append(out, e.Name())
		}
	}
	sort.Strings(out)
	return out
}

// YearFacetNotes is a year's facet notes by stem, newest day first and in
// registry order within a day.
func YearFacetNotes(calendarRoot string, facets []string, year string) []string {
	rank := map[string]int{}
	for i, f := range facets {
		rank[f] = i
	}
	entries, err := os.ReadDir(filepath.Join(calendarRoot, year))
	if err != nil {
		return nil
	}
	type facetNote struct{ day, facet, stem string }
	var notes []facetNote
	for _, e := range entries {
		m := facetNoteRe.FindStringSubmatch(e.Name())
		if e.IsDir() || m == nil || !strings.HasPrefix(m[1], year+"-") {
			continue
		}
		if _, ok := rank[m[2]]; !ok {
			continue
		}
		if _, err := time.Parse("2006-01-02", m[1]); err != nil {
			continue
		}
		notes = append(notes, facetNote{day: m[1], facet: m[2], stem: strings.TrimSuffix(e.Name(), ".md")})
	}
	sort.SliceStable(notes, func(i, j int) bool {
		if notes[i].day != notes[j].day {
			return notes[i].day > notes[j].day
		}
		return rank[notes[i].facet] < rank[notes[j].facet]
	})
	out := make([]string, 0, len(notes))
	for _, n := range notes {
		out = append(out, n.stem)
	}
	return out
}

// RenderYearMap renders a year's map over its facet notes, in the order
// YearFacetNotes gives them. `created` is the page's own, preserved.
func RenderYearMap(year string, stems []string, created string) string {
	newest := stems[0][:10]
	if created == "" {
		created = newest
	}
	lines := []string{"---", "title: " + year + " — calendar map", "kind: moc", "status: active",
		"created: " + created, "updated: " + newest, "tags: [moc, calendar]", "slug: " + calendarMapPrefix + year,
		"generated_by: " + mocGeneratedBy, "---", "", "# " + year, "", "[[" + MocRootSlug + "]]", "",
		fmt.Sprintf("%d facet %s in %s, newest first. Generated from the files under `%s/`; not edited by hand.",
			len(stems), mocPlural(len(stems), "note", "notes"), year, year)}
	month := ""
	for _, stem := range stems {
		if m := stem[:7]; m != month {
			month = m
			lines = append(lines, "", "## "+month, "")
		}
		lines = append(lines, fmt.Sprintf("- [[%s]] — %s", stem, stem[11:]))
	}
	return mocJoin(append(lines, ""))
}
