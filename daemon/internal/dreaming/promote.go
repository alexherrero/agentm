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

	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// Job "promote" — recurrence across the sessions' own records, into semantic
// candidates (agentm-vault § Dreaming, sessions 3 and 4).
//
// It reads two sections of every session trace: `## Captured`, the cards the
// session wrote down, and `## Candidates`, what it said in passing that the
// miner judged below HIGH — one line each, the rule that fired and the words it
// fired on. A candidate whose words three distinct sessions carry is a real
// recurrence: the same thing said three times. It becomes a semantic
// candidate — `status: unfiled`, no `why`, `derived_from` naming the traces —
// for the next enrichment batch to judge like any other card. A reply the miner
// refused to file, or an excerpt it cut mid-sentence, never counts, and nor does
// a sighting that quotes its own trace's `## Asked` or the always-load tier: the
// three are counted as independent origins, not repeats of one text (see
// promotable). A `## Captured`
// link three sessions carry names a card that already exists; it is reported,
// and nothing is written.
//
// What it no longer does, and why. It read every `[[wikilink]]` in an episodic
// note, which in a trace is mostly `## Recalled` — forty basenames the recall
// hook injected — so recurrence over it counted what recall happened to show,
// and on 2026-09-06 it would have promoted `zorbulax`, a test token. And it
// wrote `crystallized/`, which holds syntheses a model writes at a task's close
// or on request, never a counter's output. Deterministic: no model, no
// randomness, sorted everywhere; sources are never touched and an existing
// note is never overwritten.

const (
	JobPromote    = "promote"
	MinRecurrence = 3
	// DefaultPromoteCap bounds the candidates one pass writes. A recurrence
	// storm — a hook that pasted the same line into every trace — should cost
	// a page of candidates for the batch to judge, not a class folder.
	DefaultPromoteCap = 10
)

var (
	wikilinkRe   = regexp.MustCompile(`\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]`)
	inlineCodeRe = regexp.MustCompile("`[^`\n]*`")
	fenceRe      = regexp.MustCompile("^```")
	// candidateRe is one `## Candidates` bullet as episodic_trace.py writes
	// it: `- <rule> (×N) — “<excerpt>”`, the count and the excerpt optional.
	candidateRe = regexp.MustCompile(`^-\s+(.+?)(?:\s+\(×\d+\))?(?:\s+—\s+[“"](.*)[”"])?\s*$`)
	// quoteFragmentRe ends a fragment of an excerpt: a sentence, a line, or the
	// `...` a window was cut at. reflect.py's `_QUOTE_FRAGMENT` is the same.
	quoteFragmentRe = regexp.MustCompile(`\.\.\.|…|[.!?]\s+|\n+`)
)

// quoteMinWords is the shortest fragment that can be a quote; a shorter one
// is a connective ("ok, and"), too short to be anybody's in particular.
const quoteMinWords = 4

// quotedIn reports whether an excerpt quotes a source already folded with
// candidateKey: every fragment of four or more words in it appears there, and
// at least one does. reflect.py's `quoted_in` is the same rule.
func quotedIn(excerpt, folded string) bool {
	if folded == "" {
		return false
	}
	found := false
	for _, frag := range quoteFragmentRe.Split(excerpt, -1) {
		f := candidateKey(frag)
		if len(strings.Fields(f)) < quoteMinWords {
			continue
		}
		if !strings.Contains(folded, f) {
			return false
		}
		found = true
	}
	return found
}

// tracesAsked is one trace's `## Asked` — the session's first request —
// folded, outside fenced blocks.
func tracesAsked(content string) string {
	var lines []string
	in, inFence := false, false
	for _, line := range strings.Split(content, "\n") {
		if fenceRe.MatchString(strings.TrimSpace(line)) {
			inFence = !inFence
			continue
		}
		if strings.HasPrefix(line, "## ") || strings.HasPrefix(line, "# ") {
			in = strings.TrimSpace(strings.TrimPrefix(line, "## ")) == "Asked"
			continue
		}
		if in && !inFence {
			lines = append(lines, line)
		}
	}
	return candidateKey(strings.Join(lines, "\n"))
}

