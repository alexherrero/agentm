package crystallize

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeRecheckNote(t *testing.T, root, rel, text string) {
	t.Helper()
	p := filepath.Join(root, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

const recheckLesson = "---\ntitle: Answers that cite the memories they came from\nkind: crystallized\n" +
	"status: active\nlifecycle: pinned\nconsolidated_from:\n  - \"[[a-cites-its-sources]]\"\n" +
	"  - \"[[b-three-sub-agents]]\"\n  - \"[[c-answers-name-ids]]\"\n  - \"[[some-trace]]\"\nslug: answers-cite\n---\n\n" +
	"An answer should name the notes it rests on.\n\n## What taught it\n\n" +
	"- [[a-cites-its-sources]] — card, 2026-08-11\n- [[b-three-sub-agents]] — card, 2026-08-11\n" +
	"- [[c-answers-name-ids]] — card, 2026-09-11\n- [[some-trace]] — trace, 2026-09-12\n"

func recheckCard(title, lesson string) string {
	return "---\ntitle: " + title + "\ntype: reference\nstatus: active\nlifecycle: active\n" +
		"consolidated_into: \"[[" + lesson + "]]\"\nslug: x\n---\n\n" + title + ".\n"
}

func TestRecheckReleasesTheCardsALessonIsNotTrueOf(t *testing.T) {
	root := t.TempDir()
	writeRecheckNote(t, root, "memory/crystallized/answers-cite.md", recheckLesson)
	writeRecheckNote(t, root, "memory/semantic/a-cites-its-sources.md", recheckCard("Its query agent cites the memory ids", "answers-cite"))
	writeRecheckNote(t, root, "memory/semantic/b-three-sub-agents.md", recheckCard("Three specialist sub-agents", "answers-cite"))
	writeRecheckNote(t, root, "memory/semantic/c-answers-name-ids.md", recheckCard("Answers name their ids", "answers-cite"))
	writeRecheckNote(t, root, "memory/semantic/orphan.md", recheckCard("An orphan", "a-lesson-that-is-gone"))
	writeRecheckNote(t, root, "memory/semantic/plain.md", "---\ntitle: plain\ntype: fix\n---\n\nNo stamp.\n")

	var prompts []string
	found, acts, err := PlanRecheck(root, func(p string) (string, error) {
		prompts = append(prompts, p)
		return "```json\n{\"sources\": [1, 3], \"reason\": \"note 2 is about how the work is split, not citing\"}\n```", nil
	}, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(prompts) != 1 || !strings.Contains(prompts[0], "--- note 2 (\"Three specialist sub-agents\")") {
		t.Fatalf("one call for the one lesson that exists, with its cards numbered: %q", prompts)
	}
	byLesson := map[string]RecheckLesson{}
	for _, f := range found {
		byLesson[f.Lesson] = f
	}
	got := byLesson["memory/crystallized/answers-cite.md"]
	if strings.Join(got.Released, ",") != "memory/semantic/b-three-sub-agents.md" || len(got.Kept) != 2 {
		t.Errorf("answers-cite: kept %v, released %v", got.Kept, got.Released)
	}
	if orphan := byLesson["memory/crystallized/a-lesson-that-is-gone.md"]; len(orphan.Released) != 1 {
		t.Errorf("a stamp naming a lesson that is gone is released: %+v", orphan)
	}
	after := map[string]string{}
	for _, a := range acts {
		after[a.Rel] = a.After
	}
	if len(acts) != 3 {
		t.Fatalf("acts %v, want the released card, the orphan and the lesson", len(acts))
	}
	card := after["memory/semantic/b-three-sub-agents.md"]
	if strings.Contains(card, "consolidated_into") || !strings.Contains(card, "title: Three specialist sub-agents\n") ||
		card != strings.Replace(recheckCard("Three specialist sub-agents", "answers-cite"),
			"consolidated_into: \"[[answers-cite]]\"\n", "", 1) {
		t.Errorf("the released card changes only by its stamp:\n%s", card)
	}
	lesson := after["memory/crystallized/answers-cite.md"]
	if strings.Contains(lesson, "b-three-sub-agents") {
		t.Errorf("the lesson still names the released card:\n%s", lesson)
	}
	for _, keep := range []string{"[[a-cites-its-sources]]\"", "[[c-answers-name-ids]]\"", "[[some-trace]]\"",
		"- [[a-cites-its-sources]] — card", "- [[some-trace]] — trace", "An answer should name the notes it rests on."} {
		if !strings.Contains(lesson, keep) {
			t.Errorf("the lesson lost %q:\n%s", keep, lesson)
		}
	}
	if _, has := after["memory/semantic/a-cites-its-sources.md"]; has {
		t.Error("a card the lesson is true of was rewritten")
	}
}

func TestRecheckWritesNothingForAnAnswerItCannotRead(t *testing.T) {
	root := t.TempDir()
	writeRecheckNote(t, root, "memory/crystallized/answers-cite.md", recheckLesson)
	writeRecheckNote(t, root, "memory/semantic/a-cites-its-sources.md", recheckCard("A", "answers-cite"))
	for _, answer := range []string{"I think note 1.", `{"reason": "no list"}`} {
		found, acts, err := PlanRecheck(root, func(string) (string, error) { return answer, nil }, 0)
		if err != nil {
			t.Fatal(err)
		}
		if len(acts) != 0 || len(found) != 1 || found[0].Error == "" {
			t.Errorf("%q: found %+v, acts %d", answer, found, len(acts))
		}
	}
}
