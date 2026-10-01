package dreaming

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/note"
	"github.com/alexherrero/agentm/daemon/internal/people"
)

// Task 179 step 6: a person gets a page only once named in two pieces of
// shared work, and mentions elsewhere are listed but never qualify them.

func peopleFixture() map[string]string {
	notes := fixtureEntityNotes()
	// Ana is named once, in a task: no page.
	notes["projects/agentm/tasks/003-c/progress.md"] = "---\nupdated: 2026-09-21\n---\n\nAna Ruiz reviewed the plan.\n"
	notes["agent/memory/semantic/ana.md"] = "---\ntitle: Ana's note\ntype: reference\npeople: [Ana Ruiz]\n---\n\nAna Ruiz knows the ranker.\n"
	// Ben is named in two decisions: a page, and his card listed below them.
	notes["projects/agentm/decisions/keep-the-wall.md"] = "---\ntitle: Keep the wall\nkind: decision\n" +
		"updated: 2026-09-10\npeople: [Ben Okafor]\n---\n\nDecided with Ben Okafor.\n"
	notes["projects/agentm/decisions/ship-weekly.md"] = "---\ntitle: Ship weekly\nkind: decision\n" +
		"updated: 2026-09-12\n---\n\nBen and Pat agreed to ship weekly.\n"
	notes["agent/memory/semantic/ben.md"] = "---\ntitle: Ben's review style\ntype: reference\n---\n\nBen Okafor reads diffs bottom up.\n"
	// Cleo is named ten times, only in the reference library: no page.
	for i := 0; i < 10; i++ {
		notes["resources/topics/ranking/cleo-"+string(rune('a'+i))+".md"] = "---\ntitle: Cleo " +
			string(rune('a'+i)) + "\npeople: [Cleo Park]\n---\n\nCleo Park's paper on ranking.\n"
	}
	return notes
}

func peopleOpts(t *testing.T) PeopleOptions {
	t.Helper()
	tb, err := people.Parse("```people\nyou: [Pat Owner, Pat]\naliases:\n  Ben Okafor: [Ben]\n```\n")
	if err != nil {
		t.Fatal(err)
	}
	return PeopleOptions{Table: tb}
}

func personPages(plan EntitiesPlan) map[string]EntityPage {
	out := map[string]EntityPage{}
	for _, p := range plan.Pages {
		if p.Type == "person" {
			out[p.ID] = p
		}
	}
	return out
}

