package crystallize

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A rewrite that dropped a card's stamp leaves the card at full rank beside the
// lessons it taught. Restamp plans, for every card a lesson still lists, the
// lessons the card no longer names, and nothing else: a card several lessons
// list names them all (the operator's ruling of 2026-10-01), a lesson the card
// already names is left alone, and a trace, a tracker or a missing name is
// never stamped. One act per card, however many lessons it gains, because a
// manifest refuses a second act on a note the first one changed.
func TestRestampPlansTheStampBackOnlyWhereALessonStillListsTheCard(t *testing.T) {
	root := t.TempDir()
	writeRecheckNote(t, root, "memory/crystallized/answers-cite.md", "---\ntitle: Answers cite\n"+
		"kind: crystallized\nconsolidated_from:\n  - \"[[a-lost-its-stamp]]\"\n  - \"[[b-still-stamped]]\"\n"+
		"  - \"[[c-names-another]]\"\n  - \"[[d-listed-twice]]\"\n  - \"[[some-trace]]\"\n"+
		"  - \"[[projects/agentm/tasks/157-x/tracker|157-x]]\"\n  - \"[[gone]]\"\n---\n\nLesson.\n")
	writeRecheckNote(t, root, "memory/crystallized/other-lesson.md", "---\ntitle: Other\n"+
		"kind: crystallized\nconsolidated_from:\n  - \"[[d-listed-twice]]\"\n  - \"[[c-names-another]]\"\n---\n\nOther.\n")
	lost := "---\ntitle: A lost its stamp\ntype: reference\nstatus: active\nproject: agentm\n" +
		"slug: a-lost-its-stamp\n---\n\nA.\n"
	writeRecheckNote(t, root, "memory/semantic/a-lost-its-stamp.md", lost)
	stampedB := recheckCard("B", "answers-cite")
	writeRecheckNote(t, root, "memory/semantic/b-still-stamped.md", stampedB)
	writeRecheckNote(t, root, "memory/semantic/c-names-another.md", recheckCard("C", "other-lesson"))
	writeRecheckNote(t, root, "memory/procedural/d-listed-twice.md",
		"---\ntitle: D\ntype: workflow\nstatus: active\n---\n\nD.\n")
	writeRecheckNote(t, root, "memory/episodic/some-trace.md",
		"---\nkind: session-trace\ntitle: A session\n---\n\nTrace.\n")

	found, acts, lessons, err := PlanRestamp(root)
	if err != nil {
		t.Fatal(err)
	}
	state := map[string]string{}
	for _, f := range found {
		state[f.Lesson+" <- "+f.Card] = f.State
	}
	want := map[string]string{
		"memory/crystallized/answers-cite.md <- memory/semantic/a-lost-its-stamp.md": "restamp",
		"memory/crystallized/answers-cite.md <- memory/semantic/b-still-stamped.md":  "stamped",
		"memory/crystallized/answers-cite.md <- memory/semantic/c-names-another.md":  "restamp",
		"memory/crystallized/other-lesson.md <- memory/semantic/c-names-another.md":  "stamped",
		"memory/crystallized/answers-cite.md <- memory/procedural/d-listed-twice.md": "restamp",
		"memory/crystallized/other-lesson.md <- memory/procedural/d-listed-twice.md": "restamp",
		"memory/crystallized/answers-cite.md <- memory/episodic/some-trace.md":       "not-a-card",
		"memory/crystallized/answers-cite.md <- tracker":                             "not-a-card",
		"memory/crystallized/answers-cite.md <- gone":                                "not-a-card",
	}
	for k, v := range want {
		if state[k] != v {
			t.Errorf("%s: state %q, want %q (all: %v)", k, state[k], v, state)
		}
	}
	after := map[string]string{}
	for _, a := range acts {
		if _, dup := after[a.Rel]; dup {
			t.Fatalf("two acts on %s; a manifest refuses the second", a.Rel)
		}
		after[a.Rel] = a.After
	}
	if len(acts) != 3 {
		t.Fatalf("want one act each for a, c and d; got %d: %+v", len(acts), acts)
	}
	for rel, line := range map[string]string{
		"memory/semantic/a-lost-its-stamp.md": `consolidated_into: "[[answers-cite]]"`,
		"memory/semantic/c-names-another.md":  `consolidated_into: ["[[other-lesson]]", "[[answers-cite]]"]`,
		"memory/procedural/d-listed-twice.md": `consolidated_into: ["[[answers-cite]]", "[[other-lesson]]"]`,
	} {
		if !strings.Contains(after[rel], "\n"+line+"\n") {
			t.Errorf("%s: want the line %s in:\n%s", rel, line, after[rel])
		}
	}
	for _, a := range acts {
		if a.Rel == "memory/semantic/a-lost-its-stamp.md" && a.Before != sha([]byte(lost)) {
			t.Fatalf("the act must name the bytes it read")
		}
	}
	if !strings.HasSuffix(after["memory/semantic/a-lost-its-stamp.md"], "\n\nA.\n") {
		t.Fatalf("the body is left alone")
	}
	if strings.Join(lessons, ",") != "answers-cite,other-lesson" {
		t.Fatalf("the lessons to recheck are the ones an act touched; got %v", lessons)
	}
	// Planning writes nothing.
	raw, _ := os.ReadFile(filepath.Join(root, "memory/semantic/a-lost-its-stamp.md"))
	if string(raw) != lost {
		t.Fatalf("PlanRestamp must not write the card")
	}
}