// alwaysLoadText is the always-load tier, folded: every `*.md` in the vault's
// `standards/` (the first of the vault root's and the memory root's that
// exists) and in the retired `memory/_always-load/` pen, a stem the pen shares
// with standards read once, generated navigation (`moc-*`) left out —
// `vault_layout.always_load_files`, the reader the session-start loader and the
// miner share.
func alwaysLoadText(root string) string {
	var dirs []string
	for _, d := range []string{filepath.Join(vaultRootOf(root), "standards"), filepath.Join(root, "standards")} {
		if isDir(d) {
			dirs = append(dirs, d)
			break
		}
	}
	if pen := filepath.Join(root, "memory", "_always-load"); isDir(pen) {
		dirs = append(dirs, pen)
	}
	seen := map[string]bool{}
	var parts []string
	for _, d := range dirs {
		entries, err := os.ReadDir(d)
		if err != nil {
			continue
		}
		names := make([]string, 0, len(entries))
		for _, e := range entries {
			if !e.IsDir() && strings.HasSuffix(e.Name(), ".md") {
				names = append(names, e.Name())
			}
		}
		sort.Strings(names)
		for _, name := range names {
			stem := strings.TrimSuffix(name, ".md")
			if strings.HasPrefix(stem, "moc-") || seen[stem] {
				continue
			}
			seen[stem] = true
			if raw, err := os.ReadFile(filepath.Join(d, name)); err == nil {
				parts = append(parts, candidateKey(string(raw)))
			}
		}
	}
	return strings.Join(parts, " | ")
}

// Promotion is one recurring item the pass found.
type Promotion struct {
	// Target is what recurred: a candidate's words, or a captured card's link.
	Target  string   `json:"target"`
	Sources []string `json:"sources"`
	Rel     string   `json:"rel"`
	Summary string   `json:"summary"`
}

// PromotePlan is what one pass would (or did) write.
type PromotePlan struct {
	Intents []Intent `json:"-"`
	// Promotions are the semantic candidates written.
	Promotions []Promotion `json:"promotions"`
	// Existing are recurring items that already have a note: a captured card,
	// or a candidate a previous pass wrote. Never rewritten.
	Existing []Promotion `json:"existing"`
	// Deferred are candidates past the cap, for the next pass.
	Deferred int `json:"deferred,omitempty"`
	// Sources is how many session traces were read.
	Sources int `json:"sources"`
}

// traceSections returns the bullet lines under `## Captured` and
// `## Candidates` in one trace, outside fenced blocks.
func traceSections(content string) (captured, candidates []string) {
	var section string
	inFence := false
	for _, line := range strings.Split(content, "\n") {
		trimmed := strings.TrimSpace(line)
		if fenceRe.MatchString(trimmed) {
			inFence = !inFence
			continue
		}
		if inFence {
			continue
		}
		if strings.HasPrefix(line, "## ") {
			section = strings.TrimSpace(strings.TrimPrefix(line, "## "))
			continue
		}
		if strings.HasPrefix(line, "# ") {
			section = ""
			continue
		}
		if !strings.HasPrefix(trimmed, "- ") {
			continue
		}
		switch section {
		case "Captured":
			captured = append(captured, trimmed)
		case "Candidates":
			candidates = append(candidates, trimmed)
		}
	}
	return captured, candidates
}

// capturedTargets are the card links a `## Captured` section names.
func capturedTargets(lines []string) []string {
	var out []string
	for _, l := range lines {
		l = inlineCodeRe.ReplaceAllString(l, "")
		for _, m := range wikilinkRe.FindAllStringSubmatch(l, -1) {
			out = append(out, strings.TrimSpace(m[1]))
		}
	}
	return out
}

