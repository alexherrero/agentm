// Package note parses a vault markdown file into the fields the index needs.
//
// The parse is a faithful port of `scripts/health/week1_corpus.py`, which is the
// code the 2026-08-06 and 2026-08-07 measurements actually ran. Where a choice
// here looks arbitrary it is usually "because that is what was measured" — a
// tokenizer or a column that differs from the reference makes the +3.75 R@5
// result unverifiable against this daemon.
package note

import (
	"regexp"
	"strconv"
	"strings"
	"time"
)

var (
	frontmatterRe = regexp.MustCompile(`(?s)\A---[ \t\r]*\n(.*?)\n---[ \t\r]*\n`)
	titleRe       = regexp.MustCompile(`(?m)^title:[ \t]*(.+?)[ \t\r]*$`)
	statusRe      = regexp.MustCompile(`(?m)^status:[ \t]*(\S+)`)
	miningRe      = regexp.MustCompile(`(?m)^mining_confidence:`)
	probeRe       = regexp.MustCompile(`(?m)^probe:[ \t]*(\S+)`)
	capturedRe    = regexp.MustCompile(`(?m)^captured:[ \t]*(.+?)[ \t\r]*$`)
	updatedRe     = regexp.MustCompile(`(?m)^updated:[ \t]*(.+?)[ \t\r]*$`)
	altitudeRe    = regexp.MustCompile(`(?m)^altitude:[ \t]*(.+?)[ \t\r]*$`)
	confidenceRe  = regexp.MustCompile(`(?m)^confidence:[ \t]*([0-9.]+)[ \t\r]*$`)
	createdRe     = regexp.MustCompile(`(?m)^created:[ \t]*(.+?)[ \t\r]*$`)
	// The two frontmatter routes into durability — see isDurable.
	lifecycleTierRe = regexp.MustCompile(`(?m)^lifecycle_tier:[ \t]*(.+?)[ \t\r]*$`)
	kindRe          = regexp.MustCompile(`(?m)^kind:[ \t]*(.+?)[ \t\r]*$`)
	dateRe          = regexp.MustCompile(`(?m)^date:[ \t]*(.+?)[ \t\r]*$`)
	proposalRe      = regexp.MustCompile(`\A#[ \t]*Proposal[ \t]+\d+[ \t]*:`)
	metaScrubRe     = regexp.MustCompile(`[\[\],'"]`)
	wsRe            = regexp.MustCompile(`\s+`)
)

// Note is one parsed vault file.
type Note struct {
	// Rel is the vault-relative POSIX path. It is the note's identity.
	Rel string
	// Title is the frontmatter title plus the filename stem with separators
	// spaced out, so `vault-path-convention` also matches "vault path
	// convention". Weighted 4x above body.
	Title string
	// Meta is the aliases and tags values as plain text, in its own column
	// weighted 3x above body. Only 5.5% of the corpus has anything here today,
	// which is why it measures as a no-op — dreaming's alias backfill is what
	// fills it, and it needs somewhere to land.
	Meta string
	// Body is the frontmatter block followed by the note body. The frontmatter
	// stays searchable because it is real query surface: "what's my convention
	// for X" hits `kind: convention`.
	Body string

	Status string
	// Probe marks a synthetic self-probe note. It is read from the frontmatter
	// marker rather than inferred from the note's location, because the design
	// requires a probe to be excludable by what it carries: capture shards by
	// date, so any path rule would quietly stop matching the first time the
	// month rolled over.
	Probe bool
	// Captured is the note's capture date, used by the after:/before: bounds.
	Captured time.Time
	// CapturedSource records which signal supplied it, so a bound that behaves
	// oddly is diagnosable rather than mysterious.
	CapturedSource string

	// Flags are the rank-penalty classes this note falls into.
	Flags []string

	// Created is the note's own `created:` stamp — the decay anchor of last
	// resort, and on this corpus the one that does nearly all the work: 69.7% of
	// notes carry it, against 7.6% for `updated` and 1.3% for `captured`.
	//
	// It is a separate field from Captured rather than folded into it because
	// Captured falls back to the filesystem, and decay cannot use a filesystem
	// timestamp. Reading `created` through Captured would mean either accepting
	// mtime as an age or refusing 98.7% of the corpus an anchor — the first is
	// wrong on a corpus whose frontmatter was rewritten wholesale in an
	// afternoon, and the second is what a first cut of this actually did.
	Created string

	// Confidence is the enrichment pass's own account of how sure it was, and
	// ConfidenceSet distinguishes "scored zero" from "never scored".
	//
	// Two fields for one value because the difference is the whole point of the
	// review queue: a note enrichment judged and doubted is work for a person,
	// while a note enrichment has not reached is work for the batch pass. Read as
	// one number they are indistinguishable, and the queue would put the
	// unreached notes at the front of a list nobody can act on.
	Confidence    float64
	ConfidenceSet bool

	// Updated is the note's own `updated:` stamp, and it is the decay anchor
	// after a genuine recall.
	//
	// Not `captured`, and not mtime. `captured` would penalize a
	// frequently-maintained reference for staleness it does not have — the
	// cold-start bias the Python curve's own comment records as having silently
	// demoted an accurate, same-day-edited hit out of the top five. And mtime is
	// worse still on this corpus: the type-collapse migration rewrote 9,899
	// notes' frontmatter, which would make every one of them look freshly
	// updated to a curve reading the filesystem.
	Updated string
}

