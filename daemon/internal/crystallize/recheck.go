package crystallize

// Recheck asks again which of a lesson's stamped sources it rests on (#749).
//
// Until the prompt asked the model to name them, a lesson stamped every card in
// its cluster `consolidated_into`, and a cluster gathers notes that share a
// word. A card the lesson does not cover then ranked at x0.30 for nothing: the
// first run demoted "three specialist sub-agents" under "inbox is a landing
// strip", and nine gold questions lost their answer. For each lesson with
// stamped cards this asks the same strong tier which of those cards the lesson
// is true of, and plans the release of the rest: the stamp comes off the card,
// and the card's entries come off the lesson's `consolidated_from` and its
// "What taught it" list. It plans and writes nothing; the plan is a manifest
// the dreaming binary makes through its journal.

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

var consolidatedIntoLink = regexp.MustCompile(`(?m)^consolidated_into:[ \t]*"?\[\[([^\]|]+)`)

// RecheckAct is one in-place rewrite the recheck plans, in the dreaming
// binary's manifest shape.
type RecheckAct struct {
	Rel     string `json:"rel"`
	Before  string `json:"before_sha256"`
	After   string `json:"after"`
	Summary string `json:"summary,omitempty"`
}

// RecheckLesson is what the recheck found for one lesson.
type RecheckLesson struct {
	Lesson   string   `json:"lesson"`
	Kept     []string `json:"kept,omitempty"`
	Released []string `json:"released,omitempty"`
	Reason   string   `json:"reason,omitempty"`
	Error    string   `json:"error,omitempty"`
}

type stamped struct {
	rel   string // relative to the memory root
	stem  string
	title string
	text  string
	raw   []byte
}

func sha(b []byte) string {
	s := sha256.Sum256(b)
	return hex.EncodeToString(s[:])
}

// stampedCards is every card under the memory classes carrying a
// `consolidated_into`, by the lesson it names.
func stampedCards(root string) map[string][]stamped {
	out := map[string][]stamped{}
	memDir := filepath.Join(root, "memory")
	classes, _ := os.ReadDir(memDir)
	for _, cl := range classes {
		if !cl.IsDir() || cl.Name() == "crystallized" || strings.HasPrefix(cl.Name(), "_") {
			continue
		}
		files, _ := os.ReadDir(filepath.Join(memDir, cl.Name()))
		for _, f := range files {
			if f.IsDir() || !strings.HasSuffix(f.Name(), ".md") {
				continue
			}
			raw, err := os.ReadFile(filepath.Join(memDir, cl.Name(), f.Name()))
			if err != nil {
				continue
			}
			m := consolidatedIntoLink.FindSubmatch(raw)
			if m == nil {
				continue
			}
			text := strings.ReplaceAll(string(raw), "\r\n", "\n")
			fm := frontmatter(text)
			body := text
			if i := strings.Index(text[min(4, len(text)):], "\n---\n"); strings.HasPrefix(text, "---\n") && i >= 0 {
				body = text[4+i+5:]
			}
			out[string(m[1])] = append(out[string(m[1])], stamped{
				rel: path.Join("memory", cl.Name(), f.Name()), stem: strings.TrimSuffix(f.Name(), ".md"),
				title: fm["title"], text: strings.TrimSpace(body), raw: raw})
		}
	}
	for k := range out {
		sort.Slice(out[k], func(i, j int) bool { return out[k][i].rel < out[k][j].rel })
	}
	return out
}

// RecheckPrompt is the question for one lesson and the cards it stamped.
func RecheckPrompt(lessonTitle, lessonBody string, cards []stamped) string {
	var b strings.Builder
	fmt.Fprintf(&b, "A lesson was written from several notes that share a word:\n\n## %s\n\n%s\n\n",
		lessonTitle, strings.TrimSpace(lessonBody))
	b.WriteString("These notes were marked as taught by it, which ranks them below the lesson from then on.\n\n")
	for i, c := range cards {
		text := c.text
		if len(text) > 1500 {
			text = text[:1500] + " …"
		}
		fmt.Fprintf(&b, "--- note %d (%q)\n%s\n\n", i+1, c.title, text)
	}
	b.WriteString(`Which of these notes is this lesson true of — a note whose substance the lesson
states or generalises, so that someone with the lesson would not need the note to
learn it? A note that only shares a word or a topic with the lesson is not one.
Answer with one JSON object and nothing else:

{"sources": [<the numbers of the notes the lesson is true of>],
 "reason": "<one sentence about the notes you left out, or why none were>"}`)
	return b.String()
}

// RecheckAnswer is the model's reply.
type RecheckAnswer struct {
	Sources []int  `json:"sources"`
	Reason  string `json:"reason"`
}

