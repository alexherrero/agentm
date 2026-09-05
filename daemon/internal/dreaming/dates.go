package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// Job "dates" — relative-to-absolute date conversion in aging notes. A note
// that says "last week" means something on the day it was written and
// nothing a year later, so once a note is older than `date_gloss_after_days`
// the pass adds the absolute date beside each relative phrase, anchored on
// the note's own `captured` (else `created`) day: `last week (the week of
// 2026-08-24)`, `yesterday (2026-09-04)`, `3 days ago (2026-09-02)`. The
// words stay — the gloss is additive, in parentheses, never a rewrite — and a
// phrase already glossed is left alone, so the pass is idempotent. Nothing
// inside the frontmatter, a fenced block or an inline code span is touched,
// and "recently", the vault's commonest relative word, is unresolvable and
// deliberately not on the list.
//
// The landing shape is this task's ruling, recorded on the plan: the design
// names the job and no shape; the additive gloss is the reading every other
// mutation here already takes (nothing destructive, fingerprint-guarded).

const (
	JobDates                  = "dates"
	DefaultDateGlossAfterDays = 30.0
	dateKeyGlossAfter         = "date_gloss_after_days"
)

var (
	relativeDayRe  = regexp.MustCompile(`(?i)\b(earlier today|yesterday|tomorrow)\b`)
	relativeSpanRe = regexp.MustCompile(`(?i)\b(last|this|next) (week|month)\b`)
	relativeAgoRe  = regexp.MustCompile(`(?i)\b(\d{1,3}) (days?|weeks?) ago\b`)
	glossFollowsRe = regexp.MustCompile(`^\s*\((\d{4}-\d{2}|the week of )`)
)

// DateGlossAfter reads the threshold from the contract.
func DateGlossAfter(r *rules.Rules) float64 {
	if r != nil && r.Thresholds != nil {
		if v, ok := r.Thresholds[dateKeyGlossAfter]; ok && v > 0 {
			return v
		}
	}
	return DefaultDateGlossAfterDays
}

// Gloss is one phrase resolved against an anchor.
type Gloss struct {
	Rel    string `json:"rel"`
	Phrase string `json:"phrase"`
	Gloss  string `json:"gloss"`
}

// DatesPlan is what the pass would (or did) gloss.
type DatesPlan struct {
	Intents    []Intent `json:"-"`
	Glossed    []Gloss  `json:"glossed"`
	Considered int      `json:"considered"`
	Aging      int      `json:"aging"`
}

func mondayOf(d time.Time) time.Time {
	wd := int(d.Weekday()+6) % 7
	return d.AddDate(0, 0, -wd)
}

// resolve turns one matched phrase into its gloss text against `anchor`.
func resolve(phrase string, anchor time.Time) string {
	p := strings.ToLower(phrase)
	day := func(t time.Time) string { return t.Format("2006-01-02") }
	week := func(t time.Time) string { return "the week of " + day(mondayOf(t)) }
	switch p {
	case "earlier today":
		return day(anchor)
	case "yesterday":
		return day(anchor.AddDate(0, 0, -1))
	case "tomorrow":
		return day(anchor.AddDate(0, 0, 1))
	case "last week":
		return week(anchor.AddDate(0, 0, -7))
	case "this week":
		return week(anchor)
	case "next week":
		return week(anchor.AddDate(0, 0, 7))
	case "last month":
		return anchor.AddDate(0, -1, 0).Format("2006-01")
	case "this month":
		return anchor.Format("2006-01")
	case "next month":
		return anchor.AddDate(0, 1, 0).Format("2006-01")
	}
	if m := relativeAgoRe.FindStringSubmatch(phrase); m != nil {
		n, _ := strconv.Atoi(m[1])
		if strings.HasPrefix(strings.ToLower(m[2]), "week") {
			return week(anchor.AddDate(0, 0, -7*n))
		}
		return day(anchor.AddDate(0, 0, -n))
	}
	return ""
}

