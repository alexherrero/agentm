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

// Job "mocs" — the maps of content under `memory/mocs/`, each named for what
// it maps. A memory type has its own page, `<type>.md`, once it has
// `moc_min_members` notes (five); a page past `moc_split_at` (forty) paginates
// inside itself, a section per forty members, and never into a second file.
// `moc-memory.md` maps every type: a type with a page by its link, a type below
// the floor with its notes listed in full, so every typed note is two links
// from the root map. A page is flagged stale when its newest member is older
// than `moc_stale_after_days` (ninety).
//
// A page carries its newest member's date as `updated`, so a regeneration on
// an unchanged membership is byte-identical and writes nothing; `created`
// survives regeneration. A page whose type has since fallen below the floor
// is left alone and reported — nothing here deletes.

const (
	JobMocs                  = "mocs"
	DefaultMocMinMembers     = 5
	DefaultMocSplitAt        = 40
	DefaultMocStaleAfterDays = 90
	MocRootSlug              = "moc-root"
	MocMemorySlug            = "moc-memory"
	NeedsReviewSlug          = "needs-review"
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

// MocPage is one generated page. Sections is how many in-page sections a
// type's page holds: one until it passes `moc_split_at` members.
type MocPage struct {
	Type     string `json:"type,omitempty"`
	Rel      string `json:"rel"`
	Members  int    `json:"members"`
	Sections int    `json:"sections,omitempty"`
	Stale    bool   `json:"stale"`
	Newest   string `json:"newest,omitempty"`
	Changed  bool   `json:"changed"`
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

// MocRel is where a map in the maps' class directory lives, memory-root
// relative.
func MocRel(slug string) string {
	return "memory/" + mocClass + "/" + slug + ".md"
}

func mocPlural(n int, one, many string) string {
	if n == 1 {
		return one
	}
	return many
}

func mocMemberLine(m Member) string {
	line := fmt.Sprintf("- [[%s]] — %s", m.Slug, m.Title)
	if m.Phrase != "" {
		line += " · " + m.Phrase
	}
	return line
}

func mocJoin(lines []string) string {
	return strings.TrimRight(strings.Join(lines, "\n"), "\n") + "\n"
}

// RenderMoc renders one type's page. `created` is the page's own, preserved.
// Past `splitAt` members the page paginates inside itself: a section per
// `splitAt` members, headed by the range it holds.
func RenderMoc(t string, members []Member, created, newest string, stale bool, staleDays float64, splitAt int) string {
	if created == "" {
		created = newest
	}
	total := len(members)
	lines := []string{"---", "title: " + t + " — map of content", "kind: moc", "status: active",
		"created: " + created, "updated: " + newest, "tags: [moc, " + t + "]", "slug: " + t,
		"type_of_members: " + t, fmt.Sprintf("members: %d", total)}
	if stale {
		lines = append(lines, "stale: true")
	}
	lines = append(lines, "generated_by: "+mocGeneratedBy, "---", "", "# "+t, "",
		"[["+MocRootSlug+"]] · [["+MocMemorySlug+"]]", "",
		fmt.Sprintf("%d %s typed `%s`, newest first by their captured or created date. Generated from the notes' own frontmatter; not edited by hand.",
			total, mocPlural(total, "note", "notes"), t))
	if stale {
		lines = append(lines, "", fmt.Sprintf("Stale: the newest member is from %s, more than %.0f days ago.", newest, staleDays))
	}
	if splitAt <= 0 || total <= splitAt {
		lines = append(lines, "", "## Members", "")
		for _, m := range members {
			lines = append(lines, mocMemberLine(m))
		}
	} else {
		for lo := 0; lo < total; lo += splitAt {
			hi := lo + splitAt
			if hi > total {
				hi = total
			}
			lines = append(lines, "", fmt.Sprintf("## Members %d–%d", lo+1, hi), "")
			for _, m := range members[lo:hi] {
				lines = append(lines, mocMemberLine(m))
			}
		}
	}
	return mocJoin(append(lines, ""))
}

// RenderMemoryMap renders `moc-memory.md` over every type, in sorted order: a
// type at or past the floor by its page's link, a type below it with its
// notes listed in full.
func RenderMemoryMap(types []string, byType map[string][]Member, minMembers int, created, updated string) string {
	if created == "" {
		created = updated
	}
	total := 0
	for _, t := range types {
		total += len(byType[t])
	}
	lines := []string{"---", "title: memory — map of content", "kind: moc", "status: active",
		"created: " + created, "updated: " + updated, "tags: [moc, memory]", "slug: " + MocMemorySlug,
		fmt.Sprintf("members: %d", total), "generated_by: " + mocGeneratedBy, "---", "", "# memory", "",
		"[[" + MocRootSlug + "]]", ""}
	lines = append(lines,
		fmt.Sprintf("%d %s across %d %s, counted from the notes' own frontmatter. A type with at least %d notes has its own map; a smaller one is listed here in full. Generated; not edited by hand.",
			total, mocPlural(total, "note", "notes"), len(types), mocPlural(len(types), "type", "types"), minMembers),
		"", "## Types", "")
	var below []string
	for _, t := range types {
		n := len(byType[t])
		if n >= minMembers {
			lines = append(lines, fmt.Sprintf("- [[%s]] — %d %s", t, n, mocPlural(n, "note", "notes")))
			continue
		}
		below = append(below, t)
		lines = append(lines, fmt.Sprintf("- %s — %d %s, listed below", t, n, mocPlural(n, "note", "notes")))
	}
	for _, t := range below {
		lines = append(lines, "", "## "+t, "")
		for _, m := range byType[t] {
			lines = append(lines, mocMemberLine(m))
		}
	}
	return mocJoin(append(lines, ""))
}

// PlanMocs decides the pages: one per type at or past the floor, and
// `moc-memory.md` over them all. It writes nothing.
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
	newestAll, total := "", 0
	for _, t := range types {
		ms := byType[t]
		total += len(ms)
		// Members are newest first with the undated last, so the first
		// member's anchor is the type's newest date when it has one.
		if len(ms) > 0 && ms[0].Anchor > newestAll {
			newestAll = ms[0].Anchor
		}
		if len(ms) < minMembers {
			plan.BelowFloor[t] = len(ms)
			continue
		}
		newest := ms[0].Anchor
		if newest == "" {
			newest = today.Format("2006-01-02")
		}
		stale := false
		if d, err := time.Parse("2006-01-02", newest); err == nil {
			stale = today.Sub(d).Hours()/24 > staleAfter
		}
		sections := 1
		if splitAt > 0 {
			sections = (len(ms) + splitAt - 1) / splitAt
		}
		rel := MocRel(t)
		before, created := mocCurrentPage(root, rel)
		text := RenderMoc(t, ms, created, newest, stale, staleAfter, splitAt)
		plan.add(MocPage{Type: t, Rel: rel, Members: len(ms), Sections: sections, Stale: stale, Newest: newest}, before, text,
			fmt.Sprintf("map of content for %s (%d members) regenerated", t, len(ms)))
	}
	if total == 0 {
		// Nothing typed to map. A map of nothing is not written, and one a
		// fuller corpus left behind stays as it is.
		return plan, nil
	}
	if newestAll == "" {
		newestAll = today.Format("2006-01-02")
	}
	rel := MocRel(MocMemorySlug)
	before, created := mocCurrentPage(root, rel)
	text := RenderMemoryMap(types, byType, minMembers, created, newestAll)
	plan.add(MocPage{Rel: rel, Members: total, Newest: newestAll}, before, text, "the memory map regenerated")
	return plan, nil
}

// mocCurrentPage is a page's bytes on disk and its own `created`, or nil and
// "" when it does not exist yet.
func mocCurrentPage(root, rel string) ([]byte, string) {
	cur, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
	if err != nil {
		return nil, ""
	}
	fm, _ := ParseFrontmatter(string(cur))
	return cur, strings.TrimSpace(fm["created"])
}

// add records a page, and an intent when its text differs from the file.
func (plan *MocsPlan) add(item MocPage, before []byte, text, summary string) {
	if before != nil && string(before) == text {
		plan.Pages = append(plan.Pages, item)
		return
	}
	item.Changed = true
	plan.Pages = append(plan.Pages, item)
	plan.Intents = append(plan.Intents, Intent{Job: JobMocs, Rel: item.Rel, Before: before, After: []byte(text), Summary: summary})
}
