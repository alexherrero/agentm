package dreaming

import (
	"fmt"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// A type that falls below the floor loses its page, on the operator's ruling of
// 2026-09-13 (agentm-vault plan 07 review, finding 2). moc-memory.md lists the
// type's notes in full, and a page left behind would fail the maps gate with
// nothing to repair it. The removal is a journaled delete carrying the page's
// bytes.
func TestAPageWhoseTypeFellBelowTheFloorIsRemoved(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC)
	for i := 0; i < 4; i++ {
		writeTyped(t, root, fmt.Sprintf("memory/semantic/fact-%d.md", i), "fact", "2026-08-01", "A fact worth keeping.\n")
	}
	for i := 0; i < 5; i++ {
		writeTyped(t, root, fmt.Sprintf("memory/procedural/step-%d.md", i), "workflow", "2026-08-10", "A step.\n")
	}
	factPage := "---\nkind: moc\nslug: fact\ntype_of_members: fact\n---\n\n# fact\n\n- [[fact-0]]\n"
	ideaPage := "---\nkind: moc\nslug: idea\ntype_of_members: idea\n---\n\n# idea\n"
	writeRaw(t, root, "memory/mocs/fact.md", factPage)
	writeRaw(t, root, "memory/mocs/idea.md", ideaPage)
	// Pages the job did not write for a type are not its to remove: one under a
	// type's name made by hand, and one under no type's name at all.
	writeRaw(t, root, "memory/mocs/fix.md", "---\nkind: moc\n---\n\n# my own fix notes\n")
	writeRaw(t, root, "memory/mocs/notes.md", "---\nkind: moc\n---\n\n# not a type's page\n")
	r := &rules.Rules{}
	r.MemoryTypes = []string{"fact", "fix", "idea", "workflow"}

	plan, err := PlanMocs(root, r, now)
	if err != nil {
		t.Fatal(err)
	}
	deleted := map[string]string{}
	for _, in := range plan.Intents {
		if in.Delete {
			deleted[in.Rel] = string(in.Before)
		}
	}
	if want := map[string]string{"memory/mocs/fact.md": factPage, "memory/mocs/idea.md": ideaPage}; !reflect.DeepEqual(deleted, want) {
		t.Errorf("deleted %v; want the page of the type below the floor and of the type with no live note left", deleted)
	}
	if want := []string{"memory/mocs/fact.md", "memory/mocs/idea.md"}; !reflect.DeepEqual(plan.Removed, want) {
		t.Errorf("removed %v, want %v", plan.Removed, want)
	}
	memory := ""
	for _, in := range plan.Intents {
		if in.Rel == MocRel(MocMemorySlug) {
			memory = string(in.After)
		}
	}
	for _, want := range []string{"- fact — 4 notes, listed below\n", "\n## fact\n", "[[fact-0]]", "[[workflow]]"} {
		if !strings.Contains(memory, want) {
			t.Errorf("moc-memory.md lacks %q:\n%s", want, memory)
		}
	}
}

// With nothing typed to map, the pass writes no map and removes none: a corpus
// that reads as empty is more likely a scan gone wrong than a vault emptied.
func TestNoTypedNoteRemovesNoPage(t *testing.T) {
	root := t.TempDir()
	writeRaw(t, root, "memory/mocs/fact.md", "---\nkind: moc\nslug: fact\ntype_of_members: fact\n---\n\n# fact\n")
	r := &rules.Rules{}
	r.MemoryTypes = []string{"fact"}

	plan, err := PlanMocs(root, r, time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Intents) != 0 || len(plan.Removed) != 0 {
		t.Errorf("a map a fuller corpus left behind stays as it is when nothing is typed: removed %v", plan.Removed)
	}
}
