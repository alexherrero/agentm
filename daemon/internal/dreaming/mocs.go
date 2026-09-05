package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// Job "mocs" — MOC regeneration with a context phrase per link. One map of
// content per memory type under `memory/mocs/`, created only once a type
// has `moc_min_members` notes (five), split into numbered pages past
// `moc_split_at` (forty), and flagged stale when its newest member is older
// than `moc_stale_after_days` (ninety). A page carries its newest member's
// date as `updated`, so a regeneration on an unchanged membership is
// byte-identical and writes nothing; `created` survives regeneration. A
// page whose type has since fallen below the floor is left alone and
// reported — nothing here deletes.

const (
	JobMocs                  = "mocs"
	DefaultMocMinMembers     = 5
	DefaultMocSplitAt        = 40
	DefaultMocStaleAfterDays = 90
	mocPhraseChars           = 120
	mocKeyMinMembers         = "moc_min_members"
	mocKeySplitAt            = "moc_split_at"
	mocKeyStaleAfterDays     = "moc_stale_after_days"
	mocGeneratedBy           = "agentmdream"
	mocClass                 = "mocs"
)

// MocThresholds reads the three thresholds from the contract, defaults for
// a contract without them.
func MocThresholds(r *rules.Rules) (minMembers, splitAt int, staleAfter float64) {
	minMembers, splitAt, staleAfter = DefaultMocMinMembers, DefaultMocSplitAt, DefaultMocStaleAfterDays
	if r == nil || r.Thresholds == nil {
		return
	}
	if v, ok := r.Thresholds[mocKeyMinMembers]; ok && v > 0 {
		minMembers = int(v)
	}
	if v, ok := r.Thresholds[mocKeySplitAt]; ok && v > 0 {
		splitAt = int(v)
	}
	if v, ok := r.Thresholds[mocKeyStaleAfterDays]; ok && v > 0 {
		staleAfter = v
	}
	return
}

// Member is one note on a map.
type Member struct {
	Rel    string
	Slug   string
	Title  string
	Phrase string
	Anchor string // YYYY-MM-DD, captured else created; "" when neither
}

// MocPage is one generated page.
type MocPage struct {
	Type    string `json:"type"`
	Rel     string `json:"rel"`
	Members int    `json:"members"`
	Page    int    `json:"page"`
	Pages   int    `json:"pages"`
	Stale   bool   `json:"stale"`
	Newest  string `json:"newest,omitempty"`
	Changed bool   `json:"changed"`
}

// MocsPlan is what the regeneration would (or did) write.
type MocsPlan struct {
	Intents    []Intent       `json:"-"`
	Pages      []MocPage      `json:"pages"`
	BelowFloor map[string]int `json:"below_floor"`
	Considered int            `json:"considered"`
}

// contextPhrase is the first prose line of a body, cut on a word boundary.
func contextPhrase(body string) string {
	for _, line := range strings.Split(body, "\n") {
		l := strings.TrimSpace(line)
		if l == "" || strings.HasPrefix(l, "#") || strings.HasPrefix(l, "```") || strings.HasPrefix(l, "<!--") {
			continue
		}
		if len(l) > mocPhraseChars {
			cut := l[:mocPhraseChars]
			if i := strings.LastIndex(cut, " "); i >= 0 {
				cut = cut[:i]
			}
			return cut + " …"
		}
		return l
	}
	return ""
}

func anchorOf(fm map[string]string) string {
	for _, k := range []string{"captured", "created"} {
		if v := strings.TrimSpace(fm[k]); len(v) >= 10 {
			return v[:10]
		}
	}
	return ""
}

