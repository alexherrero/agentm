package index

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// A card a lesson rests on is demoted beside the lesson and nowhere else (task
// 182 step 3, the operator's ruling of 2026-10-01). The demotion exists so the
// things that taught a lesson cannot crowd it out of recall; where the lesson
// does not answer the query, the ×0.30 only buried the one note that did. The
// gold set's rc11 card sat at rank 55 under a lesson that was never returned.

func indexOnDisk(t *testing.T, idx *Index, rel, raw string) {
	t.Helper()
	p := filepath.Join(idx.vault, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(raw), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := idx.Upsert(note.Parse(rel, raw, time.Now()), time.Now().UnixNano(), int64(len(raw))); err != nil {
		t.Fatalf("indexing %s: %v", rel, err)
	}
}

func consolidatedFixture(t *testing.T, stamp string) *Index {
	t.Helper()
	idx := openScratch(t)
	// Filler, so the query terms are rare and BM25 scores them above zero; in a
	// three-note corpus every term is common and every score collapses to 0.
	for _, f := range []string{"release-gate", "worktree-slots", "drive-sync", "budget-line",
		"canary-probe", "morning-note", "tracker-schema", "entity-pages"} {
		indexOnDisk(t, idx, "agent/memory/semantic/filler-"+f+".md",
			"---\ntitle: "+f+"\ntype: reference\nstatus: active\n---\n\nNotes about the "+f+" and nothing else.\n")
	}
	indexOnDisk(t, idx, "agent/memory/crystallized/answers-cite.md",
		"---\ntitle: Answers cite their sources\nkind: crystallized\nlifecycle: pinned\n---\n\n"+
			"A memory system should cite the memory ids behind each answer.\n")
	card := "An agent cites the memory ids behind each answer, and its aliases were tested twelve times.\n"
	indexOnDisk(t, idx, "agent/memory/semantic/a-stamped-card.md",
		"---\ntitle: Stamped card\ntype: reference\nstatus: active\n"+stamp+"---\n\n"+card)
	return idx
}

func rowFor(rows []Result, suffix string) (Result, int) {
	for i, r := range rows {
		if strings.HasSuffix(r.Path, suffix) {
			return r, i
		}
	}
	return Result{}, -1
}

func TestAStampedCardIsDemotedBesideItsLesson(t *testing.T) {
	for _, stamp := range []string{
		"consolidated_into: \"[[answers-cite]]\"\n",
		"consolidated_into: [\"[[another-lesson]]\", \"[[answers-cite]]\"]\n",
	} {
		idx := consolidatedFixture(t, stamp)
		for _, mode := range []string{ModeAnd, ModeFusion} {
			out, err := idx.Search(Query{Text: "memory ids answer", K: 5, Mode: mode})
			if err != nil {
				t.Fatalf("%s: %v", mode, err)
			}
			card, ci := rowFor(out.Results, "a-stamped-card.md")
			if _, li := rowFor(out.Results, "answers-cite.md"); li < 0 || ci < 0 {
				t.Fatalf("%s: the card and its lesson should both match: %v", mode, resultPaths(out.Results))
			}
			if want := card.RawScore * note.Weights[note.ClassConsolidated]; card.Score != want {
				t.Errorf("%s (%s): with its lesson in the list the card takes the x0.30: score %.4f, raw %.4f, want %.4f",
					mode, stamp, card.Score, card.RawScore, want)
			}
			if !strings.Contains(card.Penalty, note.ClassConsolidated) {
				t.Errorf("%s: the class stays visible on the row: %q", mode, card.Penalty)
			}
		}
	}
}

func TestAStampedCardKeepsItsRankWhenItsLessonIsNotInTheList(t *testing.T) {
	idx := consolidatedFixture(t, "consolidated_into: \"[[answers-cite]]\"\n")
	for _, mode := range []string{ModeAnd, ModeFusion} {
		// "aliases tested twelve" is in the card, never in the lesson.
		out, err := idx.Search(Query{Text: "aliases tested twelve", K: 5, Mode: mode})
		if err != nil {
			t.Fatalf("%s: %v", mode, err)
		}
		if _, li := rowFor(out.Results, "answers-cite.md"); li >= 0 {
			t.Fatalf("%s: the lesson must not match this query: %v", mode, resultPaths(out.Results))
		}
		card, ci := rowFor(out.Results, "a-stamped-card.md")
		if ci < 0 {
			t.Fatalf("%s: the card should match: %v", mode, resultPaths(out.Results))
		}
		if card.Score != card.RawScore {
			t.Errorf("%s: without its lesson in the list the card takes no demotion: score %.4f, raw %.4f",
				mode, card.Score, card.RawScore)
		}
	}
}

// The rule on the shared ranking function, which the dense arm calls too. A
// nil reader keeps the old unconditional demotion for callers with no vault.
func TestTheLessonRuleOnTheRankingFunction(t *testing.T) {
	rows := func() []Result {
		return []Result{
			{Path: "agent/memory/semantic/card.md", Score: 10, Penalty: note.ClassConsolidated},
			{Path: "agent/memory/semantic/other.md", Score: 9},
		}
	}
	lessons := func(string) []string { return []string{"the-lesson"} }

	got := penalizeRankAndDecay(rows(), 5, nil, time.Time{}, false, "", lessons)
	if r, _ := rowFor(got, "card.md"); r.Score != 10 {
		t.Errorf("lesson absent: want the card unpenalized at 10, got %.2f", r.Score)
	}
	withLesson := append(rows(), Result{Path: "agent/memory/crystallized/the-lesson.md", Score: 1})
	got = penalizeRankAndDecay(withLesson, 5, nil, time.Time{}, false, "", lessons)
	if r, _ := rowFor(got, "card.md"); r.Score != 10*note.Weights[note.ClassConsolidated] {
		t.Errorf("lesson present: want 10 x %.2f, got %.2f", note.Weights[note.ClassConsolidated], r.Score)
	}
	// A note named like the lesson outside crystallized/ is not the lesson.
	decoy := append(rows(), Result{Path: "agent/memory/semantic/the-lesson.md", Score: 1})
	got = penalizeRankAndDecay(decoy, 5, nil, time.Time{}, false, "", lessons)
	if r, _ := rowFor(got, "card.md"); r.Score != 10 {
		t.Errorf("a same-named note outside crystallized/ is not the lesson; got %.2f", r.Score)
	}
	got = penalizeRankAndDecay(rows(), 5, nil, time.Time{}, false, "", nil)
	if r, _ := rowFor(got, "card.md"); r.Score != 10*note.Weights[note.ClassConsolidated] {
		t.Errorf("no reader: the old unconditional demotion, got %.2f", r.Score)
	}
}
