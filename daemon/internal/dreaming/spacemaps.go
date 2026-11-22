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
// A counted folder still gets its notes linked: it is given a map of its own
// beside them, `<space>/<path>/moc-<folder>.md`, which lists every note in the
// folder newest first under a link back up, and the counted line links that
// map. Without it a counted folder's cards had no link in or out (the graph
// review of 2026-11-21). The rule recurses: inside a folder's map a subfolder
// past the limit is counted and linked to a map of its own in turn. A section
// folder whose own notes are counted gets one the same way. The space's own
// map still never lists more than the limit's worth of titles from one folder.
// Once written, a folder's map is kept true while it stays on disk, and
// rewritten as an emptied page when its folder holds no note (foldermaps.go).
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
	{ResourcesSpaceName, "The reference library: topic cards and study guides. " +
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
	Dir     string
	Notes   []spaceNote // notes directly in the folder, by title
	Subdirs []spaceFolder
	Total   int // every note at or below the folder
	// MapAbs is the folder's own map when a map above counts it rather than
	// listing it, and MapLink the link to it, labelled `<name>/`.
	MapAbs  string
	MapLink string
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
// name, and every note's date into `dates`. A subfolder that holds no note but
// still has a map the night wrote goes into `emptied`.
func readSpaceFolder(vault, dir string, dates, emptied *[]string) spaceFolder {
	f := spaceFolder{Name: filepath.Base(dir), Dir: dir}
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
			sub := readSpaceFolder(vault, path, dates, emptied)
			if sub.Total > 0 {
				f.Subdirs = append(f.Subdirs, sub)
				f.Total += sub.Total
			} else if m := filepath.Join(path, "moc-"+name+".md"); keptFolderMap(m) {
				*emptied = append(*emptied, m)
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

// assignSpaceMaps gives a map of its own to each folder a map above would
// count: a section folder whose own notes are past the limit, or a folder
// below one that holds more than the limit at or below it. A map the night
// already wrote is kept whatever the folder holds now.
func assignSpaceMaps(vault string, f *spaceFolder, section bool) {
	abs := filepath.Join(f.Dir, "moc-"+f.Name+".md")
	if (section && len(f.Notes) > spaceMapListLimit) || (!section && f.Total > spaceMapListLimit) || keptFolderMap(abs) {
		f.MapAbs = abs
		f.MapLink = projLink(vault, f.MapAbs, f.Name+"/")
	}
	for i := range f.Subdirs {
		assignSpaceMaps(vault, &f.Subdirs[i], false)
	}
}

// countedLine is a counted folder's line: a link to its own map when it has
// one, else the bare count.
func countedLine(indent, name, mapLink string, n int) string {
	if mapLink != "" {
		return fmt.Sprintf("%s- %s — %d %s", indent, mapLink, n, mocPlural(n, "note", "notes"))
	}
	return fmt.Sprintf("%s- `%s/` — %d %s", indent, name, n, mocPlural(n, "note", "notes"))
}

// spaceFolderLines renders a section's body: the folder's own notes, then each
// subfolder, listed while small and counted once large or once it has a map of
// its own, a count linking that map.
func spaceFolderLines(f spaceFolder, indent string) []string {
	var lines []string
	if f.MapLink != "" {
		lines = append(lines, countedLine(indent, f.Name, f.MapLink, len(f.Notes)))
	} else if len(f.Notes) > spaceMapListLimit {
		lines = append(lines, fmt.Sprintf("%s- %d %s", indent, len(f.Notes), mocPlural(len(f.Notes), "note", "notes")))
	} else {
		for _, n := range f.Notes {
			lines = append(lines, indent+"- "+n.Link)
		}
	}
	for _, sub := range f.Subdirs {
		if sub.MapLink != "" || sub.Total > spaceMapListLimit {
			lines = append(lines, countedLine(indent, sub.Name, sub.MapLink, sub.Total))
			continue
		}
		lines = append(lines, fmt.Sprintf("%s- `%s/`", indent, sub.Name))
		lines = append(lines, spaceFolderLines(sub, indent+"  ")...)
	}
	return lines
}

// spaceNewestFirst is a folder's own notes newest `created` first, then by
// link, the order a project folder's map keeps.
func spaceNewestFirst(notes []spaceNote) []spaceNote {
	out := append([]spaceNote(nil), notes...)
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Date != out[j].Date {
			return out[i].Date > out[j].Date
		}
		return out[i].Link < out[j].Link
	})
	return out
}

// spaceDates is every note's date at or below a folder.
func spaceDates(f spaceFolder) []string {
	var out []string
	for _, n := range f.Notes {
		out = append(out, n.Date)
	}
	for _, sub := range f.Subdirs {
		out = append(out, spaceDates(sub)...)
	}
	return out
}

// spaceFolderMapLines renders a folder's own map body: every note in it newest
// first, then each subfolder, listed whole while small and linked to its own
// map once past the limit.
func spaceFolderMapLines(f spaceFolder, indent string) []string {
	var lines []string
	for _, n := range spaceNewestFirst(f.Notes) {
		line := indent + "- " + n.Link
		if n.Date != "" {
			line += " · " + n.Date
		}
		lines = append(lines, line)
	}
	for _, sub := range f.Subdirs {
		if sub.MapLink != "" {
			lines = append(lines, countedLine(indent, sub.Name, sub.MapLink, sub.Total))
			continue
		}
		lines = append(lines, fmt.Sprintf("%s- `%s/`", indent, sub.Name))
		lines = append(lines, spaceFolderMapLines(sub, indent+"  ")...)
	}
	return lines
}

// renderSpaceFolderMap renders a counted folder's own map. `where` is the
// folder's path inside the space, `topics/sqlite`; `up` links the map above.
func renderSpaceFolderMap(space, where string, f spaceFolder, up, created, updated string) string {
	if created == "" {
		created = updated
	}
	lines := []string{"---", "title: " + space + " " + where + "/ — map of content", "kind: moc", "status: active",
		"created: " + created, "updated: " + updated, "tags: [moc, " + space + "]",
		"slug: moc-" + f.Name, fmt.Sprintf("members: %d", f.Total), "generated_by: " + mocGeneratedBy,
		"---", "", "# " + where + "/", "", up, "",
		fmt.Sprintf("The %d %s in `%s/` of the %s space, newest first. Generated nightly from the folder's notes; not edited by hand.",
			f.Total, mocPlural(f.Total, "note", "notes"), where, space), ""}
	lines = append(lines, spaceFolderMapLines(f, "")...)
	return mocJoin(append(lines, ""))
}

// planSpaceFolderMaps plans the map of each folder given one, at or below f,
// in the tree's order. `up` links the nearest map above.
func planSpaceFolderMaps(plan *MocsPlan, root, vault, space string, f spaceFolder, up, today string) {
	if f.MapAbs != "" {
		where := strings.TrimPrefix(projVaultRel(vault, f.Dir), space+"/")
		updated := projNewest(spaceDates(f)...)
		if updated == "" {
			updated = today
		}
		rel := memoryRel(root, f.MapAbs)
		before, created := mocCurrentPage(root, rel)
		plan.add(MocPage{Rel: rel, Members: f.Total, Newest: updated}, before,
			renderSpaceFolderMap(space, where, f, up, created, updated),
			fmt.Sprintf("the map of %s/%s regenerated (%d %s)", space, where, f.Total, mocPlural(f.Total, "note", "notes")))
		up = projLink(vault, f.MapAbs, where+"/")
	}
	for _, sub := range f.Subdirs {
		planSpaceFolderMaps(plan, root, vault, space, sub, up, today)
	}
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
		var dates, emptied []string
		top := readSpaceFolder(vault, dir, &dates, &emptied)
		spaceUp := projLink(vault, filepath.Join(dir, "moc-"+s.Name+".md"), s.Name)
		for _, abs := range emptied {
			where := strings.TrimPrefix(projVaultRel(vault, filepath.Dir(abs)), s.Name+"/")
			rel := memoryRel(root, abs)
			before, _ := mocCurrentPage(root, rel)
			plan.add(MocPage{Rel: rel}, before,
				renderEmptiedMap(before, strings.TrimSuffix(filepath.Base(abs), ".md"), where+"/", spaceUp,
					fmt.Sprintf("`%s/` of the %s space holds no notes now, so this map lists none.", where, s.Name), today),
				fmt.Sprintf("the map of %s/%s emptied", s.Name, where))
		}
		if top.Total == 0 {
			continue
		}
		for i := range top.Subdirs {
			assignSpaceMaps(vault, &top.Subdirs[i], true)
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
		for _, sub := range top.Subdirs {
			planSpaceFolderMaps(&plan, root, vault, s.Name, sub, spaceUp, today)
		}
	}
	return plan, nil
}