// A second lesson joins the first; it does not replace it. Before task 182 the
// stamp held one value, so each lesson a card taught overwrote the last.
func TestStampAddsASecondLessonRatherThanReplacingTheFirst(t *testing.T) {
	in := "---\ntitle: a\nconsolidated_into: \"[[first]]\"\nslug: a\n---\n\nbody\n"
	out := Stamp(in, "second")
	if !strings.Contains(out, "\nconsolidated_into: [\"[[first]]\", \"[[second]]\"]\n") {
		t.Fatalf("want both lessons in order:\n%s", out)
	}
	if got := strings.Join(StampedLessons(out), ","); got != "first,second" {
		t.Fatalf("StampedLessons read %q", got)
	}
	if again := Stamp(out, "first"); again != out {
		t.Fatalf("a lesson already named changes nothing:\n%s", again)
	}
}

// The recheck asks each lesson about a card under every lesson it names, and
// releases one lesson without taking the others. A card released by all of its
// lessons loses the line, in one act.
func TestRecheckReleasesOneLessonAndKeepsTheOthers(t *testing.T) {
	root := t.TempDir()
	for _, l := range []string{"keeps", "drops", "drops-too"} {
		writeRecheckNote(t, root, "memory/crystallized/"+l+".md", "---\ntitle: "+l+"\nkind: crystallized\n"+
			"consolidated_from:\n  - \"[[shared]]\"\n  - \"[[both-drop]]\"\n---\n\nLesson "+l+".\n\n## What taught it\n\n- [[shared]] — card\n- [[both-drop]] — card\n")
	}
	shared := "---\ntitle: Shared\ntype: reference\nconsolidated_into: [\"[[keeps]]\", \"[[drops]]\"]\nslug: shared\n---\n\nShared.\n"
	bothDrop := "---\ntitle: Both drop\ntype: reference\nconsolidated_into: [\"[[drops]]\", \"[[drops-too]]\"]\nslug: both-drop\n---\n\nBoth.\n"
	writeRecheckNote(t, root, "memory/semantic/shared.md", shared)
	writeRecheckNote(t, root, "memory/semantic/both-drop.md", bothDrop)
	call := func(p string) (string, error) {
		if strings.Contains(p, "## keeps") {
			return `{"sources": [1], "reason": "true of it"}`, nil
		}
		return `{"sources": [], "reason": "not true of them"}`, nil
	}
	_, acts, err := PlanRecheck(root, call, 0)
	if err != nil {
		t.Fatal(err)
	}
	after := map[string]string{}
	for _, a := range acts {
		if _, dup := after[a.Rel]; dup {
			t.Fatalf("two acts on %s; a manifest refuses the second", a.Rel)
		}
		after[a.Rel] = a.After
	}
	if !strings.Contains(after["memory/semantic/shared.md"], "\nconsolidated_into: \"[[keeps]]\"\n") {
		t.Errorf("shared keeps the lesson that is true of it:\n%s", after["memory/semantic/shared.md"])
	}
	if strings.Contains(after["memory/semantic/both-drop.md"], "consolidated_into") {
		t.Errorf("a card every lesson releases loses the line:\n%s", after["memory/semantic/both-drop.md"])
	}
}

