package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// The root map — `memory/mocs/moc-root.md`, the entry point, generated with
// the other maps. It lists every area's map: the memory map and the review
// queue beside it, each year's calendar map, and the `moc-*.md` an area keeps
// beside its files in `diagnostics/`, `standards/` and `Projects/`. A map
// counts when it is on disk or when this pass plans it. The page is rewritten
// only when that list changes, `updated` is the day it changed, and nothing
// here deletes.

const calendarMapPrefix = "moc-calendar-"

type rootArea struct {
	Name  string
	Slugs []string
}

// vaultRootOf is the vault root beside a nested memory root (`.obsidian/` at
// the parent and none at the root), else the root itself.
func vaultRootOf(root string) string {
	parent := filepath.Dir(root)
	if isDir(filepath.Join(parent, ".obsidian")) && !isDir(filepath.Join(root, ".obsidian")) {
		return parent
	}
	return root
}

// mapsIn is the slugs of the `<prefix>*.md` files directly in dir, sorted;
// nil when dir does not exist with exactly that spelling.
func mapsIn(dir, prefix string) []string {
	if !isDirExact(dir) {
		return nil
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	var out []string
	for _, e := range entries {
		name := e.Name()
		if e.IsDir() || !strings.HasPrefix(name, prefix) || !strings.HasSuffix(name, ".md") {
			continue
		}
		out = append(out, strings.TrimSuffix(name, ".md"))
	}
	sort.Strings(out)
	return out
}

// rootAreas is the area maps in the root map's order: memory, calendar
// (newest year first), diagnostics, standards, projects. An area with no map
// is left out.
func rootAreas(root string, planned []string) []rootArea {
	has := map[string]bool{}
	for _, rel := range planned {
		has[filepath.ToSlash(rel)] = true
	}
	var areas []rootArea
	add := func(name string, slugs []string) {
		if len(slugs) > 0 {
			areas = append(areas, rootArea{Name: name, Slugs: slugs})
		}
	}
	var memory []string
	for _, slug := range []string{MocMemorySlug, NeedsReviewSlug} {
		rel := MocRel(slug)
		if st, err := os.Stat(filepath.Join(root, filepath.FromSlash(rel))); has[rel] || (err == nil && !st.IsDir()) {
			memory = append(memory, slug)
		}
	}
	add("Memory", memory)
	years := map[string]bool{}
	if cal := CalendarRoot(root); cal != "" {
		for _, slug := range mapsIn(cal, calendarMapPrefix) {
			years[slug] = true
		}
	}
	for rel := range has {
		if base := filepath.Base(rel); strings.HasPrefix(base, calendarMapPrefix) && strings.HasSuffix(base, ".md") {
			years[strings.TrimSuffix(base, ".md")] = true
		}
	}
	var calendar []string
	for slug := range years {
		calendar = append(calendar, slug)
	}
	sort.Sort(sort.Reverse(sort.StringSlice(calendar)))
	add("Calendar", calendar)
	add("Diagnostics", mapsIn(filepath.Join(root, "diagnostics"), "moc-"))
	vault := vaultRootOf(root)
	add("Standards", mapsIn(filepath.Join(vault, "standards"), "moc-"))
	add("Projects", mapsIn(filepath.Join(vault, "Projects"), "moc-"))
	return areas
}

func renderRootMap(areas []rootArea, created, updated string) string {
	lines := []string{"---", "title: root — map of content", "kind: moc", "status: active",
		"created: " + created, "updated: " + updated, "tags: [moc, root]", "slug: " + MocRootSlug,
		"generated_by: " + mocGeneratedBy, "---", "", "# root", "",
		"The entry point: every area's map, generated nightly. Not edited by hand."}
	for _, a := range areas {
		lines = append(lines, "", "## "+a.Name, "")
		for _, slug := range a.Slugs {
			lines = append(lines, "- [["+slug+"]]")
		}
	}
	return mocJoin(append(lines, ""))
}

// PlanRootMap decides `moc-root.md` from the area maps on disk and the ones
// `planned` names (memory-root relative, the pages this pass writes). It
// writes nothing, and plans nothing when no area has a map.
func PlanRootMap(root string, planned []string, now time.Time) (MocsPlan, error) {
	var plan MocsPlan
	areas := rootAreas(root, planned)
	if len(areas) == 0 {
		return plan, nil
	}
	today := now.UTC().Format("2006-01-02")
	count := 0
	for _, a := range areas {
		count += len(a.Slugs)
	}
	rel := MocRel(MocRootSlug)
	before, created := mocCurrentPage(root, rel)
	if created == "" {
		created = today
	}
	if before != nil {
		fm, _ := ParseFrontmatter(string(before))
		if updated := strings.TrimSpace(fm["updated"]); updated != "" && string(before) == renderRootMap(areas, created, updated) {
			plan.Pages = append(plan.Pages, MocPage{Rel: rel, Members: count, Newest: updated})
			return plan, nil
		}
	}
	plan.add(MocPage{Rel: rel, Members: count, Newest: today}, before, renderRootMap(areas, created, today),
		fmt.Sprintf("the root map regenerated over %d area map(s)", count))
	return plan, nil
}
