package door

import (
	"strings"
	"testing"
)

// The allow/deny matrix the filing-v2 plan names as this part's verification.
// Every row is one ruled behavior; a row failing is the table drifting from
// the operator's ruling, not a style problem.
func TestAuthorityMatrix(t *testing.T) {
	a := DefaultAuthority()
	none := NewGrants()
	agentm := NewGrants("agentm")

	cases := []struct {
		name   string
		rel    string
		exists bool
		grants Grants
		want   Permission
		whyHas string
	}{
		{"the agent's own half is standing",
			"Agent/memory/semantic/some-fact.md", false, none, Standing, "agent's own half"},
		{"the calendar is a shared surface, no grant needed",
			"Calendar/2026/2026-09-01-diary.md", false, none, Standing, "shared surface"},
		{"a project write without a grant asks, and names the grant phrase",
			"Projects/agentm/_harness/PLAN.md", true, none, Alignment,
			`open the files for project agentm`},
		{"a grant admits the working bulk",
			"Projects/agentm/_harness/PLAN.md", true, agentm, Standing, "session grant for agentm"},
		{"a grant does not waive the face rule — changing a root document still asks",
			"Projects/agentm/README.md", true, agentm, Alignment, "visible face"},
		{"a new root document under a grant is standing, per the no-cap rule",
			"Projects/agentm/decisions.md", false, agentm, Standing, "as many"},
		{"a grant is per project, not per space",
			"Projects/crickets/notes.md", false, agentm, Alignment, "crickets"},
		{"the projects space itself stays the operator's even under a grant",
			"Projects", false, agentm, Alignment, "only the operator creates"},
		{"the operator's personal space always asks",
			"Personal/Home/recipes/soup.md", false, none, Alignment, "per-task instruction"},
		{"a grant never reaches Personal",
			"Personal/Home/recipes/soup.md", false, agentm, Alignment, "per-task instruction"},
		{"standards are operator-owned — propose, never apply unasked",
			"standards/storage-rules.md", true, none, Alignment, "operator-owned"},
		{"a root file is the operator's",
			"Filing.md", true, none, Alignment, "not in the declared address space"},
		{"an undeclared space is denied by default",
			"Attic/box.md", false, none, Alignment, "not in the declared address space"},
		{"matching is case-insensitive, because macOS treats the cases as one directory",
			"personal/Home/x.md", false, none, Alignment, "per-task instruction"},
		{"case-insensitivity survives the grant composition — the working bulk",
			"projects/agentm/_harness/notes.md", false, agentm, Standing, "maintains freely"},
		{"case-insensitivity survives the grant composition — the face rule",
			"PROJECTS/agentm/README.md", true, agentm, Alignment, "visible face"},
		{"a lowercase project write without a grant still names the grant phrase",
			"projects/agentm/x.md", false, none, Alignment, "open the files for project agentm"},
		{"an unreadable path asks",
			"Projects/../standards/storage-rules.md", true, agentm, Alignment, "unreadable"},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			d := a.JudgeSpace(tc.rel, tc.exists, tc.grants)
			if d.Permission != tc.want {
				t.Fatalf("JudgeSpace(%q, exists=%v, grants=%v) = %s (%s); want %s",
					tc.rel, tc.exists, tc.grants, d.Permission, d.Why, tc.want)
			}
			if !strings.Contains(d.Why, tc.whyHas) {
				t.Errorf("why = %q; want it to mention %q — the reason is part of the "+
					"contract, because the asked party acts on it", d.Why, tc.whyHas)
			}
		})
	}
}

func TestGrantsNormalize(t *testing.T) {
	g := NewGrants("  AgentM ", "")
	if !g.Has("agentm") || !g.Has("AGENTM") {
		t.Error("grants must match the way the table matches — trimmed, case-insensitive")
	}
	if g.Has("") {
		t.Error("an empty slug must never be a grant")
	}
	if NewGrants().Has("anything") {
		t.Error("the zero grant set is every session's starting state and holds nothing")
	}
}

// The composition seam: a granted write inside a project defers to the
// per-file-class judgment, and the decision says both halves' reasons.
func TestGrantComposesWithFileClassJudgment(t *testing.T) {
	a := DefaultAuthority()
	d := a.JudgeSpace("Projects/agentm/wiki/designs/notes.md", false, NewGrants("agentm"))
	if d.Permission != Standing {
		t.Fatalf("subfolder under a grant = %s (%s); want standing", d.Permission, d.Why)
	}
	if d.Space != "Projects" || d.Level != GrantRequired || d.Project != "agentm" {
		t.Errorf("composed decision lost its vault-level context: %+v", d)
	}
	if !strings.Contains(d.Why, "grant") || !strings.Contains(d.Why, "maintains freely") {
		t.Errorf("why = %q; want both halves' reasons visible", d.Why)
	}
}
