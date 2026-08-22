package door

import (
	"strings"
	"testing"
)

func roots() Roots { return DefaultRoots() }

// The verification the plan names: a root-document write requires alignment.
func TestChangingARootDocumentRequiresAlignment(t *testing.T) {
	d := roots().Judge("desk/projects/agentm/README.md", true)
	if d.Permission != Alignment {
		t.Errorf("changing a root document is %s: %s", d.Permission, d.Why)
	}
	if d.Project != "agentm" {
		t.Errorf("Project = %q", d.Project)
	}
	if d.MayWrite() {
		t.Error("MayWrite is true for a write that needs alignment")
	}
	if !strings.Contains(d.Why, "visible face") {
		t.Errorf("the reason does not say why the root is different: %s", d.Why)
	}
}

// And the half that keeps the no-cap rule meaningful: a project may have as many
// root documents as it needs, so *creating* one is not the thing that asks.
func TestANewRootDocumentIsStanding(t *testing.T) {
	d := roots().Judge("desk/projects/agentm/CHARTER.md", false)
	if d.Permission != Standing {
		t.Errorf("creating a root document is %s: %s — the design says a project "+
			"may have as many as it needs, with no cap", d.Permission, d.Why)
	}
}

// Below the root the agent adds and maintains freely.
func TestEverythingBelowTheRootIsStanding(t *testing.T) {
	for _, rel := range []string{
		// The three the design names by table.
		"desk/projects/agentm/desk/a-draft.md",
		"desk/projects/agentm/decisions/0001-something.md",
		"desk/projects/agentm/research/a-question.md",
		// And the ones it covers by rule rather than by name — the vault's own
		// projects carry these, and a door that only knew the three would ask
		// about every one of them.
		"desk/projects/agentm/arcs/an-arc.md",
		"desk/projects/agentm/convention/a-convention.md",
		"desk/projects/agentm/pattern/a-pattern.md",
		"desk/projects/agentm/_harness/PLAN.md",
		// Arbitrarily deep.
		"desk/projects/agentm/research/deep/deeper/note.md",
	} {
		for _, exists := range []bool{true, false} {
			d := roots().Judge(rel, exists)
			if d.Permission != Standing {
				t.Errorf("%s (exists=%v) is %s: %s", rel, exists, d.Permission, d.Why)
			}
		}
	}
}

// Only the operator creates a project. That is what makes the door mean
// anything, so the project directory itself is never standing.
func TestTheProjectDirectoryItselfNeedsAlignment(t *testing.T) {
	d := roots().Judge("desk/projects/a-new-project", false)
	if d.Permission != Alignment {
		t.Errorf("creating a project directory is %s: %s", d.Permission, d.Why)
	}
	if !strings.Contains(d.Why, "only the operator") {
		t.Errorf("the reason does not say whose declaration a project is: %s", d.Why)
	}
}

// The workbench is the agent's own container — the difference between a task and
// a project is authorship of the container, not the contents.
func TestTheTaskWorkbenchIsStandingThroughout(t *testing.T) {
	for _, rel := range []string{
		"desk/tasks/an-investigation",
		"desk/tasks/an-investigation/progress.md",
		"desk/tasks/an-investigation/notes/deep.md",
	} {
		d := roots().Judge(rel, true)
		if d.Permission != Standing {
			t.Errorf("%s is %s: %s", rel, d.Permission, d.Why)
		}
		if d.Task != "an-investigation" {
			t.Errorf("%s: Task = %q", rel, d.Task)
		}
	}
}

// Outside a project or a task this door abstains rather than inventing an
// opinion. Other rules govern the memory spaces, and a door with a view on
// everything is one that will eventually disagree with them.
func TestOutsideAProjectTheDoorAbstains(t *testing.T) {
	for _, rel := range []string{
		"memory/semantic/a-fact.md",
		"desk/diagnostics/health.md",
		"standards/storage-rules.md",
		"desk/projects",
	} {
		d := roots().Judge(rel, true)
		if d.Permission != Outside {
			t.Errorf("%s is %s, want outside: %s", rel, d.Permission, d.Why)
		}
		if d.MayWrite() {
			t.Errorf("%s: MayWrite is true for a path this door does not govern", rel)
		}
	}
}

// Every unknown asks. Answering standing wrongly rewrites a document the
// operator meant to own; answering alignment wrongly asks a question that was
// not needed. One is a conversation and the other is a surprise.
func TestAnUnreadablePathAsks(t *testing.T) {
	for _, rel := range []string{
		"",
		"   ",
		"desk/projects/agentm/../../../etc/passwd",
		"../outside",
	} {
		d := roots().Judge(rel, true)
		if d.Permission == Standing {
			t.Errorf("%q was answered standing: %s", rel, d.Why)
		}
	}
}

// Every decision says why, including the ones that went well.
func TestEveryDecisionCarriesItsReason(t *testing.T) {
	for _, rel := range []string{
		"desk/projects/agentm/README.md",
		"desk/projects/agentm/research/a.md",
		"desk/tasks/t/progress.md",
		"memory/semantic/a.md",
		"",
	} {
		if d := roots().Judge(rel, true); strings.TrimSpace(d.Why) == "" {
			t.Errorf("%q was answered %s with no reason", rel, d.Permission)
		}
	}
}

// The layout is supplied rather than hardcoded, because it has moved once
// already — `projects/` became `desk/projects/` in the four-space migration, and
// a literal would have gone quietly wrong that day.
func TestTheLayoutIsConfigurable(t *testing.T) {
	old := Roots{Projects: "projects", Tasks: "tasks"}
	d := old.Judge("projects/agentm/research/a.md", true)
	if d.Permission != Standing {
		t.Errorf("under the older layout, %s: %s", d.Permission, d.Why)
	}
	// And the current layout does not answer for the old one's paths.
	if d := roots().Judge("projects/agentm/research/a.md", true); d.Permission != Outside {
		t.Errorf("the current layout claimed an old-layout path: %s", d.Why)
	}
}

// Windows separators resolve the same way. The vault syncs across machines and a
// path that arrived with backslashes is the same path.
func TestSeparatorsAreNormalized(t *testing.T) {
	d := roots().Judge(`desk\projects\agentm\research\a.md`, true)
	if d.Permission != Standing {
		t.Errorf("a backslash path is %s: %s", d.Permission, d.Why)
	}
	if d.Project != "agentm" {
		t.Errorf("Project = %q", d.Project)
	}
}
