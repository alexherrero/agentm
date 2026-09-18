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
