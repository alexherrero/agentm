package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// The `dreaming` facet: the day's record of the machinery, in the register
// beside the operator's own facets.
//
// Everything the night did, one line each — what sank, what was archived, what
// was deleted and the manifest that recorded it, what was consolidated, what
// retention removed, what `sequence` numbered, and every file that moved by
// hand while the run was working. It is written from what the pass actually
// did, not from what it planned: a line here is a file that changed.
//
// Why a facet rather than another diagnostic: the operator reads the register,
// and a night that only reports into `diagnostics/` reports into a folder
// nobody opens. The contract's `facets` list gains the word, so the calendar's
// own index and rollups pick it up like any other facet.

const (
	JobFacet = "dreaming-facet"
	// FacetDreaming is the facet's name in the contract's registry.
	FacetDreaming = "dreaming"
)

// FacetPlan is the day's facet, written or unchanged.
type FacetPlan struct {
	Intents []Intent `json:"-"`
	Rel     string   `json:"rel,omitempty"`
	Acts    int      `json:"acts"`
	Skipped string   `json:"skipped,omitempty"`
}

// PlanDreamingFacet renders the day's facet from what this pass did.
//
// Nothing to say is nothing written: a night that moved nothing writes no
// facet, the same rule the other facets follow — a facet file exists only on a
// day that had content for it.
func PlanDreamingFacet(root string, r *rules.Rules, rep *Report, now time.Time) FacetPlan {
	var plan FacetPlan
	if rep == nil {
		return plan
	}
	if !facetRegistered(r) {
		plan.Skipped = "the contract's `facets` does not name `dreaming`"
		return plan
	}
	calendarRoot := CalendarRoot(root)
	if calendarRoot == "" {
		plan.Skipped = "no calendar/ space"
		return plan
	}
	// A note the operator edited stays "touched" every night until the night
	// next moves it, so it is named once, on the first night: a note an
	// earlier facet named is not named again (task 186).
	if len(rep.Plan.Touched) > 0 {
		named := leftAloneNamed(calendarRoot, now)
		var fresh []Move
		for _, m := range rep.Plan.Touched {
			if !named[m.Rel] {
				fresh = append(fresh, m)
			}
		}
		if len(fresh) != len(rep.Plan.Touched) {
			copied := *rep
			copied.Plan.Touched = fresh
			rep = &copied
		}
	}
	body, acts := renderDreamingFacet(rep, now)
	plan.Acts = acts
	if acts == 0 {
		return plan
	}
	day := now.UTC()
	abs := filepath.Join(calendarRoot, fmt.Sprintf("%04d", day.Year()),
		day.Format("2006-01-02")+"-"+FacetDreaming+".md")
	rel, err := filepath.Rel(root, abs)
	if err != nil {
		plan.Skipped = "the calendar is outside the memory root"
		return plan
	}
	plan.Rel = filepath.ToSlash(rel)

	cur, readErr := os.ReadFile(abs)
	if readErr == nil && string(cur) == body {
		return plan
	}
	intent := Intent{Job: JobFacet, Rel: plan.Rel, After: []byte(body),
		Summary: fmt.Sprintf("the night's own record of %d act(s)", acts),
		Meta:    map[string]string{"from": "", "to": "written", "reason": "the dreaming facet"}}
	if readErr == nil {
		// A creation is an intent with no `before`; the journal derives it.
		intent.Before = cur
	}
	plan.Intents = append(plan.Intents, intent)
	return plan
}

func facetRegistered(r *rules.Rules) bool {
	for _, f := range Facets(r) {
		if f == FacetDreaming {
			return true
		}
	}
	return false
}

// leftAloneHeading is the section that names the notes the operator edited.
const leftAloneHeading = "Left alone — you edited its lifecycle"

// facetRelRe is a facet line's path: "- [[base]] (`rel`)".
var facetRelRe = regexp.MustCompile("\\(`([^`]+)`\\)")

// leftAloneNamed is every note an earlier dreaming facet already named under
// leftAloneHeading. Today's own facet is not read, so a rerun renders the
// same. A note the operator edits a second time while it is still listed here
// is not named again; the facets are the record of the first.
func leftAloneNamed(calendarRoot string, now time.Time) map[string]bool {
	out := map[string]bool{}
	today := now.UTC().Format("2006-01-02")
	files, _ := filepath.Glob(filepath.Join(calendarRoot, "*", "*-"+FacetDreaming+".md"))
	for _, f := range files {
		day := strings.TrimSuffix(filepath.Base(f), "-"+FacetDreaming+".md")
		if len(day) != len(today) || day >= today {
			continue
		}
		raw, err := os.ReadFile(f)
		if err != nil {
			continue
		}
		in := false
		for _, line := range strings.Split(strings.ReplaceAll(string(raw), "\r\n", "\n"), "\n") {
			if strings.HasPrefix(line, "## ") {
				in = strings.TrimPrefix(line, "## ") == leftAloneHeading
				continue
			}
			if in {
				if m := facetRelRe.FindStringSubmatch(line); m != nil {
					out[m[1]] = true
				}
			}
		}
	}
	return out
}

