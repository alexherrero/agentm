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
// and the lesson names the card in its `released:` list, keeping it in
// `consolidated_from` and "What taught it" as provenance (task 182). It plans and writes nothing; the plan is a manifest
// the dreaming binary makes through its journal.

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"

	"github.com/alexherrero/agentm/daemon/internal/cardshape"
	"github.com/alexherrero/agentm/daemon/internal/fmlist"
)

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
			text := strings.ReplaceAll(string(raw), "\r\n", "\n")
			lessons := StampedLessons(text)
			if len(lessons) == 0 {
				continue
			}
			fm := frontmatter(text)
			body := text
			if i := strings.Index(text[min(4, len(text)):], "\n---\n"); strings.HasPrefix(text, "---\n") && i >= 0 {
				body = text[4+i+5:]
			}
			// A card several lessons rest on is asked about under each of them.
			for _, lesson := range lessons {
				out[lesson] = append(out[lesson], stamped{
					rel: path.Join("memory", cl.Name(), f.Name()), stem: strings.TrimSuffix(f.Name(), ".md"),
					title: fm["title"], text: strings.TrimSpace(body), raw: raw})
			}
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

// unstampFrom is the card with these lessons taken out of its
// `consolidated_into`, and the key gone when none is left; every other byte
// stays where it was. A card several lessons rest on keeps the rest.
func unstampFrom(raw []byte, released []string) string {
	var keep []string
	for _, l := range StampedLessons(string(raw)) {
		if !containsString(released, l) {
			keep = append(keep, l)
		}
	}
	if len(keep) == 0 {
		out, _ := fmlist.Remove(string(raw), "consolidated_into")
		return out
	}
	out, _ := fmlist.Replace(string(raw), "consolidated_into", stampLine(keep))
	return out
}

// ReleasedStems is the stems a lesson's `released:` list names: the cards
// that taught it, are still listed in `consolidated_from` as its provenance,
// and carry no stamp because a recheck found the lesson is not true of them
// (the operator's ruling of 2026-10-01, task 182). Frontmatter only.
func ReleasedStems(lesson string) []string {
	return linkStems(fmlist.Items(lesson, "released"))
}

// withReleased is the lesson with these stems added to its `released:` list.
// Its `consolidated_from` and "What taught it" stay as they are: a card the
// lesson is not true of still taught it, and a lesson whose list emptied would
// be a lesson with no provenance at all (ci-green-closes-work, released by every
// one of its five cards on 2026-10-01).
func withReleased(lesson string, stems []string) string {
	all := ReleasedStems(lesson)
	for _, s := range stems {
		if !containsString(all, s) {
			all = append(all, s)
		}
	}
	links := make([]string, len(all))
	for i, s := range all {
		links[i] = fmt.Sprintf("\"[[%s]]\"", s)
	}
	line := "released: [" + strings.Join(links, ", ") + "]"
	if out, ok := fmlist.Replace(lesson, "released", line); ok {
		return out
	}
	if !strings.HasPrefix(lesson, "---\n") {
		return lesson
	}
	end := strings.Index(lesson[4:], "\n---")
	if end < 0 {
		return lesson
	}
	end += 4
	return cardshape.Reorder(lesson[:end] + "\n" + line + lesson[end:])
}

// PlanRecheck asks about every lesson with stamped cards, at most cap of them
// (0: all), and returns what it found and the rewrites that release the cards
// a lesson is not true of. It writes nothing.
func PlanRecheck(root string, call Caller, cap int) ([]RecheckLesson, []RecheckAct, error) {
	return PlanRecheckOnly(root, call, cap, nil)
}

// PlanRecheckOnly is PlanRecheck over the named lessons alone (by stem), or
// over every lesson when `only` is empty. Restamp names the lessons whose cards
// it stamped again; those cards were never asked about, and asking every other
// lesson a second time would spend a call each for answers already on file.
func PlanRecheckOnly(root string, call Caller, cap int, only []string) ([]RecheckLesson, []RecheckAct, error) {
	byLesson := stampedCards(root)
	want := map[string]bool{}
	for _, s := range only {
		want[s] = true
	}
	stems := make([]string, 0, len(byLesson))
	for s := range byLesson {
		if len(want) > 0 && !want[s] {
			continue
		}
		stems = append(stems, s)
	}
	sort.Strings(stems)
	var found []RecheckLesson
	var lessonActs []RecheckAct
	// A card several lessons rest on can be released by more than one of them
	// in a run. Its releases are gathered and made as one act, because a
	// manifest refuses a second act on a note whose bytes the first changed.
	type release struct {
		raw     []byte
		lessons []string
		why     []string
	}
	releases := map[string]*release{}
	releaseCard := func(c stamped, lesson, why string) {
		r := releases[c.rel]
		if r == nil {
			r = &release{raw: c.raw}
			releases[c.rel] = r
		}
		r.lessons = append(r.lessons, lesson)
		r.why = append(r.why, why)
	}
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
				releaseCard(c, stem, "released: its lesson "+stem+" does not exist")
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
			releaseCard(c, stem, "released from "+stem+": the lesson is not true of it")
		}
		if len(releasedStems) > 0 {
			lessonActs = append(lessonActs, RecheckAct{Rel: lessonRel, Before: sha(raw),
				After:   withReleased(lesson, releasedStems),
				Summary: fmt.Sprintf("%d source(s) it is not true of named in released", len(releasedStems))})
		}
		found = append(found, r)
	}
	rels := make([]string, 0, len(releases))
	for rel := range releases {
		rels = append(rels, rel)
	}
	sort.Strings(rels)
	acts := make([]RecheckAct, 0, len(rels)+len(lessonActs))
	for _, rel := range rels {
		r := releases[rel]
		acts = append(acts, RecheckAct{Rel: rel, Before: sha(r.raw), After: unstampFrom(r.raw, r.lessons),
			Summary: strings.Join(r.why, "; ")})
	}
	return found, append(acts, lessonActs...), nil
}
