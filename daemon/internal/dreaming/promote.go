package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode"
)

// Job "promote" — the port of consolidate.py: recurrence-gated promotion
// into `crystallized/`. Typed edges are read off every episodic note the
// way graph.py reads them — a `[[wikilink]]` outside a fenced block and
// outside an inline code span, plus a `supersedes:` / `superseded_by:`
// frontmatter value — and grouped by target. A target that three or more
// DISTINCT episodic notes reference is durable, not incidental, and earns a
// consolidated entry: `kind: crystallized`, `lifecycle_tier: durable`, the
// sources named in `derived_from` and `consolidated_from` — the provenance
// the contract requires of every derived-class note and the CI gate checks.
// The sources are never touched; an entry that already exists is never
// overwritten. Deterministic: no model, no randomness, sorted everywhere.

const (
	JobPromote    = "promote"
	MinRecurrence = 3 // consolidate.MIN_RECURRENCE
	DigestKind    = "crystallized"
)

var (
	wikilinkRe   = regexp.MustCompile(`\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]`)
	inlineCodeRe = regexp.MustCompile("`[^`\n]*`")
	fenceRe      = regexp.MustCompile("^```")
)

// Promotion is one consolidated entry the pass would (or did) write.
type Promotion struct {
	Target  string   `json:"target"`
	Sources []string `json:"sources"`
	Rel     string   `json:"rel"`
	Summary string   `json:"summary"`
}

// PromotePlan is what one pass would (or did) promote.
type PromotePlan struct {
	Intents    []Intent    `json:"-"`
	Promotions []Promotion `json:"promotions"`
	// Existing names the recurring targets already promoted (never rewritten).
	Existing []Promotion `json:"existing"`
	// Sources is how many episodic notes were read.
	Sources int `json:"sources"`
}

// Edges is graph.extract_edges: the targets one note references, in order.
func Edges(content string) []string {
	var targets []string
	lines := strings.Split(content, "\n")
	inFence := false
	fenced := map[int]bool{}
	starts := make([]int, len(lines))
	offset := 0
	for i, line := range lines {
		starts[i] = offset
		if fenceRe.MatchString(strings.TrimSpace(line)) {
			inFence = !inFence
			fenced[i] = true
		} else if inFence {
			fenced[i] = true
		}
		offset += len(line) + 1
	}
	for _, m := range wikilinkRe.FindAllStringSubmatchIndex(content, -1) {
		lineIdx := 0
		for i := len(starts) - 1; i >= 0; i-- {
			if starts[i] <= m[0] {
				lineIdx = i
				break
			}
		}
		if fenced[lineIdx] {
			continue
		}
		col := m[0] - starts[lineIdx]
		inCode := false
		for _, span := range inlineCodeRe.FindAllStringIndex(lines[lineIdx], -1) {
			if span[0] <= col && col < span[1] {
				inCode = true
				break
			}
		}
		if inCode {
			continue
		}
		targets = append(targets, strings.TrimSpace(content[m[2]:m[3]]))
	}
	if strings.HasPrefix(content, "---\n") {
		if end := strings.Index(content[4:], "\n---\n"); end >= 0 {
			for _, line := range strings.Split(content[4:end+4], "\n") {
				key, value, ok := strings.Cut(line, ":")
				if !ok {
					continue
				}
				k := strings.TrimSpace(key)
				if k != "supersedes" && k != "superseded_by" {
					continue
				}
				v := strings.TrimSpace(value)
				v = strings.Trim(v, `"`)
				v = strings.Trim(v, `'`)
				if m := wikilinkRe.FindStringSubmatch(v); m != nil && strings.HasPrefix(v, "[[") {
					v = strings.TrimSpace(m[1])
				}
				if v != "" {
					targets = append(targets, v)
				}
			}
		}
	}
	return targets
}

// ConsolidatedSlug is consolidate._consolidated_slug: `consolidated-<stem>`
// with every non-alphanumeric, non-dash character folded to a dash and the
// dashes collapsed.
func ConsolidatedSlug(target string) string {
	stem := strings.TrimSuffix(filepath.Base(target), filepath.Ext(target))
	var b strings.Builder
	for _, r := range strings.ToLower(stem) {
		if unicode.IsLetter(r) || unicode.IsDigit(r) || r == '-' {
			b.WriteRune(r)
		} else {
			b.WriteRune('-')
		}
	}
	var parts []string
	for _, p := range strings.Split(b.String(), "-") {
		if p != "" {
			parts = append(parts, p)
		}
	}
	return "consolidated-" + strings.Join(parts, "-")
}

