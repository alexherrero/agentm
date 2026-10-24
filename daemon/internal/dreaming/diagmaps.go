package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// Job "mocs", over `agent/diagnostics/` (task 193 step 2). The folder has kept
// a `moc-diagnostics.md` since the 2a migration seeded a stub there
// (`scripts/migrate/structural_2a.sh`), but nothing generated it, so the
// reports and scorecards beside it hung off no map (agentm-vault § Maps are
// named for what they map). This writes that page in place, keeping its
// `kind`, its slug and its own `created`.
//
// The page lists every note, family by family, newest first, each linked by
// its path (task 193 LD-1). Unlike the shared spaces' maps it never counts a
// folder in place of listing it: diagnostics rank low as a dampened space and
// stay out of entity extraction, so a full list pulls no recall, and the
// retention sweep bounds the families it keeps (about two hundred notes once
// its first deletion is approved). A family is a
// folder directly under `diagnostics/`. The dated reports at the folder's top,
// with the folders that open on their date, are listed as "Reports".
//
// The projects maps' rules hold here too: a note is dated by its `created`,
// else by the date its name or folder carries, so the night's enrichment,
// which stamps `updated`, never reorders the page; the page carries its newest
// note's date as `updated`, so a regeneration over unchanged notes writes
// nothing; a folder with no note gets no page; and nothing here deletes.

const (
	DiagnosticsDirName = "diagnostics"
	diagnosticsMapName = "moc-diagnostics.md"
	diagReportsSection = "Reports"
)

// diagFamilyOrder is the families in the order the page lists them, after
// the reports. A folder not named here follows them, by name.
var diagFamilyOrder = []string{"digests", "morning", "health", "lint", "dreaming", "migrations"}

type diagNote struct {
	Rel  string // vault-relative; orders notes of one date
	Line string
	Date string
}

type diagSection struct {
	Name  string
	Notes []diagNote
}

// diagDate is a note's `created`, else the first date its file name carries,
// else the nearest folder's, dashed (`2026-10-22-…`) or compact
// (`20261011-digest-monthly`, `20260904T073713-route/`); "" when none does.
// `rel` is relative to the diagnostics folder.
func diagDate(fm map[string]string, rel string) string {
	if d := projDate(fm["created"]); d != "" {
		return d
	}
	parts := strings.Split(strings.TrimSuffix(rel, ".md"), "/")
	for i := len(parts) - 1; i >= 0; i-- {
		for _, m := range dateInName.FindAllStringSubmatch(parts[i], -1) {
			if t, err := time.Parse("20060102", m[1]+m[2]+m[3]); err == nil {
				return t.Format("2006-01-02")
			}
		}
	}
	return ""
}

// diagNoteAt reads one note for a map line. Forty-nine scorecards share one
// title, so a line names its date unless the title already does.
func diagNoteAt(vault, diag, path string) (diagNote, bool) {
	fm, body, ok := projReadNote(path)
	if !ok {
		return diagNote{}, false
	}
	label := projTitle(fm, body)
	if label == "" {
		label = strings.TrimSuffix(filepath.Base(path), ".md")
	}
	date := diagDate(fm, projVaultRel(diag, path))
	line := "- " + projLink(vault, path, label)
	if date != "" && !strings.Contains(label, date) {
		line += " · " + date
	}
	return diagNote{Rel: projVaultRel(vault, path), Line: line, Date: date}, true
}

// opensOnDate says whether a name starts with a date, as a dated report's
// folder of working files does (`2026-10-09-memory-junk-review/`).
func opensOnDate(name string) bool {
	loc := dateInName.FindStringIndex(name)
	return loc != nil && loc[0] == 0
}

// readDiagFolder appends every note at or below dir.
func readDiagFolder(vault, diag, dir string, notes *[]diagNote) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return
	}
	for _, e := range entries {
		name := e.Name()
		if spaceSkip(name) {
			continue
		}
		path := filepath.Join(dir, name)
		if e.IsDir() {
			readDiagFolder(vault, diag, path, notes)
			continue
		}
		if !strings.HasSuffix(name, ".md") {
			continue
		}
		if n, ok := diagNoteAt(vault, diag, path); ok {
			*notes = append(*notes, n)
		}
	}
}

