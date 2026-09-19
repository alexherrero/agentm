package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
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
	section("Left alone — you edited its lifecycle", moves(p.Touched, func(Move) string { return "" }))

	var removed []string
	for _, row := range rep.Retain.Removed {
		removed = append(removed, fmt.Sprintf("`%s` — %s, %.0f days old, kept %.0f",
			row.Rel, row.What, row.Days, row.Keep))
	}
	section("Removed by retention", removed)

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
