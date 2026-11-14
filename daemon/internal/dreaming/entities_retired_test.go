package dreaming

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/people"
)

// Task 202: pages the builder wrote for issues, releases and people the
// people file does not list are no longer written, and no longer removed
// either: they stay, byte for byte, for the clear-out the operator approves,
// and the plan names them.
func TestPagesTheBuilderNoLongerWritesAreLeftAndNamed(t *testing.T) {
	notes := fixtureEntityNotes()
	builderPage := func(kind, id, title string) string {
		return "---\ntitle: " + title + "\nkind: entity-profile\ncreated: 2026-09-01\nupdated: 2026-09-01\n" +
			"entity_type: " + kind + "\nentity_id: \"" + kind + ":" + id + "\"\n---\n\n# " + title + "\n"
	}
	old := map[string]string{
		"agent/memory/entities/issues/alexherrero-agentm-466.md":       builderPage("issue", "alexherrero/agentm#466", "alexherrero/agentm#466"),
		"agent/memory/entities/releases/alexherrero-agentm-v10-0-0.md": builderPage("release", "alexherrero/agentm@v10.0.0", "alexherrero/agentm v10.0.0"),
		"agent/memory/entities/people/samus.md":                        builderPage("person", "samus", "Samus"),
		"agent/memory/entities/people/kevin.md":                        builderPage("person", "kevin", "Kevin"),
		// A listed person's page, under the bar: still removed.
		"agent/memory/entities/people/jane-doe.md": builderPage("person", "jane-doe", "Jane Doe"),
	}
	for rel, text := range old {
		notes[rel] = text
	}
	root, x := entityVault(t, notes)
	table, err := people.Parse("```people\naliases:\n  Jane Doe: [Jane]\n```\n")
	if err != nil {
		t.Fatal(err)
	}
	plan, err := PlanEntities(root, filepath.Dir(root), x, PeopleOptions{Table: table}, nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		"agent/memory/entities/issues/alexherrero-agentm-466.md",
		"agent/memory/entities/people/kevin.md",
		"agent/memory/entities/people/samus.md",
		"agent/memory/entities/releases/alexherrero-agentm-v10-0-0.md",
	}
	if strings.Join(plan.NoLongerBuilt, " ") != strings.Join(want, " ") {
		t.Errorf("no longer built %v, want %v", plan.NoLongerBuilt, want)
	}
	for _, in := range plan.Intents {
		for rel := range old {
			if strings.HasSuffix(rel, in.Rel) && !strings.HasSuffix(rel, "jane-doe.md") {
				t.Errorf("the plan touches %s, a page it no longer writes", in.Rel)
			}
		}
	}
	if len(plan.Removed) != 2 || plan.Removed[0] != "memory/entities/repos/alexherrero-gone.md" ||
		plan.Removed[1] != "memory/entities/people/jane-doe.md" {
		t.Errorf("removed %v, want the listed person under the bar and the stale repository page", plan.Removed)
	}
	applyEntityIntents(t, root, plan.Intents)
	for rel, text := range old {
		got, err := os.ReadFile(filepath.Join(filepath.Dir(root), filepath.FromSlash(rel)))
		if strings.HasSuffix(rel, "jane-doe.md") {
			if !os.IsNotExist(err) {
				t.Errorf("the listed person's page under its bar is still there: %v", err)
			}
			continue
		}
		if err != nil || string(got) != text {
			t.Errorf("%s changed or went: %v", rel, err)
		}
	}
	// The night's log names the count, not 246 lines.
	body, _ := renderNightSections(&Report{Entities: plan})
	if !strings.Contains(body, "4 page(s) the builder no longer writes") {
		t.Errorf("the night's log does not name the pages left:\n%s", body)
	}
}
