package crystallize

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// When an arc closes, the closing session marks it in the project tracker and
// the phase's next run writes the arc's synthesis at
// `memory/crystallized/<project>-<arc>.md` from the arc's Outcomes.

// arcMark writes a project's own tracker with `arc_closed:` set.
func (f *fixture) arcMark(t *testing.T, project string, arcs ...string) {
	t.Helper()
	p := filepath.Join(f.vault, "projects", project, "tracker.md")
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	body := "---\nkind: tracker\ntitle: " + project + "\nstatus: active\n" +
		ArcField + ": [" + strings.Join(arcs, ", ") + "]\n---\n\n## Objective\n\nthe work\n"
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestAClosedArcIsSynthesisedUnderItsOwnName(t *testing.T) {
	f := newFixture(t)
	f.arcMark(t, "agentm", "vault-perfection")
	// Three closed tasks, no shared vocabulary between them: an arc is the
	// project's own story, not a recurrence that happened inside it.
	f.outcome(t, "agentm", "101-one", "2026-08-01", "the wall")
	f.outcome(t, "agentm", "102-two", "2026-08-20", "the curve")
	f.outcome(t, "agentm", "103-three", "2026-09-10", "the sidecars")

	rep := f.run(t, Options{})

	if len(rep.Lessons) != 1 {
		t.Fatalf("wrote %d lesson(s), want the arc's synthesis: %+v (skipped %+v)",
			len(rep.Lessons), rep.Lessons, rep.Skipped)
	}
	if got := rep.Lessons[0].Rel; got != "memory/crystallized/agentm-vault-perfection.md" {
		t.Errorf("the synthesis landed at %s; the closing session looks for it at "+
			"<project>-<arc>", got)
	}
	// The model's own `subject` does not get to rename it.
	if !strings.Contains(strings.Join(f.prompts, "\n"), "An arc of work has closed") {
		t.Error("the arc was asked the recurrence question")
	}
	if len(rep.Lessons[0].Sources) != 3 {
		t.Errorf("the synthesis names %d source(s), want every Outcome in the arc",
			len(rep.Lessons[0].Sources))
	}
}

func TestAnArcTooShortToSayAnythingIsRefusedAndSaysWhy(t *testing.T) {
	f := newFixture(t)
	f.arcMark(t, "agentm", "one-afternoon")
	f.outcome(t, "agentm", "101-one", "2026-09-10", "the wall")
	f.outcome(t, "agentm", "102-two", "2026-09-11", "the curve")

	rep := f.run(t, Options{})

	if len(rep.Lessons) != 0 || len(f.prompts) != 0 {
		t.Fatalf("an arc of two tasks in a day was synthesised: %+v", rep.Lessons)
	}
	var found string
	for _, m := range rep.NearMisses {
		if strings.HasPrefix(m.Reason, "arc:") {
			found = m.Reason
		}
	}
	if found == "" {
		t.Error("a marked arc was refused with nothing said about why; the session " +
			"that marked it has no other way to find out")
	}
}

func TestAnArcIsSynthesisedOnce(t *testing.T) {
	f := newFixture(t)
	f.arcMark(t, "agentm", "vault-perfection")
	f.outcome(t, "agentm", "101-one", "2026-08-01", "the wall")
	f.outcome(t, "agentm", "102-two", "2026-08-20", "the curve")
	f.outcome(t, "agentm", "103-three", "2026-09-10", "the sidecars")

	f.run(t, Options{})
	calls := len(f.prompts)
	second := f.run(t, Options{})

	if len(second.Lessons) != 0 || len(f.prompts) != calls {
		t.Errorf("the arc was synthesised again: %+v after %d more call(s); the "+
			"mark is a record of what closed, not a trigger that fires weekly",
			second.Lessons, len(f.prompts)-calls)
	}
}

func TestTheMarkIsReadAsOneNameOrAList(t *testing.T) {
	f := newFixture(t)
	p := filepath.Join(f.vault, "projects", "agentm", "tracker.md")
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	for _, spelling := range []string{
		ArcField + ": vault-perfection\n",
		ArcField + ": [vault-perfection]\n",
		ArcField + ":\n  - vault-perfection\n",
		ArcField + ": \"vault-perfection\"\n",
	} {
		body := "---\nkind: tracker\nstatus: active\n" + spelling + "---\n\nbody\n"
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
		got := ClosedArcs(f.vault)
		if len(got) != 1 || got[0].Stem() != "agentm-vault-perfection" {
			t.Errorf("%q read as %+v", spelling, got)
		}
	}
}

func TestAnArcNameThatAlreadyCarriesTheProjectIsNotDoubled(t *testing.T) {
	a := Arc{Project: "agentm", Name: "agentm-vault"}
	if got := a.Stem(); got != "agentm-vault" {
		t.Errorf("stem = %q, want the name as written", got)
	}
}
