package dreaming

import (
	"fmt"
	"sort"
	"strings"
	"time"
)

// The entity map (task 179): the mocs job lists the builder's four folders in
// one generated page beside the other maps, so the root map reaches every
// entity page in two clicks. Rendered from what the builder planned tonight,
// not from a walk, so the map and the pages cannot disagree.

// MocEntitiesSlug is the entity map's name in the maps' class directory.
const MocEntitiesSlug = "moc-entities"

// entityHeadings are the map's sections, in the order they read.
var entityHeadings = []struct{ kind, heading string }{
	{"person", "People"}, {"repo", "Repositories"}, {"issue", "Issues"}, {"release", "Releases"},
}

// PlanEntityMap decides the entity map. With no pages it removes a map it
// generated before; it never touches a page nothing generated.
func PlanEntityMap(root string, pages []EntityPage, now time.Time) MocsPlan {
	plan := MocsPlan{BelowFloor: map[string]int{}}
	rel := MocRel(MocEntitiesSlug)
	before, created := mocCurrentPage(root, rel)
	if len(pages) == 0 {
		if before != nil {
			fm, _ := ParseFrontmatter(string(before))
			if strings.TrimSpace(fm["generated_by"]) == mocGeneratedBy {
				plan.Removed = append(plan.Removed, rel)
				plan.Intents = append(plan.Intents, Intent{Job: JobMocs, Rel: rel, Before: before, Delete: true,
					Summary: "the entity map removed (no entity pages)"})
			}
		}
		return plan
	}
	updated := ""
	for _, p := range pages {
		if p.Last > updated {
			updated = p.Last
		}
	}
	if updated == "" {
		updated = now.UTC().Format("2006-01-02")
	}
	if created == "" {
		created = updated
	}
	plan.add(MocPage{Rel: rel, Members: len(pages), Newest: updated}, before,
		renderEntityMap(pages, created, updated),
		fmt.Sprintf("the entity map regenerated (%d %s)", len(pages), mocPlural(len(pages), "page", "pages")))
	return plan
}

func renderEntityMap(pages []EntityPage, created, updated string) string {
	lines := []string{"---", "title: entities — map of content", "kind: moc", "status: active",
		"created: " + created, "updated: " + updated, "tags: [moc, entities]", "slug: " + MocEntitiesSlug,
		fmt.Sprintf("members: %d", len(pages)), "generated_by: " + mocGeneratedBy, "---", "", "# entities", "",
		"[[" + MocRootSlug + "]]", "",
		fmt.Sprintf("%d %s, one for each repository, repo-qualified issue, release and person the vault keeps mentioning, rebuilt each night from the notes that mention them. Generated; not edited by hand.",
			len(pages), mocPlural(len(pages), "page", "pages")), ""}
	for _, h := range entityHeadings {
		var of []EntityPage
		for _, p := range pages {
			if p.Type == h.kind {
				of = append(of, p)
			}
		}
		if len(of) == 0 {
			continue
		}
		sort.Slice(of, func(i, j int) bool {
			if of[i].SharedWork != of[j].SharedWork {
				return of[i].SharedWork > of[j].SharedWork
			}
			if of[i].Mentions != of[j].Mentions {
				return of[i].Mentions > of[j].Mentions
			}
			return of[i].ID < of[j].ID
		})
		lines = append(lines, fmt.Sprintf("## %s (%d)", h.heading, len(of)), "")
		for _, p := range of {
			tail := fmt.Sprintf("%d %s", p.Mentions, mocPlural(p.Mentions, "note", "notes"))
			if p.Type == "person" {
				tail = fmt.Sprintf("%d %s of shared work, %s", p.SharedWork,
					mocPlural(p.SharedWork, "piece", "pieces"), tail)
			}
			lines = append(lines, fmt.Sprintf("- [[%s|%s]] — %s",
				strings.TrimSuffix(p.Rel, ".md"), strings.NewReplacer("|", "-", "[", "(", "]", ")").Replace(p.Title), tail))
		}
		lines = append(lines, "")
	}
	return strings.Join(lines, "\n")
}