// sortDiagNotes orders a section newest first, undated notes last, and notes
// of one date by path.
func sortDiagNotes(notes []diagNote) {
	sort.SliceStable(notes, func(i, j int) bool {
		if notes[i].Date != notes[j].Date {
			return notes[i].Date > notes[j].Date
		}
		return notes[i].Rel < notes[j].Rel
	})
}

// readDiagnostics is the page's sections: the reports, then each family that
// holds a note.
func readDiagnostics(vault, diag string) []diagSection {
	entries, err := os.ReadDir(diag)
	if err != nil {
		return nil
	}
	var reports []diagNote
	families := map[string][]diagNote{}
	for _, e := range entries {
		name := e.Name()
		if spaceSkip(name) {
			continue
		}
		path := filepath.Join(diag, name)
		switch {
		case e.IsDir() && opensOnDate(name):
			readDiagFolder(vault, diag, path, &reports)
		case e.IsDir():
			var notes []diagNote
			readDiagFolder(vault, diag, path, &notes)
			if len(notes) > 0 {
				families[name] = notes
			}
		case strings.HasSuffix(name, ".md"):
			if n, ok := diagNoteAt(vault, diag, path); ok {
				reports = append(reports, n)
			}
		}
	}
	var sections []diagSection
	if len(reports) > 0 {
		sortDiagNotes(reports)
		sections = append(sections, diagSection{Name: diagReportsSection, Notes: reports})
	}
	var names []string
	known := map[string]bool{}
	for _, name := range diagFamilyOrder {
		known[name] = true
		if _, ok := families[name]; ok {
			names = append(names, name)
		}
	}
	var others []string
	for name := range families {
		if !known[name] {
			others = append(others, name)
		}
	}
	sort.Strings(others)
	for _, name := range append(names, others...) {
		sortDiagNotes(families[name])
		sections = append(sections, diagSection{Name: name, Notes: families[name]})
	}
	return sections
}

func renderDiagnosticsMap(sections []diagSection, members int, created, updated string) string {
	if created == "" {
		created = updated
	}
	lines := []string{"---", "title: diagnostics — map of content", "kind: moc", "status: active",
		"created: " + created, "updated: " + updated, "tags: [moc, diagnostics]",
		"slug: moc-diagnostics", fmt.Sprintf("members: %d", members), "generated_by: " + mocGeneratedBy,
		"---", "", "# diagnostics", "", "[[" + MocRootSlug + "]]", "",
		"The system's health surfaces: the dated reports, then a section per family, every note newest first. " +
			"Digests, morning notes, scorecards, lint reports and dreaming notes are kept for the days " +
			"the retention schedule names.", "",
		"Generated nightly from the folder's notes; not edited by hand."}
	for _, s := range sections {
		lines = append(lines, "", "## "+s.Name, "")
		for _, n := range s.Notes {
			lines = append(lines, n.Line)
		}
	}
	return mocJoin(append(lines, ""))
}

// PlanDiagnosticsMap decides `diagnostics/moc-diagnostics.md`. It writes
// nothing, and plans nothing when the folder does not exist or holds no note.
func PlanDiagnosticsMap(root string, now time.Time) (MocsPlan, error) {
	plan := MocsPlan{BelowFloor: map[string]int{}}
	diag := filepath.Join(root, DiagnosticsDirName)
	if !isDirExact(diag) {
		return plan, nil
	}
	sections := readDiagnostics(vaultRootOf(root), diag)
	members := 0
	var dates []string
	for _, s := range sections {
		members += len(s.Notes)
		for _, n := range s.Notes {
			dates = append(dates, n.Date)
		}
	}
	if members == 0 {
		return plan, nil
	}
	updated := projNewest(dates...)
	if updated == "" {
		updated = now.UTC().Format("2006-01-02")
	}
	rel := memoryRel(root, filepath.Join(diag, diagnosticsMapName))
	before, created := mocCurrentPage(root, rel)
	plan.add(MocPage{Rel: rel, Members: members, Newest: updated}, before,
		renderDiagnosticsMap(sections, members, created, updated),
		fmt.Sprintf("the diagnostics map regenerated (%d %s)", members, mocPlural(members, "note", "notes")))
	return plan, nil
}