// The recheck asks only the lessons it is told to. Restamp's cards were never
// asked about; asking every other lesson again would pay for answers on file.
func TestRecheckOnlyAsksTheNamedLessons(t *testing.T) {
	root := t.TempDir()
	writeRecheckNote(t, root, "memory/crystallized/answers-cite.md", recheckLesson)
	writeRecheckNote(t, root, "memory/crystallized/other-lesson.md",
		"---\ntitle: Other\nkind: crystallized\nconsolidated_from:\n  - \"[[z]]\"\n---\n\nOther.\n")
	writeRecheckNote(t, root, "memory/semantic/a-cites-its-sources.md", recheckCard("A", "answers-cite"))
	writeRecheckNote(t, root, "memory/semantic/z.md", recheckCard("Z", "other-lesson"))

	var asked []string
	call := func(p string) (string, error) {
		asked = append(asked, p)
		return `{"sources": [1], "reason": "kept"}`, nil
	}
	found, _, err := PlanRecheckOnly(root, call, 0, []string{"other-lesson"})
	if err != nil {
		t.Fatal(err)
	}
	if len(asked) != 1 || len(found) != 1 || found[0].Lesson != "memory/crystallized/other-lesson.md" {
		t.Fatalf("want one call, for other-lesson; asked %d, found %+v", len(asked), found)
	}
	asked = nil
	if found, _, err = PlanRecheckOnly(root, call, 0, nil); err != nil || len(found) != 2 || len(asked) != 2 {
		t.Fatalf("no filter asks every lesson with stamped cards; found %d, asked %d, err %v", len(found), len(asked), err)
	}
}

// A card the recheck released stays in the lesson's consolidated_from as
// provenance and is named in its released list; restamp leaves it unstamped.
// Without that the two tools would cycle: restamp gives the stamp back, the next
// recheck takes it off.
func TestRestampLeavesAReleasedCardUnstamped(t *testing.T) {
	root := t.TempDir()
	writeRecheckNote(t, root, "memory/crystallized/ci-green.md", "---\ntitle: CI green closes work\n"+
		"kind: crystallized\nconsolidated_from:\n  - \"[[taught-only]]\"\n  - \"[[rests-on]]\"\n"+
		"released: [\"[[taught-only]]\"]\n---\n\nLesson.\n")
	writeRecheckNote(t, root, "memory/semantic/taught-only.md", "---\ntitle: T\ntype: reference\n---\n\nT.\n")
	writeRecheckNote(t, root, "memory/semantic/rests-on.md", "---\ntitle: R\ntype: reference\n---\n\nR.\n")
	found, acts, _, err := PlanRestamp(root)
	if err != nil {
		t.Fatal(err)
	}
	state := map[string]string{}
	for _, f := range found {
		state[f.Card] = f.State
	}
	if state["taught-only"] != "released" || state["memory/semantic/rests-on.md"] != "restamp" {
		t.Fatalf("states %v", state)
	}
	if len(acts) != 1 || acts[0].Rel != "memory/semantic/rests-on.md" {
		t.Fatalf("only the card the lesson rests on is stamped: %+v", acts)
	}
}

// Releasing every source no longer empties the lesson: its provenance stays.
func TestRecheckReleasingEverySourceKeepsTheLessonsProvenance(t *testing.T) {
	root := t.TempDir()
	writeRecheckNote(t, root, "memory/crystallized/answers-cite.md", recheckLesson)
	for _, c := range []string{"a-cites-its-sources", "b-three-sub-agents", "c-answers-name-ids"} {
		writeRecheckNote(t, root, "memory/semantic/"+c+".md", recheckCard(c, "answers-cite"))
	}
	_, acts, err := PlanRecheck(root, func(string) (string, error) {
		return `{"sources": [], "reason": "true of none"}`, nil
	}, 0)
	if err != nil {
		t.Fatal(err)
	}
	for _, a := range acts {
		if a.Rel != "memory/crystallized/answers-cite.md" {
			continue
		}
		if !strings.Contains(a.After, "consolidated_from:\n  - \"[[a-cites-its-sources]]\"") {
			t.Fatalf("the lesson's provenance must stay:\n%s", a.After)
		}
		if got := len(ReleasedStems(a.After)); got != 3 {
			t.Fatalf("all three released, got %d:\n%s", got, a.After)
		}
		return
	}
	t.Fatal("no act on the lesson")
}
