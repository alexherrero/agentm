package dreaming

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// The night's log: the machinery's own record of what a pass did.
//
// Everything the night did, one line each — what sank, what was archived, what
// was deleted and the manifest that recorded it, what was consolidated, what
// retention removed, what `sequence` numbered, and every file that moved by
// hand while the run was working. It is written from what the pass actually
// did, not from what it planned: a line here is a file that changed.
//
// It is one page, `diagnostics/dreaming/latest_night.md`, rewritten on a night
// that did something: that night in full, then one line for each earlier
// night. Every night's full text is a row in `history.jsonl` beside it, a data
// file the index never opens (task 201).
//
// Until then it was a calendar facet, `calendar/YYYY/YYYY-MM-DD-dreaming.md`,
// a new note a night: 49 of the calendar's 65 files by 2026-11-13, in a
// register that is meant to hold what a person wrote. The morning note links
// the page, so the night is still read where the operator looks.

const (
	// JobFacet names the job in the journal. The name predates the move out of
	// the calendar and is kept, so one night's journal reads like the last's.
	JobFacet = "dreaming-facet"
	// FacetDreaming is the retired facet's name: what the old calendar notes
	// are called, and nothing a pass writes any more.
	FacetDreaming = "dreaming"

	// NightLogDir is where the page and its history live, under the memory root.
	NightLogDir = "diagnostics/dreaming"
	// NightLogPage is the one page.
	NightLogPage = "latest_night.md"
	// NightLogHistory is the data file beside it, in the shape the Python
	// reports' `report_history.py` writes: one JSON object a line, oldest first.
	NightLogHistory = "history.jsonl"
	// NightLogReport is the log's name in that history.
	NightLogReport = "dreaming-night"
	// nightLogEarlier is how many earlier nights the page lists.
	nightLogEarlier = 30
)

// FacetPlan is the night's log: the page written or unchanged, and the
// history the page's write brings with it.
type FacetPlan struct {
	Intents []Intent `json:"-"`
	Rel     string   `json:"rel,omitempty"`
	Acts    int      `json:"acts"`
	Skipped string   `json:"skipped,omitempty"`
	// History is `history.jsonl` as it reads once this night's row is in it;
	// nil when the page did not change. It is written beside the page, outside
	// the journal: the journal keeps a before and an after of every file it
	// writes, and a file that grows a row a night would be copied whole twice
	// a night.
	History    []byte `json:"-"`
	HistoryRel string `json:"history_rel,omitempty"`
}

// PlanDreamingFacet renders the night's log from what this pass did.
//
// Nothing to say is nothing written: a night that moved nothing leaves the
// page as the last night that did, and adds no row.
func PlanDreamingFacet(root string, _ *rules.Rules, rep *Report, now time.Time) FacetPlan {
	var plan FacetPlan
	if rep == nil {
		return plan
	}
	sections, acts := renderNightSections(rep)
	plan.Acts = acts
	if acts == 0 {
		return plan
	}
	day := now.UTC().Format("2006-01-02")
	dir := filepath.Join(root, filepath.FromSlash(NightLogDir))
	abs := filepath.Join(dir, NightLogPage)
	plan.Rel = NightLogDir + "/" + NightLogPage
	plan.HistoryRel = NightLogDir + "/" + NightLogHistory

	rows := readNightHistory(filepath.Join(dir, NightLogHistory))
	body := renderNightPage(day, sections, earlierNights(rows, day))

	cur, readErr := os.ReadFile(abs)
	if readErr == nil && string(cur) == body {
		return plan
	}
	intent := Intent{Job: JobFacet, Rel: plan.Rel, After: []byte(body),
		Summary: fmt.Sprintf("the night's own record of %d act(s)", acts),
		Meta:    map[string]string{"from": "", "to": "written", "reason": "the night's log"}}
	if readErr == nil {
		// A creation is an intent with no `before`; the journal derives it.
		intent.Before = cur
	}
	plan.Intents = append(plan.Intents, intent)
	plan.History = withNightRow(rows, map[string]any{
		"date": day, "report": NightLogReport, "acts": acts,
		"at":   now.UTC().Format("2006-01-02T15:04:05Z"),
		"body": nightTitle(day) + sections,
	})
	return plan
}

// WriteNightHistory writes the history a plan carries, through a temporary
// file and a rename so a reader sees the old file or the new one.
func WriteNightHistory(root string, plan FacetPlan) error {
	if plan.History == nil || plan.HistoryRel == "" {
		return nil
	}
	abs := filepath.Join(root, filepath.FromSlash(plan.HistoryRel))
	if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(abs), ".history-*.tmp")
	if err != nil {
		return err
	}
	name := tmp.Name()
	if _, err := tmp.Write(plan.History); err != nil {
		tmp.Close()
		os.Remove(name)
		return err
	}
	if err := tmp.Close(); err != nil {
		os.Remove(name)
		return err
	}
	if err := os.Rename(name, abs); err != nil {
		os.Remove(name)
		return err
	}
	return nil
}

