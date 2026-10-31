package note

import "testing"

// What fades is the contract's list and nothing else (the operator's ruling of
// 2026-10-30), and a `*` reaches a folder in any one project.
func TestOnlyTheListedAreasFade(t *testing.T) {
	t.Cleanup(func() { SetFadeAreas(nil) })
	SetFadeAreas([]string{"agent/memory/episodic", "agent/memory/entities/issues", "projects/*/cleanup"})

	for rel, want := range map[string]bool{
		"agent/memory/episodic/2026-10-01-trace.md":       true,
		"agent/memory/entities/issues/a-repo-12.md":       true,
		"projects/movies-tv-games/cleanup/run.md":         true,
		"projects/movies/CleanUp/nested/run.md":           true,
		"agent/memory/entities/repos/a-repo.md":           false,
		"agent/memory/semantic/a-fact.md":                 false,
		"projects/agentm/designs/agentm-vault.md":         false,
		"projects/movies-tv-games/cleanup.md":             false,
		"projects/movies-tv-games/completed/cleanup/x.md": false,
		"projects/cleanup/x.md":                           false,
	} {
		if got := Fades(rel); got != want {
			t.Errorf("Fades(%q) = %v, want %v", rel, got, want)
		}
	}
}

// No list is the old behaviour rather than nothing fading, so a contract that
// predates the line ranks as it did.
func TestAContractWithNoListFadesEverything(t *testing.T) {
	t.Cleanup(func() { SetFadeAreas(nil) })
	for _, areas := range [][]string{nil, {}} {
		SetFadeAreas(areas)
		if !Fades("projects/agentm/designs/agentm-vault.md") {
			t.Errorf("with %v as the list, a project note stopped fading", areas)
		}
	}
}