// members reads every memory typed `t` across the classes (the maps'
// own class excepted), superseded and archived notes left out.
func mocMembers(root string, r *rules.Rules) (map[string][]Member, int, error) {
	rels, err := MemoryNotes(root)
	if err != nil {
		return nil, 0, err
	}
	byType := map[string][]Member{}
	considered := 0
	for _, rel := range rels {
		if classOf(rel) == mocClass {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
		if err != nil {
			continue
		}
		fm, body := ParseFrontmatter(string(raw))
		if fm["kind"] != "" {
			continue
		}
		t := strings.TrimSpace(fm["type"])
		if t == "" {
			continue
		}
		considered++
		if r != nil {
			if repl, dep := r.ReplacementFor(t); dep && repl != "" {
				t = repl
			}
		}
		lc := strings.ToLower(strings.TrimSpace(fm["lifecycle"]))
		if lc == "superseded" || lc == "archived" || strings.ToLower(fm["status"]) == "superseded" {
			continue
		}
		slug := strings.TrimSpace(fm["slug"])
		if slug == "" {
			slug = strings.TrimSuffix(filepath.Base(rel), ".md")
		}
		title := strings.TrimSpace(fm["title"])
		if title == "" {
			title = slug
		}
		byType[t] = append(byType[t], Member{Rel: rel, Slug: slug, Title: title, Phrase: contextPhrase(body), Anchor: anchorOf(fm)})
	}
	for t := range byType {
		ms := byType[t]
		sort.SliceStable(ms, func(i, j int) bool {
			if ms[i].Anchor != ms[j].Anchor {
				return ms[i].Anchor > ms[j].Anchor // newest first; "" last
			}
			return ms[i].Slug < ms[j].Slug
		})
		byType[t] = ms
	}
	return byType, considered, nil
}

func mocRel(t string, page int) string {
	if page <= 1 {
		return "memory/" + mocClass + "/" + t + ".md"
	}
	return fmt.Sprintf("memory/%s/%s-%d.md", mocClass, t, page)
}

// RenderMoc renders one page. `created` is the page's own, preserved.
func RenderMoc(t string, members []Member, total, page, pages int, created, newest string, stale bool, staleDays float64) string {
	slug := t
	if page > 1 {
		slug = fmt.Sprintf("%s-%d", t, page)
	}
	if created == "" {
		created = newest
	}
	lines := []string{"---", "title: " + t + " — map of content", "kind: moc", "status: active",
		"created: " + created, "updated: " + newest, "tags: [moc, " + t + "]", "group: memory", "slug: " + slug,
		"type_of_members: " + t, fmt.Sprintf("members: %d", total)}
	if pages > 1 {
		lines = append(lines, fmt.Sprintf("page: %d of %d", page, pages))
	}
	if stale {
		lines = append(lines, "stale: true")
	}
	lines = append(lines, "generated_by: "+mocGeneratedBy, "---", "", "# "+t, "", "[[Home]]", "",
		fmt.Sprintf("%d note%s typed `%s`, newest first by their captured or created date. Generated from the notes' own frontmatter; not edited by hand.",
			total, map[bool]string{true: "", false: "s"}[total == 1], t))
	if pages > 1 {
		lines = append(lines, "", fmt.Sprintf("Page %d of %d.", page, pages))
	}
	if stale {
		lines = append(lines, "", fmt.Sprintf("Stale: the newest member is from %s, more than %.0f days ago.", newest, staleDays))
	}
	lines = append(lines, "", "## Members", "")
	for _, m := range members {
		line := fmt.Sprintf("- [[%s]] — %s", m.Slug, m.Title)
		if m.Phrase != "" {
			line += " · " + m.Phrase
		}
		lines = append(lines, line)
	}
	lines = append(lines, "")
	return strings.TrimRight(strings.Join(lines, "\n"), "\n") + "\n"
}

// PlanMocs decides the pages. It writes nothing.
func PlanMocs(root string, r *rules.Rules, now time.Time) (MocsPlan, error) {
	plan := MocsPlan{BelowFloor: map[string]int{}}
	minMembers, splitAt, staleAfter := MocThresholds(r)
	byType, considered, err := mocMembers(root, r)
	if err != nil {
		return plan, err
	}
	plan.Considered = considered
	var types []string
	for t := range byType {
		types = append(types, t)
	}
	sort.Strings(types)
	today := time.Date(now.UTC().Year(), now.UTC().Month(), now.UTC().Day(), 0, 0, 0, 0, time.UTC)
	for _, t := range types {
		ms := byType[t]
		if len(ms) < minMembers {
			plan.BelowFloor[t] = len(ms)
			continue
		}
		newest := ""
		for _, m := range ms {
			if m.Anchor != "" {
				newest = m.Anchor
				break
			}
		}
		if newest == "" {
			newest = today.Format("2006-01-02")
		}
		stale := false
		if d, err := time.Parse("2006-01-02", newest); err == nil {
			stale = today.Sub(d).Hours()/24 > staleAfter
		}
		pages := (len(ms) + splitAt - 1) / splitAt
		for page := 1; page <= pages; page++ {
			lo, hi := (page-1)*splitAt, page*splitAt
			if hi > len(ms) {
				hi = len(ms)
			}
			rel := mocRel(t, page)
			p := filepath.Join(root, filepath.FromSlash(rel))
			var before []byte
			created := ""
			if cur, err := os.ReadFile(p); err == nil {
				before = cur
				fm, _ := ParseFrontmatter(string(cur))
				created = strings.TrimSpace(fm["created"])
			}
			text := RenderMoc(t, ms[lo:hi], len(ms), page, pages, created, newest, stale, staleAfter)
			item := MocPage{Type: t, Rel: rel, Members: hi - lo, Page: page, Pages: pages, Stale: stale, Newest: newest}
			if before != nil && string(before) == text {
				plan.Pages = append(plan.Pages, item)
				continue
			}
			item.Changed = true
			plan.Pages = append(plan.Pages, item)
			plan.Intents = append(plan.Intents, Intent{Job: JobMocs, Rel: rel, Before: before, After: []byte(text),
				Summary: fmt.Sprintf("map of content for %s (page %d of %d, %d members) regenerated", t, page, pages, hi-lo)})
		}
	}
	return plan, nil
}
