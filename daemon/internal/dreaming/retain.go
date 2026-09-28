package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// The retention sweep: the night deletes its own paper, and only its own.
//
// `agent/diagnostics/` has no archive. Sixty-five digests in a folder the
// operator opens for one is the eyeline problem the class folders had, and a
// scorecard from last spring is worth nothing anyone would go looking for. So
// the night keeps each kind for the days the contract's `retention:` block
// names and deletes what is past — with the same manifest-and-journal-line
// first that every other deletion on the axis takes.
//
// **Only what it wrote.** The doctrine that no policy deletes a memory or the
// operator's document is untouched by this: a digest of Tuesday is neither. The
// migration and purge manifests are deliberately absent from the contract's
// block, because they are the record of what moved and what was forgotten, and
// nothing prunes the record.
//
// A file's age is read from the date in its own name. A file whose name carries
// no date is counted and left: an mtime is not an age on this vault — the
// type-collapse migration rewrote nine thousand notes in an afternoon — and a
// sweep that guessed would delete by the wrong clock.

const JobRetain = "retain"

// retentionRule ties a contract key to the files it governs.
type retentionRule struct {
	// Key is the contract's `retention:` line.
	Key string
	// Dir is the directory, relative to the memory root.
	Dir string
	// Match decides whether a file in Dir is this rule's. The first rule whose
	// Match answers wins, so the order below is the order of specificity.
	Match func(name string) bool
	// What names the kind in a report line.
	What string
}

var dateInName = regexp.MustCompile(`(20\d{2})-?(\d{2})-?(\d{2})`)

// retentionRules are the kinds the night keeps, most specific first.
var retentionRules = []retentionRule{
	{Key: "digest_monthly_days", Dir: "diagnostics/digests", What: "monthly digest",
		Match: func(n string) bool { return strings.Contains(n, "-digest-monthly") }},
	{Key: "digest_weekly_days", Dir: "diagnostics/digests", What: "weekly digest",
		Match: func(n string) bool { return strings.Contains(n, "-digest-weekly") }},
	{Key: "digest_3day_days", Dir: "diagnostics/digests", What: "three-day digest",
		Match: func(n string) bool { return strings.Contains(n, "-digest-3day") }},
	{Key: "digest_daily_days", Dir: "diagnostics/digests", What: "daily digest",
		Match: func(n string) bool { return strings.Contains(n, "-digest-daily") }},
	{Key: "morning_note_days", Dir: "diagnostics/morning", What: "morning note",
		Match: func(n string) bool { return true }},
	{Key: "lint_report_days", Dir: "diagnostics/lint", What: "lint report",
		Match: func(n string) bool { return true }},
	{Key: "scorecard_days", Dir: "diagnostics/health", What: "scorecard",
		Match: func(n string) bool { return true }},
	{Key: "divergence_note_days", Dir: "diagnostics/dreaming", What: "dreaming note",
		Match: func(n string) bool { return true }},
}

// RetainPlan is what one sweep would (or did) remove.
type RetainPlan struct {
	Intents []Intent    `json:"-"`
	Removed []RetainRow `json:"removed"`
	// Undated is a file the sweep left because its name carries no date. Counted
	// rather than guessed at: an mtime is not an age on this vault.
	Undated int `json:"undated"`
	Kept    int `json:"kept"`
	Capped  int `json:"skipped_by_cap"`
	// Held is what the sweep would have removed while it waits for the
	// operator's first approval (RetentionGateRel); Gate names that note.
	Held []RetainRow `json:"held,omitempty"`
	Gate string      `json:"gate,omitempty"`
}

// RetainRow is one file the sweep removes.
type RetainRow struct {
	Rel  string  `json:"rel"`
	What string  `json:"what"`
	Days float64 `json:"days"`
	Keep float64 `json:"keep_days"`
}

// LatestMirrorPrefix marks the `latest_*` mirrors, which are kept on the long
// line rather than their kind's: they are the current reading of something, and
// the operator's own links point at them.
const LatestMirrorPrefix = "latest_"