func TestAPersonNeedsTwoPiecesOfSharedWork(t *testing.T) {
	root, x := entityVault(t, peopleFixture())
	plan, err := PlanEntities(root, filepath.Dir(root), x, peopleOpts(t), nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	got := personPages(plan)
	if len(got) != 1 || got["person:ben-okafor"].SharedWork != 2 || got["person:ben-okafor"].Mentions != 3 {
		t.Fatalf("person pages %+v, want only Ben, with 2 shared-work notes of 3 mentions", got)
	}
	var page string
	for _, in := range plan.Intents {
		if in.Rel == "memory/entities/people/ben-okafor.md" {
			page = string(in.After)
		}
	}
	shared := strings.Index(page, "## Shared work")
	other := strings.Index(page, "## Other mentions")
	if shared < 0 || other < shared ||
		!strings.Contains(page[shared:other], "[[projects/agentm/decisions/ship-weekly|Ship weekly]]") ||
		!strings.Contains(page[other:], "[[agent/memory/semantic/ben|Ben's review style]]") {
		t.Errorf("Ben's page does not list his shared work first:\n%s", page)
	}
	for _, want := range []string{"entity_type: person\n", "shared_work: 2\n", "aliases: [Ben]\n"} {
		if !strings.Contains(page, want) {
			t.Errorf("Ben's page lacks %q:\n%s", want, page)
		}
	}
}

// A page whose person falls under the bar is removed, and a walled area is
// never read: a note behind the wall neither registers a person nor counts.
func TestAPersonUnderTheBarLosesTheirPageAndTheWallHolds(t *testing.T) {
	notes := peopleFixture()
	// Behind the wall, Dee would qualify twice over — two meeting notes — and
	// Ana would gain the second piece of shared work she lacks.
	notes["personal/Home/Important Docs/board meeting.md"] = "---\npeople: [Dee Voss]\n---\n\nDee Voss and Ana Ruiz.\n"
	notes["personal/Home/Important Docs/budget meeting.md"] = "---\npeople: [Dee Voss]\n---\n\nDee Voss.\n"
	note.SetRecallExemptAreas([]string{"personal/Home/Important Docs"})
	t.Cleanup(func() { note.SetRecallExemptAreas(nil) })
	root, x := entityVault(t, notes)
	first, err := PlanEntities(root, filepath.Dir(root), x, peopleOpts(t), nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"person:dee-voss", "person:ana-ruiz"} {
		if _, ok := personPages(first)[id]; ok {
			t.Fatalf("%s got a page from notes behind the wall", id)
		}
	}
	applyEntityIntents(t, root, first.Intents)
	vault := filepath.Dir(root)
	if err := os.Remove(filepath.Join(vault, "projects/agentm/decisions/ship-weekly.md")); err != nil {
		t.Fatal(err)
	}
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}
	second, err := PlanEntities(root, filepath.Dir(root), x, peopleOpts(t), nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	if len(second.Removed) != 1 || second.Removed[0] != "memory/entities/people/ben-okafor.md" {
		t.Errorf("removed %v, want Ben's page once he has one piece of shared work", second.Removed)
	}
}

func TestSharedWorkIsKnownByItsPath(t *testing.T) {
	for rel, want := range map[string]bool{
		"projects/agentm/tasks/001-a/plan.md":               true,
		"projects/agentm/completed/tasks/001-a/progress.md": true,
		"projects/completed/old/tasks/001-a/tracker.md":     true,
		"projects/agentm/tracker.md":                        true,
		"projects/agentm/decisions/keep-the-wall.md":        true,
		"projects/agentm/completed/decisions/old.md":        true,
		"calendar/2026/2026-09-20-meetings.md":              true,
		"personal/Church/Ward Council Meeting Notes.md":     true,
		"projects/agentm/tasks/001-a/notes.md":              false,
		"projects/agentm/research/topic/ranking.md":         false,
		"resources/topics/ranking/paper.md":                 false,
		"agent/memory/semantic/a-card.md":                   false,
		"calendar/_daily-template.md":                       false,
	} {
		if got := IsSharedWork(rel); got != want {
			t.Errorf("IsSharedWork(%q) = %v, want %v", rel, got, want)
		}
	}
}

// The email switch (step 7): off, a thread is ignored and the source is never
// asked; on, a thread with the operator is shared work for each person on it.
type fixtureMail struct {
	threads []EmailThread
	asked   *int
}

func (f fixtureMail) Threads(context.Context) ([]EmailThread, error) {
	*f.asked++
	return f.threads, nil
}

func TestAnEmailThreadCountsOnlyWhileTheSwitchIsOn(t *testing.T) {
	root, x := entityVault(t, peopleFixture())
	opts := peopleOpts(t)
	asked := 0
	opts.Email = fixtureMail{threads: []EmailThread{{ID: "t1", Participants: []string{"Ana Ruiz", "Pat Owner"}}}, asked: &asked}
	off, err := PlanEntities(root, filepath.Dir(root), x, opts, nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := personPages(off)["person:ana-ruiz"]; ok || asked != 0 {
		t.Errorf("with the switch off, Ana's page %v and the source was asked %d time(s)", ok, asked)
	}
	opts.EmailEnabled = true
	on, err := PlanEntities(root, filepath.Dir(root), x, opts, nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	if p, ok := personPages(on)["person:ana-ruiz"]; !ok || p.SharedWork != 2 {
		t.Errorf("with the switch on, Ana's page %+v; want one task and one thread", p)
	}
	if _, ok := personPages(on)["person:pat-owner"]; ok {
		t.Error("the operator got a page from their own thread")
	}
}
