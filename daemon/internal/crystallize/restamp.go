package crystallize

// Restamp puts back the stamps a rewrite dropped (task 182).
//
// A lesson lists every card it rests on in `consolidated_from`, and each of
// those cards carries `consolidated_into` naming the lesson. Until task 182,
// nightly enrichment did not carry `consolidated_into` through a rewrite, so a
// card that was re-judged lost its stamp while its lesson still listed it. By
// 2026-10-01 that was 111 links across 37 cards: each of those cards ranked at
// full weight beside the lesson it taught, the opposite of the design.
//
// The lesson's list is the record of what the lesson rests on — the recheck
// (#749) keeps it in step, taking a released card off the list as it takes the
// stamp off the card — so this plans, for each card a lesson lists, the lessons
// the card no longer names, and nothing else. A card several lessons rest on
// names them all (the operator's ruling of 2026-10-01). It never takes a lesson
// off a card. It plans and writes nothing; the plan is a manifest the dreaming
// binary makes through its journal, the same as the recheck's.
//
// The recheck asked only about cards that still carried a stamp, so a card
// unstamped before it ran was never asked. Restamp names the lessons it
// touched, and the recheck is meant to run over exactly those next.

import (
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

// RestampFinding is one listed source and what the plan does about it.
type RestampFinding struct {
	Lesson string `json:"lesson"`
	Card   string `json:"card"`
	// State is `restamp` (the card's act adds this lesson), `stamped` (the
	// card already names it), or `not-a-card` (a trace, a tracker, a record or
	// a note that is gone: crystallize never stamps those).
	State string `json:"state"`
	Note  string `json:"note,omitempty"`
}

var wikiTarget = regexp.MustCompile(`\[\[([^\]|]+)`)

// listedStems is the stems a lesson's `consolidated_from` links to, in order.
func listedStems(lesson string) []string {
	var out []string
	for _, m := range wikiTarget.FindAllStringSubmatch(frontmatter(lesson)["consolidated_from"], -1) {
		target := strings.TrimSuffix(strings.TrimSpace(m[1]), ".md")
		out = append(out, path.Base(target))
	}
	return out
}

// cardPath is where a stem lives among the memory classes, relative to the
// memory root, or "" when no class holds it.
func cardPath(root, stem string) string {
	for _, class := range classDirs {
		rel := path.Join("memory", class, stem+".md")
		if _, err := os.Stat(filepath.Join(root, filepath.FromSlash(rel))); err == nil {
			return rel
		}
	}
	return ""
}

// PlanRestamp reads every lesson under the memory root and plans a stamp for
// each card it lists that no longer names it. It returns the findings, the
// acts, and the lessons the acts touch, sorted.
func PlanRestamp(root string) ([]RestampFinding, []RecheckAct, []string, error) {
	entries, err := os.ReadDir(filepath.Join(root, filepath.FromSlash(Dir)))
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil, nil, nil
		}
		return nil, nil, nil, err
	}
	type listing struct{ lesson, stem string }
	var listings []listing
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".md") {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(Dir), e.Name()))
		if err != nil {
			continue
		}
		lesson := strings.TrimSuffix(e.Name(), ".md")
		for _, stem := range listedStems(strings.ReplaceAll(string(raw), "\r\n", "\n")) {
			listings = append(listings, listing{lesson, stem})
		}
	}
	sort.Slice(listings, func(i, j int) bool {
		if listings[i].lesson != listings[j].lesson {
			return listings[i].lesson < listings[j].lesson
		}
		return listings[i].stem < listings[j].stem
	})

	var found []RestampFinding
	type card struct {
		rel     string
		raw     []byte
		missing []string
	}
	cards := map[string]*card{}
	touched := map[string]bool{}
	for _, l := range listings {
		f := RestampFinding{Lesson: path.Join(Dir, l.lesson+".md"), Card: l.stem}
		rel := cardPath(root, l.stem)
		if rel == "" {
			f.State, f.Note = "not-a-card", "no memory card by that name"
			found = append(found, f)
			continue
		}
		f.Card = rel
		raw, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
		if err != nil {
			f.State, f.Note = "not-a-card", err.Error()
			found = append(found, f)
			continue
		}
		text := string(raw)
		if k := unquote(frontmatter(text)["kind"]); k != "" {
			f.State, f.Note = "not-a-card", "a "+k+" record; crystallize stamps cards only"
			found = append(found, f)
			continue
		}
		if !strings.HasPrefix(strings.ReplaceAll(text, "\r\n", "\n"), "---\n") {
			f.State, f.Note = "not-a-card", "no frontmatter to stamp"
			found = append(found, f)
			continue
		}
		if containsString(StampedLessons(text), l.lesson) {
			f.State = "stamped"
			found = append(found, f)
			continue
		}
		c := cards[rel]
		if c == nil {
			c = &card{rel: rel, raw: raw}
			cards[rel] = c
		}
		if !containsString(c.missing, l.lesson) {
			c.missing = append(c.missing, l.lesson)
		}
		f.State = "restamp"
		found = append(found, f)
		touched[l.lesson] = true
	}
	rels := make([]string, 0, len(cards))
	for rel := range cards {
		rels = append(rels, rel)
	}
	sort.Strings(rels)
	var acts []RecheckAct
	for _, rel := range rels {
		c := cards[rel]
		after := string(c.raw)
		for _, lesson := range c.missing {
			after = Stamp(after, lesson)
		}
		acts = append(acts, RecheckAct{Rel: rel, Before: sha(c.raw), After: after,
			Summary: "re-stamped for " + strings.Join(c.missing, ", ") + ": a rewrite had dropped consolidated_into"})
	}
	lessons := make([]string, 0, len(touched))
	for l := range touched {
		lessons = append(lessons, l)
	}
	sort.Strings(lessons)
	return found, acts, lessons, nil
}
