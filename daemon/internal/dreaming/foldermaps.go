package dreaming

import (
	"os"
	"strings"
)

// A folder's own map, in a project (`research/moc-research.md`) or a shared
// space (`resources/topics/sqlite/moc-sqlite.md`), is first written when its
// folder holds more than spaceMapListLimit notes. From then on it is kept true
// for as long as it stays on disk (agentm-vault § The project skeleton, amended
// 2026-11-21):
//
//   - it is regenerated every night from what its folder holds now, whatever
//     the count, and the map above keeps linking it, so a folder that hovers
//     at the limit does not flip between listed and linked;
//   - a project folder's map also lists the notes that moved from the folder
//     to the project's `completed/` mirror, the place the night's lifecycle
//     move takes finished records, so a move never leaves them unlinked;
//   - once the folder, and its mirror, hold no note, the map is rewritten as
//     an emptied page: no links, `status: archived`, `members: 0`.
//
// Nothing here deletes a map; removing an emptied one is the operator's hand.
// Before this rule a map was planned only while its folder was past the limit,
// so a folder that shrank or emptied left its map on disk, never rewritten,
// linking notes where they no longer were (`desk/briefs/moc-briefs.md`, found
// by the graph review of 2026-11-21).
//
// Only a page the night wrote is kept: one whose frontmatter says
// `generated_by: agentmdream`. A hand page of the same name is left alone.

// keptFolderMap says whether abs is a folder map the night wrote and still
// keeps.
func keptFolderMap(abs string) bool {
	raw, err := os.ReadFile(abs)
	if err != nil {
		return false
	}
	fm, _ := ParseFrontmatter(strings.ReplaceAll(string(raw), "\r\n", "\n"))
	return strings.TrimSpace(fm["generated_by"]) == mocGeneratedBy
}

// renderEmptiedMap renders a kept map whose folder holds no note now. It keeps
// the page's own title, tags and dates, so a second night over the same empty
// folder renders the same bytes.
func renderEmptiedMap(before []byte, slug, heading, up, sentence, today string) string {
	fm, _ := ParseFrontmatter(strings.ReplaceAll(string(before), "\r\n", "\n"))
	field := func(k, fallback string) string {
		if v := strings.TrimSpace(fm[k]); v != "" {
			return v
		}
		return fallback
	}
	updated := field("updated", today)
	created := field("created", updated)
	lines := []string{"---", "title: " + field("title", heading+" — map of content"), "kind: moc", "status: archived",
		"created: " + created, "updated: " + updated, "tags: " + field("tags", "[moc]"),
		"slug: " + field("slug", slug), "members: 0", "generated_by: " + mocGeneratedBy,
		"---", "", "# " + heading, "", up, "", sentence,
		"", "The night keeps this page rather than deleting it; remove it by hand if the folder is gone for good."}
	return mocJoin(append(lines, ""))
}