// PlanRetain reads the contract's retention block and decides the sweep. It
// writes nothing; the run applies the intents through the journal, after the
// manifest.
func PlanRetain(root string, r *rules.Rules, now time.Time, cap int) (RetainPlan, error) {
	var plan RetainPlan
	if r == nil {
		// No contract, no retention. The safe direction for a sweep that
		// deletes: a missing rule keeps a file forever, and forever is
		// recoverable.
		return plan, nil
	}
	cap = DemotionCap(r, cap)
	day := time.Date(now.UTC().Year(), now.UTC().Month(), now.UTC().Day(), 0, 0, 0, 0, time.UTC)

	// The calendar's own horizon, which is not under diagnostics/.
	rulesToWalk := append([]retentionRule(nil), retentionRules...)
	if calendarRoot := CalendarRoot(root); calendarRoot != "" {
		if rel, err := filepath.Rel(root, calendarRoot); err == nil {
			rulesToWalk = append(rulesToWalk, retentionRule{
				Key: "calendar_facet_days", Dir: filepath.ToSlash(rel), What: "calendar facet",
				Match: func(n string) bool { return true },
			})
		}
	}

	for _, rule := range rulesToWalk {
		keep, ok := r.RetentionDays(rule.Key)
		if !ok || keep <= 0 {
			continue
		}
		dir := filepath.Join(root, filepath.FromSlash(rule.Dir))
		var files []string
		err := filepath.Walk(dir, func(p string, info os.FileInfo, err error) error {
			if err != nil || info.IsDir() || filepath.Ext(p) != ".md" {
				return nil
			}
			files = append(files, p)
			return nil
		})
		if err != nil {
			continue
		}
		sort.Strings(files)
		for _, p := range files {
			name := filepath.Base(p)
			if !rule.Match(name) {
				continue
			}
			rel, err := filepath.Rel(root, p)
			if err != nil {
				continue
			}
			relSlash := filepath.ToSlash(rel)
			// A `latest_*` mirror is the current reading of something and is
			// kept on the five-year line, whatever kind it mirrors.
			keepDays := keep
			if strings.HasPrefix(name, LatestMirrorPrefix) {
				if v, ok := r.RetentionDays("latest_mirror_days"); ok && v > 0 {
					keepDays = v
				}
			}
			age, ok := ageFromName(name, day)
			if !ok {
				plan.Undated++
				continue
			}
			if age <= keepDays {
				plan.Kept++
				continue
			}
			if len(plan.Removed) >= cap {
				plan.Capped++
				continue
			}
			raw, err := os.ReadFile(p)
			if err != nil {
				continue
			}
			summary := fmt.Sprintf("%s %.0f days old, past retention %.0f → removed",
				rule.What, age, keepDays)
			plan.Intents = append(plan.Intents, Intent{Job: JobRetain, Rel: relSlash,
				Before: raw, Delete: true, Summary: summary,
				Meta: map[string]string{"from": "kept", "to": "removed", "reason": summary}})
			plan.Removed = append(plan.Removed, RetainRow{
				Rel: relSlash, What: rule.What, Days: age, Keep: keepDays})
		}
	}
	return plan, nil
}

// ageFromName reads a file's own date out of its name — `20260711-digest-daily`,
// `2026-09-18`, `vault-lint-2026-07-15`, `divergence-2026-09-04` — and returns
// its age in days.
//
// The name, never the mtime. The type-collapse migration rewrote nine thousand
// notes' frontmatter in an afternoon, so an mtime on this vault says when a pass
// last touched a file and not how old it is; a sweep that deletes by that clock
// would keep what it rewrote and delete what it did not.
func ageFromName(name string, now time.Time) (float64, bool) {
	m := dateInName.FindStringSubmatch(name)
	if m == nil {
		return 0, false
	}
	t, err := time.Parse("2006-01-02", m[1]+"-"+m[2]+"-"+m[3])
	if err != nil {
		return 0, false
	}
	return now.Sub(t).Hours() / 24, true
}