// candidateKey is what makes two sessions' candidates the same candidate: the
// words, lower-cased, with punctuation and runs of space folded. Not the rule —
// two rules can fire on one sentence — and not the count, which is how often it
// was said within one session.
func candidateKey(excerpt string) string {
	var b strings.Builder
	space := false
	for _, r := range strings.ToLower(excerpt) {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			b.WriteRune(r)
			space = false
		} else if !space && b.Len() > 0 {
			b.WriteByte(' ')
			space = true
		}
	}
	return strings.TrimSpace(b.String())
}

// promotable says whether a `## Candidates` line can become a card at all,
// however many sessions carry it. Two kinds cannot.
//
// A reply the miner logged and refused to file (`not filed: …`): the trace is
// where a reply is logged, and three sessions answering "yes" is not a memory.
//
// An excerpt the miner cut at its start: `reflect`'s window around a match
// opens with an ellipsis when the message went on before it, so it begins
// mid-sentence and cannot stand as a statement. What recurs across sessions
// there is a pasted block — the same standing instructions in three task
// prompts — not the same thing said three times; the first two candidates this
// job ever wrote (2026-10-04) were two windows of one such paragraph. A window
// cut only at its end starts where the operator did, and still counts.
//
// And a sighting that is not an independent origin (task 192, locked call 3):
// an excerpt that quotes `origins` — the trace's own `## Asked`, the session's
// first request, and the always-load tier, each folded — is the session
// carrying a text it was given, not saying it. The 2026-10-07 audit found a
// CLAUDE.md paragraph pasted into three chip briefs promoted as "said in 3
// sessions"; three copies of one paste would pass any recurrence bar, so
// MinRecurrence stays 3 and counts origins instead.
func promotable(rule, excerpt string, origins ...string) bool {
	if strings.HasPrefix(strings.TrimSpace(rule), "not filed:") {
		return false
	}
	e := strings.TrimSpace(excerpt)
	if strings.HasPrefix(e, "...") || strings.HasPrefix(e, "…") {
		return false
	}
	for _, o := range origins {
		if quotedIn(e, o) {
			return false
		}
	}
	return true
}

// candidateSlug is a candidate's filename stem: its first words, hyphenated.
func candidateSlug(key string) string {
	words := strings.Fields(key)
	if len(words) > 8 {
		words = words[:8]
	}
	return "candidate-" + strings.Join(words, "-")
}

// RenderCandidate is the semantic candidate a recurrence becomes: unjudged,
// with no `why` — no writer that knows wrote it — and the traces it came from
// in `derived_from`. The excerpt is quoted in an Evidence block, because it is
// what was said and the batch will keep it that way.
func RenderCandidate(excerpt string, sources []string, defaultType, today string) string {
	title := excerpt
	if r := []rune(title); len(r) > 80 {
		title = strings.TrimSpace(string(r[:80])) + "…"
	}
	links := make([]string, len(sources))
	for i, s := range sources {
		links[i] = `"[[` + strings.TrimSuffix(filepath.Base(s), ".md") + `]]"`
	}
	var b strings.Builder
	b.WriteString("---\n")
	fmt.Fprintf(&b, "title: %s\n", yamlQuote(title))
	if defaultType != "" {
		fmt.Fprintf(&b, "type: %s\n", defaultType)
	}
	b.WriteString("status: unfiled\n")
	b.WriteString("lifecycle: active\n")
	// The type is the contract's default, a guess rather than a judgment, so
	// the filing confidence is low — what capture writes for a note it typed
	// by default, and what keeps the card off `active` until a pass judges it.
	b.WriteString("filing_confidence: low\n")
	b.WriteString("source: conversation\n")
	b.WriteString("trust: trusted\n")
	fmt.Fprintf(&b, "created: %s\n", today)
	fmt.Fprintf(&b, "updated: %s\n", today)
	fmt.Fprintf(&b, "derived_from: [%s]\n", strings.Join(links, ", "))
	b.WriteString("---\n\n")
	fmt.Fprintf(&b, "Said in passing in %d sessions, and promoted for a judgment.\n\n", len(sources))
	b.WriteString("## Evidence\n\n")
	fmt.Fprintf(&b, "> %s\n", excerpt)
	return b.String()
}