// Parse turns one file's bytes into a Note. `rel` is the vault-relative POSIX
// path; `modTime` is the filesystem mtime, used only as the last fallback for
// the capture date.
func Parse(rel, raw string, modTime time.Time) Note {
	n := Note{Rel: rel}

	head, body := splitFrontmatter(raw)

	// Title: frontmatter title, then the stem with separators spaced out.
	stem := rel
	if i := strings.LastIndex(stem, "/"); i >= 0 {
		stem = stem[i+1:]
	}
	stem = strings.TrimSuffix(stem, ".md")
	stemWords := strings.NewReplacer("-", " ", "_", " ").Replace(stem)
	if m := titleRe.FindStringSubmatch(head); m != nil {
		n.Title = strings.Trim(strings.TrimSpace(m[1]), `'"`) + " " + stemWords
	} else {
		n.Title = stemWords
	}

	n.Meta = extractMeta(head)
	if head != "" {
		n.Body = head + "\n" + body
	} else {
		n.Body = body
	}

	n.Status = parseStatus(head)
	n.Probe = parseProbe(head)
	n.Captured, n.CapturedSource = parseCaptured(head, modTime)
	n.Flags = classify(rel, head, strings.TrimLeft(body, " \t\r\n"), n.Status)
	if m := updatedRe.FindStringSubmatch(head); m != nil {
		n.Updated = strings.TrimSpace(m[1])
	}
	if m := createdRe.FindStringSubmatch(head); m != nil {
		n.Created = strings.TrimSpace(m[1])
	}
	if m := confidenceRe.FindStringSubmatch(head); m != nil {
		if f, err := strconv.ParseFloat(strings.TrimSpace(m[1]), 64); err == nil {
			n.Confidence, n.ConfidenceSet = f, true
		}
	}
	return n
}

func parseStatus(head string) string {
	if m := statusRe.FindStringSubmatch(head); m != nil {
		return strings.ToLower(strings.Trim(m[1], `'"`))
	}
	return ""
}

// ProbeMarker is the frontmatter key that marks a note as a synthetic
// self-probe, and ProbeMarkerValue is what the daemon writes into it.
//
// One key, read by everything that has any business excluding a probe from a
// measurement — the classifier's JSON output, the alias backfill, any future
// scorecard. `true` is accepted alongside the named value so a note marked by
// hand is not silently ignored.
const (
	ProbeMarker      = "probe"
	ProbeMarkerValue = "self-probe"
)

func parseProbe(head string) bool {
	m := probeRe.FindStringSubmatch(head)
	if m == nil {
		return false
	}
	switch strings.ToLower(strings.Trim(m[1], `'"`)) {
	case ProbeMarkerValue, "true", "yes":
		return true
	}
	return false
}

// parseCaptured prefers the note's own claim, then a `date` field, then the
// filesystem. Which one won is recorded rather than smoothed over: sharding and
// the temporal bounds both hang off this value, and a silently-guessed date
// would make an episodic query quietly wrong instead of visibly approximate.
func parseCaptured(head string, modTime time.Time) (time.Time, string) {
	for _, probe := range []struct {
		re  *regexp.Regexp
		src string
	}{
		{capturedRe, "frontmatter:captured"},
		{dateRe, "frontmatter:date"},
	} {
		m := probe.re.FindStringSubmatch(head)
		if m == nil {
			continue
		}
		if t, ok := parseTime(strings.Trim(strings.TrimSpace(m[1]), `'"`)); ok {
			return t, probe.src
		}
	}
	return modTime.UTC(), "mtime"
}

var timeLayouts = []string{
	time.RFC3339,
	"2006-01-02T15:04:05",
	"2006-01-02 15:04:05",
	"2006-01-02T15:04",
	"2006-01-02",
	"2006/01/02",
}

func parseTime(s string) (time.Time, bool) {
	for _, layout := range timeLayouts {
		if t, err := time.Parse(layout, s); err == nil {
			return t.UTC(), true
		}
	}
	return time.Time{}, false
}

// extractMeta pulls the aliases and tags values as plain text. Both the inline
// form (`tags: [a, b]`) and the block form (`tags:\n  - a`) are read; the Python
// reference only saw the inline form, and reading both is strictly more surface
// with no effect on the classifier the measurement pinned.
func extractMeta(head string) string {
	var parts []string
	lines := strings.Split(head, "\n")
	for i, line := range lines {
		key := ""
		switch {
		case strings.HasPrefix(line, "aliases:"):
			key = "aliases:"
		case strings.HasPrefix(line, "tags:"):
			key = "tags:"
		default:
			continue
		}
		parts = append(parts, strings.TrimPrefix(line, key))
		for _, next := range lines[i+1:] {
			trimmed := strings.TrimSpace(next)
			if !strings.HasPrefix(next, " ") && !strings.HasPrefix(next, "\t") {
				break
			}
			if !strings.HasPrefix(trimmed, "- ") && trimmed != "-" {
				break
			}
			parts = append(parts, strings.TrimPrefix(trimmed, "-"))
		}
	}
	if len(parts) == 0 {
		return ""
	}
	joined := metaScrubRe.ReplaceAllString(strings.Join(parts, " "), " ")
	return strings.TrimSpace(wsRe.ReplaceAllString(joined, " "))
}