// RetentionRows are the manifest's lines for the sweep's deletions.
//
// The lifecycle job's DeletionRows keeps only its own intents, so the sweep's
// deletions reached the manifest as no rows at all, no manifest was written,
// and the run dropped them as it must drop a deletion with no record: retention
// never deleted anything. Its own rows, its own manifest.
func RetentionRows(intents []Intent, removed []RetainRow) []DeletionRow {
	what := map[string]RetainRow{}
	for _, r := range removed {
		what[r.Rel] = r
	}
	var out []DeletionRow
	for _, in := range intents {
		if !in.Delete || in.Job != JobRetain {
			continue
		}
		r := what[in.Rel]
		out = append(out, DeletionRow{Rel: in.Rel, Title: r.What, Days: r.Days, SHA256: Hash(in.Before)})
	}
	return out
}

// RetentionGateRel is the note that holds the sweep's first deletion for the
// operator, under the memory root. The migrations folder is outside every
// retention line, so the note outlives what it approved.
const RetentionGateRel = PurgeManifestDir + "/retention-first-deletion.md"

var approvedRe = regexp.MustCompile(`(?m)^approved:[ \t]*true[ \t]*$`)

// RetentionApproved answers whether the sweep may delete. The operator's
// condition for agentm-vault plan 11 was that retention's first deletion be
// report-only, with the list read before anything goes; this is that
// condition in code rather than in a runbook.
//
// Until the operator sets `approved: true` in the gate note, a pass that would
// delete writes the note — what it would remove, and how to let it — and
// deletes nothing. The note is rewritten only when the list changes, so a night
// that holds the same files commits nothing. Once approved, the sweep deletes
// on the contract's lines from then on, each deletion behind its manifest.
func RetentionApproved(root string, held []RetainRow, now time.Time) (bool, string, error) {
	p := filepath.Join(root, filepath.FromSlash(RetentionGateRel))
	raw, err := os.ReadFile(p)
	if err == nil {
		head := string(raw)
		if end := strings.Index(head[3:], "\n---"); strings.HasPrefix(head, "---\n") && end >= 0 {
			if approvedRe.MatchString(head[:end+3]) {
				return true, RetentionGateRel, nil
			}
		}
	} else if !os.IsNotExist(err) {
		return false, RetentionGateRel, err
	}
	created := now.UTC().Format("2006-01-02")
	if m := regexp.MustCompile(`(?m)^created:[ \t]*(\S+)`).FindStringSubmatch(string(raw)); m != nil {
		created = m[1]
	}
	body := retentionGateBody(held, now)
	if strings.Contains(string(raw), body) {
		return false, RetentionGateRel, nil
	}
	note := "---\nkind: report\nstatus: active\ncreated: " + created + "\nupdated: " +
		now.UTC().Format("2006-01-02") + "\ntags: [retention, purge, first-deletion]\napproved: false\n---\n\n" + body
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return false, RetentionGateRel, err
	}
	return false, RetentionGateRel, os.WriteFile(p, []byte(note), 0o644)
}

// retentionGateBody lists the held files by the date each crossed its line
// rather than by its age, so the same list reads the same on every night and a
// night that holds nothing new rewrites nothing.
func retentionGateBody(held []RetainRow, now time.Time) string {
	day := time.Date(now.UTC().Year(), now.UTC().Month(), now.UTC().Day(), 0, 0, 0, 0, time.UTC)
	var b strings.Builder
	b.WriteString("# Retention's first deletion — waiting for you\n\n")
	b.WriteString("The night keeps its own paper for the days the contract's `retention:` block names, and these files are past their line. ")
	b.WriteString("Nothing has been deleted. Read the list, then set `approved: true` in this note's frontmatter; ")
	b.WriteString("from the next pass on, the sweep deletes on the contract's lines, writing a manifest before each deletion ")
	b.WriteString("as every other deletion on the axis does. The vault's git history keeps every file either way.\n\n")
	b.WriteString("| File | Kind | Kept for | Past its line since |\n|---|---|---:|---|\n")
	for _, r := range held {
		crossed := day.AddDate(0, 0, int(r.Keep)-int(r.Days))
		fmt.Fprintf(&b, "| `%s` | %s | %.0f days | %s |\n", r.Rel, r.What, r.Keep, crossed.Format("2006-01-02"))
	}
	return b.String()
}