// ParseRecheck reads the answer, tolerating a fence.
func ParseRecheck(out string) (RecheckAnswer, error) {
	start, end := strings.Index(out, "{"), strings.LastIndex(out, "}")
	if start < 0 || end <= start {
		return RecheckAnswer{}, fmt.Errorf("no JSON object in the answer: %q", truncate(out, 200))
	}
	var a RecheckAnswer
	if err := json.Unmarshal([]byte(out[start:end+1]), &a); err != nil {
		return RecheckAnswer{}, err
	}
	if a.Sources == nil {
		return RecheckAnswer{}, fmt.Errorf("the answer named no \"sources\" list")
	}
	return a, nil
}

var consolidatedIntoLine = regexp.MustCompile(`(?m)^consolidated_into:[ \t]*.*\r?\n`)

// unstamp is the card without its `consolidated_into` line; every other byte
// stays where it was.
func unstamp(raw []byte) string {
	return consolidatedIntoLine.ReplaceAllString(string(raw), "")
}

// withoutSources is the lesson's text without the list entries that link to
// any of the released stems, in `consolidated_from` and "What taught it".
func withoutSources(lesson string, released []string) string {
	drop := func(line string) bool {
		t := strings.TrimSpace(line)
		if !strings.HasPrefix(t, "- ") {
			return false
		}
		for _, stem := range released {
			if strings.Contains(t, "[["+stem+"]]") || strings.Contains(t, "[["+stem+"|") ||
				strings.Contains(t, "/"+stem+"]]") || strings.Contains(t, "/"+stem+"|") {
				return true
			}
		}
		return false
	}
	var out []string
	for _, l := range strings.Split(lesson, "\n") {
		if !drop(l) {
			out = append(out, l)
		}
	}
	return strings.Join(out, "\n")
}

// PlanRecheck asks about every lesson with stamped cards, at most cap of them
// (0: all), and returns what it found and the rewrites that release the cards
// a lesson is not true of. It writes nothing.
func PlanRecheck(root string, call Caller, cap int) ([]RecheckLesson, []RecheckAct, error) {
	byLesson := stampedCards(root)
	stems := make([]string, 0, len(byLesson))
	for s := range byLesson {
		stems = append(stems, s)
	}
	sort.Strings(stems)
	var found []RecheckLesson
	var acts []RecheckAct
	for _, stem := range stems {
		if cap > 0 && len(found) >= cap {
			break
		}
		cards := byLesson[stem]
		lessonRel := path.Join(Dir, stem+".md")
		r := RecheckLesson{Lesson: lessonRel}
		raw, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(lessonRel)))
		if err != nil {
			// A stamp naming a lesson that is gone points at nothing: release it.
			r.Reason = "the lesson it names does not exist"
			for _, c := range cards {
				r.Released = append(r.Released, c.rel)
				acts = append(acts, RecheckAct{Rel: c.rel, Before: sha(c.raw), After: unstamp(c.raw),
					Summary: "released: its lesson " + stem + " does not exist"})
			}
			found = append(found, r)
			continue
		}
		lesson := strings.ReplaceAll(string(raw), "\r\n", "\n")
		fm := frontmatter(lesson)
		body := lesson
		if i := strings.Index(lesson[min(4, len(lesson)):], "\n---\n"); i >= 0 {
			body = lesson[4+i+5:]
		}
		body = strings.SplitN(body, "\n## What taught it", 2)[0]
		out, err := call(RecheckPrompt(fm["title"], body, cards))
		if err != nil {
			r.Error = err.Error()
			found = append(found, r)
			continue
		}
		ans, err := ParseRecheck(out)
		if err != nil {
			r.Error = err.Error()
			found = append(found, r)
			continue
		}
		r.Reason = ans.Reason
		keep := map[int]bool{}
		for _, n := range ans.Sources {
			if n >= 1 && n <= len(cards) {
				keep[n] = true
			}
		}
		var releasedStems []string
		for i, c := range cards {
			if keep[i+1] {
				r.Kept = append(r.Kept, c.rel)
				continue
			}
			r.Released = append(r.Released, c.rel)
			releasedStems = append(releasedStems, c.stem)
			acts = append(acts, RecheckAct{Rel: c.rel, Before: sha(c.raw), After: unstamp(c.raw),
				Summary: "released from " + stem + ": the lesson is not true of it"})
		}
		if len(releasedStems) > 0 {
			acts = append(acts, RecheckAct{Rel: lessonRel, Before: sha(raw),
				After:   withoutSources(lesson, releasedStems),
				Summary: fmt.Sprintf("%d source(s) it is not true of taken off what taught it", len(releasedStems))})
		}
		found = append(found, r)
	}
	return found, acts, nil
}