// renderDreamingFacet writes the day's lines, and says how many acts it found.
func renderDreamingFacet(rep *Report, now time.Time) (string, int) {
	var b strings.Builder
	day := now.UTC().Format("2006-01-02")
	b.WriteString("---\n")
	b.WriteString("title: " + day + " — what the night did\n")
	b.WriteString("kind: calendar-facet\n")
	b.WriteString("facet: " + FacetDreaming + "\n")
	b.WriteString("created: " + day + "\n")
	b.WriteString("---\n\n")

	acts := 0
	section := func(heading string, lines []string) {
		if len(lines) == 0 {
			return
		}
		acts += len(lines)
		b.WriteString("## " + heading + "\n\n")
		for _, l := range lines {
			b.WriteString("- " + l + "\n")
		}
		b.WriteString("\n")
	}
	link := func(rel string) string {
		base := strings.TrimSuffix(filepath.Base(rel), ".md")
		return "[[" + base + "]] (`" + rel + "`)"
	}
	moves := func(ms []Move, tail func(Move) string) []string {
		out := make([]string, 0, len(ms))
		for _, m := range ms {
			out = append(out, link(m.Rel)+tail(m))
		}
		return out
	}
	silent := func(m Move) string { return fmt.Sprintf(" — silent %.0f days", m.Days) }

	p := rep.Plan
	section("Sank", moves(p.Demoted, silent))
	section("Lifted by a recall", moves(p.Revived, func(m Move) string {
		return fmt.Sprintf(" — recalled %.0f days ago", m.Days)
	}))
	section("Archived", moves(p.Archived, func(m Move) string {
		return fmt.Sprintf(" — silent %.0f days, moved to `%s`", m.Days, ArchiveDestination(m.Rel))
	}))
	deleted := moves(p.Deleted, silent)
	if len(deleted) > 0 && rep.DeletionManifest != "" {
		deleted = append(deleted, "the manifest that recorded them: `"+rep.DeletionManifest+"`")
	}
	section("Deleted", deleted)
	section("Moved back into its class by hand", moves(p.Returned, func(Move) string { return "" }))
	section(leftAloneHeading, moves(p.Touched, func(Move) string { return "" }))

	var removed []string
	for _, row := range rep.Retain.Removed {
		removed = append(removed, fmt.Sprintf("`%s` — %s, %.0f days old, kept %.0f",
			row.Rel, row.What, row.Days, row.Keep))
	}
	section("Removed by retention", removed)

	var entityPages []string
	for _, rel := range rep.Entities.Removed {
		entityPages = append(entityPages, "`"+rel+"` — under its bar; the journal keeps its text")
	}
	section("Entity pages removed", entityPages)

	var entityHeld []string
	for _, rel := range rep.Entities.Held {
		entityHeld = append(entityHeld, "`"+rel+"` — a note you wrote is at its path; move it to let the page be built")
	}
	if n := len(rep.Entities.PeopleHeld); n > 0 {
		entityHeld = append(entityHeld, fmt.Sprintf("people pages kept as they were: %d note(s) could not be read, e.g. `%s`",
			n, rep.Entities.PeopleHeld[0]))
	}
	section("Entity pages held", entityHeld)

	var held []string
	for _, row := range rep.Retain.Held {
		held = append(held, fmt.Sprintf("`%s` — %s, %.0f days old, kept %.0f", row.Rel, row.What, row.Days, row.Keep))
	}
	if len(held) > 0 && rep.Retain.Gate != "" {
		held = append(held, "nothing is deleted until you set `approved: true` in `"+rep.Retain.Gate+"`")
	}
	section("Held by retention until you approve", held)

	var consolidated []string
	for _, fam := range rep.Copies.Families {
		if len(fam.Copies) == 0 {
			continue
		}
		consolidated = append(consolidated,
			fmt.Sprintf("%s kept; %d copy(ies) marked superseded", link(fam.Canonical), len(fam.Copies)))
	}
	section("Consolidated", consolidated)

	// The skipped hand moves: a file that moved under the pass between the plan
	// and the write. Named here rather than swallowed, because the reconcile
	// step at the end of the same night is what repairs them, and a repair
	// nobody can see is indistinguishable from a loss.
	var skipped []string
	for _, s := range rep.SkippedByHandMove {
		skipped = append(skipped, "`"+s+"` — moved by hand during the run")
	}
	section("Skipped, and repaired by reconcile", skipped)

	var repaired []string
	for _, row := range rep.Reconcile.Repaired {
		repaired = append(repaired, fmt.Sprintf("`%s` → `%s`", row.From, row.To))
	}
	section("Followed a move you made", repaired)

	if acts == 0 {
		return "", 0
	}
	return b.String(), acts
}