// readNightHistory is every row the history holds, oldest first. A line that
// is not a row — a torn write — is skipped, never an error: a history that
// cannot be read back must not stop the night that appends to it.
func readNightHistory(path string) []map[string]any {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var rows []map[string]any
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var row map[string]any
		if json.Unmarshal([]byte(line), &row) != nil {
			continue
		}
		if s, _ := row["date"].(string); s == "" {
			continue
		}
		rows = append(rows, row)
	}
	sortNightRows(rows)
	return rows
}

func sortNightRows(rows []map[string]any) {
	str := func(row map[string]any, key string) string {
		s, _ := row[key].(string)
		return s
	}
	sort.SliceStable(rows, func(i, j int) bool {
		if a, b := str(rows[i], "date"), str(rows[j], "date"); a != b {
			return a < b
		}
		return str(rows[i], "at") < str(rows[j], "at")
	})
}

// withNightRow is the history with this night's row in it, replacing the row
// an earlier pass the same day wrote.
func withNightRow(rows []map[string]any, row map[string]any) []byte {
	kept := make([]map[string]any, 0, len(rows)+1)
	for _, r := range rows {
		if r["date"] == row["date"] && r["report"] == row["report"] {
			continue
		}
		kept = append(kept, r)
	}
	kept = append(kept, row)
	sortNightRows(kept)
	var out bytes.Buffer
	enc := json.NewEncoder(&out)
	enc.SetEscapeHTML(false)
	for _, r := range kept {
		// A map marshals with its keys sorted, as the Python writer sorts them.
		if err := enc.Encode(r); err != nil {
			continue
		}
	}
	return out.Bytes()
}

// earlierNights is one line for each night before `day`, newest first.
func earlierNights(rows []map[string]any, day string) []string {
	var out []string
	for i := len(rows) - 1; i >= 0 && len(out) < nightLogEarlier; i-- {
		r := rows[i]
		date, _ := r["date"].(string)
		if r["report"] != NightLogReport || date >= day {
			continue
		}
		acts, _ := r["acts"].(float64)
		out = append(out, fmt.Sprintf("%s — %.0f act(s)", date, acts))
	}
	return out
}

func nightTitle(day string) string {
	return "# What the night did — " + day + "\n\n"
}

// renderNightPage is the page: the night in full, then the earlier nights.
func renderNightPage(day, sections string, earlier []string) string {
	var b strings.Builder
	b.WriteString("---\n")
	b.WriteString("title: What the night did\n")
	b.WriteString("kind: report\n")
	b.WriteString("date: " + day + "\n")
	b.WriteString("generated_by: agentmdream\n")
	b.WriteString("---\n\n")
	b.WriteString(nightTitle(day))
	b.WriteString(sections)
	if len(earlier) > 0 {
		b.WriteString("## Earlier nights\n\n")
		b.WriteString("One line a night; each night's full record is in `" + NightLogHistory + "` beside this page.\n\n")
		for _, l := range earlier {
			b.WriteString("- " + l + "\n")
		}
		b.WriteString("\n")
	}
	return b.String()
}

// leftAloneHeading is the section that names the notes the operator edited.
// Each edit is named once, on the night the lifecycle pass finds and journals
// it: after that the journal agrees with the note, and the pass no longer finds
// an edit there (task 187; until then a standing "touch" and a filter over the
// earlier facets did this).
const leftAloneHeading = "Left alone — you edited its lifecycle"

// renderNightSections writes the night's lines, a section for each kind of
// act, and says how many acts it found.
func renderNightSections(rep *Report) (string, int) {
	var b strings.Builder
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
	section("Superseded lessons retired", moves(p.Retired, func(m Move) string {
		return fmt.Sprintf(" — moved to `%s`", ArchiveDestination(m.Rel))
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
	if n := len(rep.Entities.NoLongerBuilt); n > 0 {
		entityHeld = append(entityHeld, fmt.Sprintf("%d page(s) the builder no longer writes — issue and release pages, "+
			"and people the people file does not list — left as they are for the clear-out", n))
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

	// The idea judge's night (task 192), which ran before this pass: its counts,
	// and each card it wrote. A night it judged nothing says nothing.
	var judged []string
	if j := rep.IdeaJudge; j != nil && (j.Judged+j.Removed+j.Deferred > 0 || len(j.Written) > 0) {
		line := fmt.Sprintf("%d remark(s) judged: %d steering, %d repeat(s), %d new", j.Judged, j.Steering, j.Repeats, j.New)
		if j.Removed > 0 {
			line += fmt.Sprintf("; %d you had removed, dropped without a call", j.Removed)
		}
		if j.Deferred > 0 {
			line += fmt.Sprintf("; %d past the night's cap, for the next night", j.Deferred)
		}
		if j.Mode != "apply" {
			line += " — reporting only, nothing written"
		}
		judged = append(judged, line)
		for _, rel := range j.Written {
			judged = append(judged, "`"+rel+"`")
		}
	}
	section("Ideas judged", judged)

	if acts == 0 {
		return "", 0
	}
	return b.String(), acts
}