// pyRepr renders a string the way Python's repr does for the plain case:
// single quotes unless the text holds a single quote and no double quote.
func pyRepr(s string) string {
	q := "'"
	if strings.Contains(s, "'") && !strings.Contains(s, `"`) {
		q = `"`
	}
	s = strings.ReplaceAll(s, `\`, `\\`)
	if q == "'" {
		s = strings.ReplaceAll(s, "'", `\'`)
	}
	return q + s + q
}

// RenderConsolidated is consolidate_target's note: the locked-order
// frontmatter save._build_frontmatter emits, the five-section digest body
// crystallize._render_body renders, and the provenance the contract names.
func RenderConsolidated(target string, sources []string, today string) (rel, content string) {
	slug := ConsolidatedSlug(target)
	n := len(sources)
	var inv strings.Builder
	fmt.Fprintf(&inv, "%d episodic entries reference %s:\n", n, pyRepr(target))
	for i, p := range sources {
		if i > 0 {
			inv.WriteString("\n")
		}
		inv.WriteString("- " + p)
	}
	sections := []struct{ title, value string }{
		{"Question", fmt.Sprintf("What recurring reference to %s appears across episodic entries?", pyRepr(target))},
		{"Investigation", inv.String()},
		{"Findings", fmt.Sprintf("%s recurs across %d distinct entries (recurrence floor: %d), a deterministic signal that this is durable, not incidental.", pyRepr(target), n, MinRecurrence)},
		{"Lessons", fmt.Sprintf("Promoted episodic -> semantic (V6-4). The consolidated entry is durable (decay-exempt) and carries a derived_from provenance edge back to its %d sources; none of those sources were deleted or modified.", n)},
		{"Open threads", ""},
	}
	var body []string
	for _, s := range sections {
		body = append(body, fmt.Sprintf("## %s\n\n%s\n", s.title, strings.TrimSpace(s.value)))
	}
	list := "[" + strings.Join(sources, ", ") + "]"
	fm := strings.Join([]string{
		"---",
		"kind: " + DigestKind,
		"status: active",
		"altitude: artifact",
		"created: " + today,
		"updated: " + today,
		"tags: []",
		"group: memory",
		"slug: " + slug,
		"always_load: false",
		"lifecycle_tier: durable",
		"derived_from: " + list,
		"consolidated_from: " + list,
		"---",
	}, "\n") + "\n"
	text := strings.Join(body, "\n")
	if !strings.HasSuffix(text, "\n") {
		text += "\n"
	}
	return "memory/" + DigestKind + "/" + slug + ".md", fm + "\n" + text
}

// RecurringTargets is consolidate.find_recurring_targets over the episodic
// notes under `root`: {target: sorted distinct sources} for targets at or
// past the floor. Returns the episodic paths read, for the report.
func RecurringTargets(root string, minRecurrence int) (map[string][]string, int, error) {
	if minRecurrence <= 0 {
		minRecurrence = MinRecurrence
	}
	rels, err := MemoryNotes(root)
	if err != nil {
		return nil, 0, err
	}
	byTarget := map[string]map[string]bool{}
	read := 0
	for _, rel := range rels {
		if classOf(rel) != "episodic" {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
		if err != nil {
			continue
		}
		read++
		for _, target := range Edges(string(raw)) {
			if byTarget[target] == nil {
				byTarget[target] = map[string]bool{}
			}
			byTarget[target][rel] = true
		}
	}
	out := map[string][]string{}
	for target, sources := range byTarget {
		if len(sources) < minRecurrence {
			continue
		}
		var list []string
		for s := range sources {
			list = append(list, s)
		}
		sort.Strings(list)
		out[target] = list
	}
	return out, read, nil
}

// PlanPromote decides the promotions. It writes nothing.
func PlanPromote(root string, now time.Time) (PromotePlan, error) {
	var plan PromotePlan
	recurring, read, err := RecurringTargets(root, MinRecurrence)
	if err != nil {
		return plan, err
	}
	plan.Sources = read
	var targets []string
	for t := range recurring {
		targets = append(targets, t)
	}
	sort.Strings(targets)
	today := now.UTC().Format("2006-01-02")
	for _, target := range targets {
		sources := recurring[target]
		rel, content := RenderConsolidated(target, sources, today)
		item := Promotion{Target: target, Sources: sources, Rel: rel,
			Summary: fmt.Sprintf("%s recurs across %d episodic entries — promote to %s with its provenance", target, len(sources), rel)}
		if _, err := os.Stat(filepath.Join(root, filepath.FromSlash(rel))); err == nil {
			item.Summary = fmt.Sprintf("%s recurs across %d episodic entries — already promoted at %s; not overwriting", target, len(sources), rel)
			plan.Existing = append(plan.Existing, item)
			continue
		}
		plan.Promotions = append(plan.Promotions, item)
		plan.Intents = append(plan.Intents, Intent{Job: JobPromote, Rel: rel, Before: nil, After: []byte(content), Summary: item.Summary})
	}
	return plan, nil
}