// GlossBody adds the glosses to one body, leaving fenced blocks, inline
// code and already-glossed phrases alone. Returns the new body and what
// was glossed.
func GlossBody(body string, anchor time.Time) (string, []Gloss) {
	var out []string
	var glossed []Gloss
	inFence := false
	for _, line := range strings.Split(body, "\n") {
		if fenceRe.MatchString(strings.TrimSpace(line)) {
			inFence = !inFence
			out = append(out, line)
			continue
		}
		if inFence {
			out = append(out, line)
			continue
		}
		spans := inlineCodeRe.FindAllStringIndex(line, -1)
		inCode := func(pos int) bool {
			for _, s := range spans {
				if s[0] <= pos && pos < s[1] {
					return true
				}
			}
			return false
		}
		type hit struct{ start, end int }
		var hits []hit
		for _, re := range []*regexp.Regexp{relativeDayRe, relativeSpanRe, relativeAgoRe} {
			for _, m := range re.FindAllStringIndex(line, -1) {
				hits = append(hits, hit{m[0], m[1]})
			}
		}
		// Left to right, non-overlapping.
		for i := 0; i < len(hits); i++ {
			for j := i + 1; j < len(hits); j++ {
				if hits[j].start < hits[i].start {
					hits[i], hits[j] = hits[j], hits[i]
				}
			}
		}
		var b strings.Builder
		last := 0
		for _, h := range hits {
			if h.start < last || inCode(h.start) || glossFollowsRe.MatchString(line[h.end:]) {
				continue
			}
			phrase := line[h.start:h.end]
			g := resolve(phrase, anchor)
			if g == "" {
				continue
			}
			b.WriteString(line[last:h.end])
			b.WriteString(" (" + g + ")")
			last = h.end
			glossed = append(glossed, Gloss{Phrase: phrase, Gloss: g})
		}
		b.WriteString(line[last:])
		out = append(out, b.String())
	}
	return strings.Join(out, "\n"), glossed
}

// PlanDates decides the glosses over every aging memory. It writes nothing.
func PlanDates(root string, r *rules.Rules, now time.Time) (DatesPlan, error) {
	var plan DatesPlan
	after := DateGlossAfter(r)
	rels, err := MemoryNotes(root)
	if err != nil {
		return plan, err
	}
	today := time.Date(now.UTC().Year(), now.UTC().Month(), now.UTC().Day(), 0, 0, 0, 0, time.UTC)
	for _, rel := range rels {
		if classOf(rel) == mocClass {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
		if err != nil {
			continue
		}
		text := string(raw)
		if !strings.HasPrefix(text, "---\n") {
			continue
		}
		fm, _ := ParseFrontmatter(text)
		plan.Considered++
		anchorStr := anchorOf(fm)
		if anchorStr == "" {
			continue
		}
		anchor, err := time.Parse("2006-01-02", anchorStr)
		if err != nil || today.Sub(anchor).Hours()/24 <= after {
			continue
		}
		plan.Aging++
		end := strings.Index(text[4:], "\n---\n")
		if end < 0 {
			continue
		}
		head := text[:end+4+5]
		body := text[end+4+5:]
		newBody, glossed := GlossBody(body, anchor)
		if len(glossed) == 0 {
			continue
		}
		var phrases []string
		for i := range glossed {
			glossed[i].Rel = rel
			phrases = append(phrases, glossed[i].Phrase+" ("+glossed[i].Gloss+")")
		}
		plan.Glossed = append(plan.Glossed, glossed...)
		plan.Intents = append(plan.Intents, Intent{Job: JobDates, Rel: rel, Before: raw, After: []byte(head + newBody),
			Summary: fmt.Sprintf("%s — written %s; glossed %s", rel, anchorStr, strings.Join(phrases, ", "))})
	}
	return plan, nil
}
