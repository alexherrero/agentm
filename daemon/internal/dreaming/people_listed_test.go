package dreaming

import (
	"path/filepath"
	"strings"
	"testing"
)

// Task 202, on the audit's own cases: a first name two listed people share
// builds no page and counts for neither; a name the people file never listed
// (a game character) builds no page and is reported; a listed person's full
// name builds one.
func TestOnlyAListedPersonGetsAPage(t *testing.T) {
	note := func(day, person, body string) string {
		return "---\nupdated: 2026-09-" + day + "\npeople: [" + person + "]\n---\n\n" + body + "\n"
	}
	notes := map[string]string{
		"projects/agentm/project.yaml":            "slug: agentm\nrepositories:\n  - alexherrero/agentm\n",
		"projects/agentm/tasks/001-a/progress.md": note("20", "Kevin", "Kevin reviewed the plan."),
		"projects/agentm/tasks/002-b/progress.md": note("21", "Kevin", "Kevin again."),
		"projects/zero/tasks/001-a/progress.md":   note("22", "Samus", "Samus jumps higher."),
		"projects/zero/tasks/002-b/progress.md":   note("23", "Samus", "Samus again."),
		"projects/agentm/tasks/003-c/progress.md": note("24", "Kevin Smith", "Kevin Smith signed off."),
		"projects/agentm/tasks/004-d/progress.md": note("25", "Kev", "Kev merged it."),
	}
	root, x := entityVault(t, notes)
	opts := listedPeople(t, "  Kevin Smith: [Kevin, Kev]\n  Kevin Jones: [Kevin]\n")
	plan, err := PlanEntities(root, filepath.Dir(root), x, opts, nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	pages := personPages(plan)
	if len(pages) != 1 {
		t.Fatalf("person pages %+v, want Kevin Smith's alone", pages)
	}
	smith := pages["person:kevin-smith"]
	// His full name and "Kev", which only he answers to; not the two notes
	// that say only "Kevin", which Kevin Jones answers to as well.
	if smith.SharedWork != 2 || smith.Mentions != 2 {
		t.Errorf("Kevin Smith's page: %+v, want 2 pieces of shared work", smith)
	}
	if strings.Join(plan.Unlisted, ",") != "Samus" {
		t.Errorf("unlisted %v, want Samus (\"Kevin\" is listed, twice, so it is not unlisted)", plan.Unlisted)
	}
}

// An empty people file builds no person pages at all, and reports every name.
func TestWithNoPeopleFileThereAreNoPersonPages(t *testing.T) {
	root, x := entityVault(t, map[string]string{
		"projects/agentm/tasks/001-a/progress.md": "---\nupdated: 2026-09-20\npeople: [Ana Ruiz]\n---\n\nAna Ruiz.\n",
		"projects/agentm/tasks/002-b/progress.md": "---\nupdated: 2026-09-21\npeople: [Ana Ruiz]\n---\n\nAna Ruiz.\n",
	})
	plan, err := PlanEntities(root, filepath.Dir(root), x, PeopleOptions{}, nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	if len(personPages(plan)) != 0 || strings.Join(plan.Unlisted, ",") != "Ana Ruiz" {
		t.Errorf("pages %v, unlisted %v", personPages(plan), plan.Unlisted)
	}
}
