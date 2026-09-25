package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// Job "mocs", over the two shared spaces the operator added on 2026-09-24
// (agentm-vault § The layout): `resources/`, the reference library, and
// `systems/`, the systems the operator runs. Each space gets one map at its
// root, `<space>/moc-<space>.md`, once it holds a note.
//
// The map has a section per folder directly under the space. A folder's own
// notes are listed by title while there are few of them; a subfolder is listed
// by title the same way, or counted once it holds more. `resources/topics/`
// runs to hundreds of reference cards, and a map listing every title would
// match nearly every query — the reason a project's map counts its research
// by bundle. `systems/` is small, so a system's overview and its components
// are listed whole.
//
// The rules the projects maps keep hold here too: a note is dated by its
// `created`, so the night's enrichment, which stamps `updated`, never reorders
// or rewrites a map; a page carries its newest input's date as `updated`, so a
// regeneration over unchanged inputs is byte-identical and writes nothing; a
// map of nothing is not written; and nothing here deletes.

const (
	ResourcesSpaceName = "resources"
	SystemsSpaceName   = "systems"
	// spaceMapListLimit is how many notes a folder may hold and still be
	// listed title by title; past it the folder is counted.
	spaceMapListLimit = 12
)

// sharedSpaces is the two spaces in the order the maps are planned, with the
// line each map opens on.
var sharedSpaces = []struct {
	Name string
	Text string
}{
	{ResourcesSpaceName, "The reference library: topic cards, the watchlist and study guides. " +
		"It ranks low in recall unless the question is about its topic. [[../index|index]] says who writes where."},
	{SystemsSpaceName, "The systems you run: each has an overview and its components, " +
		"or a front door to its repo's wiki. [[../index|index]] says who writes where."},
}

type spaceNote struct {
	Link string
	Date string
}

type spaceFolder struct {
	Name    string
	Notes   []spaceNote // notes directly in the folder, by title
	Subdirs []spaceFolder
	Total   int // every note at or below the folder
}

// spaceNoteAt reads one note for a map line: its link by title and its date.
func spaceNoteAt(vault, path string) (spaceNote, bool) {
	fm, body, ok := projReadNote(path)
	if !ok {
		return spaceNote{}, false
	}
	title := projTitle(fm, body)
	if title == "" {
		title = strings.TrimSuffix(filepath.Base(path), ".md")
	}
	return spaceNote{Link: projLink(vault, path, title), Date: projDate(fm["created"])}, true
}

// spaceSkip is a name the maps do not walk: hidden, underscored, or a
// generated map (a folder's own `moc-*.md` is the map's output, not a member).
func spaceSkip(name string) bool {
	return strings.HasPrefix(name, ".") || strings.HasPrefix(name, "_") || strings.HasPrefix(name, "moc-")
}

// readSpaceFolder reads a folder: its notes in title order, its subfolders by
// name, and every note's date into `dates`.
func readSpaceFolder(vault, dir string, dates *[]string) spaceFolder {
	f := spaceFolder{Name: filepath.Base(dir)}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return f
	}
	for _, e := range entries {
		name := e.Name()
		if spaceSkip(name) {
			continue
		}
		path := filepath.Join(dir, name)
		if e.IsDir() {
			sub := readSpaceFolder(vault, path, dates)
			if sub.Total > 0 {
				f.Subdirs = append(f.Subdirs, sub)
				f.Total += sub.Total
			}
			continue
		}
		if !strings.HasSuffix(name, ".md") {
			continue
		}
		n, ok := spaceNoteAt(vault, path)
		if !ok {
			continue
		}
		f.Notes = append(f.Notes, n)
		f.Total++
		*dates = append(*dates, n.Date)
	}
	sort.SliceStable(f.Notes, func(i, j int) bool { return f.Notes[i].Link < f.Notes[j].Link })
	sort.SliceStable(f.Subdirs, func(i, j int) bool { return f.Subdirs[i].Name < f.Subdirs[j].Name })
	return f
}

// spaceFolderLines renders a section's body: the folder's own notes, then each
// subfolder, listed while small and counted once large.
func spaceFolderLines(f spaceFolder, indent string) []string {
	var lines []string
	if len(f.Notes) > spaceMapListLimit {
		lines = append(lines, fmt.Sprintf("%s- %d %s", indent, len(f.Notes), mocPlural(len(f.Notes), "note", "notes")))
	} else {
		for _, n := range f.Notes {
			lines = append(lines, indent+"- "+n.Link)
		}
	}
	for _, sub := range f.Subdirs {
		if sub.Total > spaceMapListLimit {
			lines = append(lines, fmt.Sprintf("%s- `%s/` — %d %s", indent, sub.Name, sub.Total, mocPlural(sub.Total, "note", "notes")))
			continue
		}
		lines = append(lines, fmt.Sprintf("%s- `%s/`", indent, sub.Name))
		lines = append(lines, spaceFolderLines(sub, indent+"  ")...)
	}
	return lines
}

func renderSpaceMap(space, text string, top spaceFolder, created, updated string) string {
	if created == "" {
		created = updated
	}
	lines := []string{"---", "title: " + space + " — map of content", "kind: moc", "status: active",
		"created: " + created, "updated: " + updated, "tags: [moc, " + space + "]",
		"slug: moc-" + space, fmt.Sprintf("members: %d", top.Total), "generated_by: " + mocGeneratedBy,
		"---", "", "# " + space, "", "[[" + MocRootSlug + "]]", "", text, "",
		"Generated nightly from the space's notes; not edited by hand."}
	if len(top.Notes) > 0 {
		lines = append(lines, "", "## Notes", "")
		lines = append(lines, spaceFolderLines(spaceFolder{Notes: top.Notes}, "")...)
	}
	for _, sub := range top.Subdirs {
		lines = append(lines, "", "## "+sub.Name, "")
		lines = append(lines, spaceFolderLines(sub, "")...)
	}
	return mocJoin(append(lines, ""))
}

// PlanSpaceMaps decides the maps of the two shared spaces. It writes nothing,
// and plans nothing for a space that does not exist or holds no note.
func PlanSpaceMaps(root string, now time.Time) (MocsPlan, error) {
	plan := MocsPlan{BelowFloor: map[string]int{}}
	vault := vaultRootOf(root)
	today := now.UTC().Format("2006-01-02")
	for _, s := range sharedSpaces {
		dir := filepath.Join(vault, s.Name)
		if !isDirExact(dir) {
			continue
		}
		var dates []string
		top := readSpaceFolder(vault, dir, &dates)
		if top.Total == 0 {
			continue
		}
		updated := projNewest(dates...)
		if updated == "" {
			updated = today
		}
		rel := memoryRel(root, filepath.Join(dir, "moc-"+s.Name+".md"))
		before, created := mocCurrentPage(root, rel)
		plan.add(MocPage{Rel: rel, Members: top.Total, Newest: updated}, before,
			renderSpaceMap(s.Name, s.Text, top, created, updated),
			fmt.Sprintf("the %s map regenerated (%d %s)", s.Name, top.Total, mocPlural(top.Total, "note", "notes")))
	}
	return plan, nil
}