func yamlQuote(s string) string {
	return `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`, "\n", " ").Replace(s) + `"`
}

// noteExists reports whether a note with this stem lives anywhere under the
// memory root — Obsidian resolves a link by its basename, so a card is found
// wherever it was filed.
func noteExists(stems map[string]bool, target string) bool {
	return stems[strings.ToLower(strings.TrimSuffix(filepath.Base(target), ".md"))]
}

// PlanPromote decides the promotions. It writes nothing.
func PlanPromote(root string, contract *rules.Rules, now time.Time) (PromotePlan, error) {
	var plan PromotePlan
	rels, err := MemoryNotes(root)
	if err != nil {
		return plan, err
	}
	stems := map[string]bool{}
	for _, rel := range rels {
		stems[strings.ToLower(strings.TrimSuffix(filepath.Base(rel), ".md"))] = true
	}

	captured := map[string]map[string]bool{}
	candidates := map[string]map[string]bool{}
	excerpts := map[string]string{}
	standards := alwaysLoadText(root)
	for _, rel := range rels {
		if classOf(rel) != "episodic" {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
		if err != nil {
			continue
		}
		fm, _ := ParseFrontmatter(string(raw))
		if fm["kind"] != "session-trace" {
			continue
		}
		plan.Sources++
		capLines, candLines := traceSections(string(raw))
		asked := tracesAsked(string(raw))
		for _, t := range capturedTargets(capLines) {
			if captured[t] == nil {
				captured[t] = map[string]bool{}
			}
			captured[t][rel] = true
		}
		for _, l := range candLines {
			m := candidateRe.FindStringSubmatch(l)
			if m == nil || strings.TrimSpace(m[2]) == "" || !promotable(m[1], m[2], asked, standards) {
				continue
			}
			key := candidateKey(m[2])
			if key == "" {
				continue
			}
			if candidates[key] == nil {
				candidates[key] = map[string]bool{}
				excerpts[key] = strings.TrimSpace(m[2])
			}
			candidates[key][rel] = true
		}
	}

	// A card three sessions captured already exists; it is reported.
	for _, t := range sortedKeys(captured) {
		if len(captured[t]) < MinRecurrence {
			continue
		}
		sources := sortedKeys(captured[t])
		item := Promotion{Target: t, Sources: sources,
			Summary: fmt.Sprintf("[[%s]] was captured in %d sessions — already a card; nothing written", t, len(sources))}
		plan.Existing = append(plan.Existing, item)
	}

	defaultType := ""
	if contract != nil {
		defaultType = contract.DefaultType
	}
	today := now.UTC().Format("2006-01-02")
	for _, key := range sortedKeys(candidates) {
		if len(candidates[key]) < MinRecurrence {
			continue
		}
		sources := sortedKeys(candidates[key])
		slug := candidateSlug(key)
		rel := "memory/semantic/" + slug + ".md"
		item := Promotion{Target: excerpts[key], Sources: sources, Rel: rel}
		if noteExists(stems, slug) {
			item.Summary = fmt.Sprintf("%q recurs across %d sessions — already a candidate at %s; not overwriting",
				excerpts[key], len(sources), rel)
			plan.Existing = append(plan.Existing, item)
			continue
		}
		if len(plan.Promotions) >= DefaultPromoteCap {
			plan.Deferred++
			continue
		}
		item.Summary = fmt.Sprintf("%q recurs across %d sessions — a semantic candidate at %s for the batch to judge",
			excerpts[key], len(sources), rel)
		plan.Promotions = append(plan.Promotions, item)
		plan.Intents = append(plan.Intents, Intent{Job: JobPromote, Rel: rel, Before: nil,
			After: []byte(RenderCandidate(excerpts[key], sources, defaultType, today)), Summary: item.Summary})
	}
	return plan, nil
}

func sortedKeys[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
