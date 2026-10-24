package dreaming

import (
	"fmt"
	"strings"
	"testing"
	"time"

	"golang.org/x/text/unicode/norm"
)

// The unique-stem rule (task 193 LD-4): a bare link only where the name is the
// vault's only one, else the note's path with a label. The cases match
// scripts/test_stem_links.py's.

func stemVault(t *testing.T) (root, vault string) {
	t.Helper()
	root, vault = rootMapVault(t)
	for _, rel := range []string{
		"agent/memory/semantic/_index.md", "agent/memory/procedural/_index.md",
		"agent/memory/semantic/editor-choice.md", "projects/x/Editor-Choice.md",
		"agent/memory/semantic/lonely.md", "agent/memory/semantic/pic.md", "resources/pic.png",
		".trash/lonely.md", "agent/memory/semantic/.hidden.md",
		"projects/x/" + norm.NFD.String("café") + ".md", "agent/memory/semantic/" + norm.NFC.String("café") + ".md",
	} {
		writeAt(t, vault, rel, "x\n")
	}
	return root, vault
}

func TestStemIndexLinks(t *testing.T) {
	_, vault := stemVault(t)
	ix := NewStemIndex(vault)
	for _, c := range []struct{ rel, label, want string }{
		{"agent/memory/semantic/lonely.md", "", "[[lonely]]"},
		{"agent/memory/semantic/lonely.md", "Lonely note", "[[lonely|Lonely note]]"},
		{"agent/memory/semantic/_index.md", "", "[[agent/memory/semantic/_index|_index]]"},
		{"agent/memory/procedural/_index.md", "procedural", "[[agent/memory/procedural/_index|procedural]]"},
		// compared without case, and in NFC
		{"agent/memory/semantic/editor-choice.md", "", "[[agent/memory/semantic/editor-choice|editor-choice]]"},
		{"agent/memory/semantic/café.md", "", "[[agent/memory/semantic/café|café]]"},
		// a dot-folder, a dot-file and an attachment carry no name
		{"agent/memory/semantic/pic.md", "", "[[pic]]"},
		{"agent/memory/mocs/tomorrow.md", "", "[[tomorrow]]"},
		{"agent/memory/semantic/_index.md", "a | b [c]", "[[agent/memory/semantic/_index|a - b (c)]]"},
	} {
		if got := ix.Link(c.rel, c.label); got != c.want {
			t.Errorf("Link(%q, %q) = %q, want %q", c.rel, c.label, got, c.want)
		}
	}
	if !ix.Unique("lonely") || !ix.Unique(".hidden") {
		t.Errorf("a dot-folder's or dot-file's name was counted")
	}
	var none *StemIndex
	if got := none.Link("agent/memory/semantic/_index.md", ""); got != "[[_index]]" {
		t.Errorf("a nil index links %q, want the bare name", got)
	}
}

// The type maps and the memory map (task 193 step 3): a member whose name
// another note shares is linked by its path, a unique one stays bare, a type
// page whose name is shared is linked by path, and the class indexes are
// listed by path.
func TestMocsLinkASharedNameByItsPath(t *testing.T) {
	root, vault := rootMapVault(t)
	for i := 0; i < 4; i++ {
		writeTyped(t, root, fmt.Sprintf("memory/semantic/fact-%d.md", i), "fact", "2026-08-0"+fmt.Sprint(i+1), "A fact.\n")
	}
	writeTyped(t, root, "memory/semantic/shared-name.md", "fact", "2026-08-09", "A fact.\n")
	writeAt(t, vault, "projects/x/shared-name.md", "x\n")
	writeTyped(t, root, "memory/procedural/only-step.md", "workflow", "2026-08-10", "A step.\n")
	writeAt(t, vault, "projects/x/workflow.md", "x\n")
	for _, class := range []string{"semantic", "procedural", "crystallized"} {
		writeRaw(t, root, "memory/"+class+"/_index.md", "---\ntitle: "+class+"\nkind: dir-index\nslug: _index\n---\n\n# "+class+"\n")
	}

	plan, err := PlanMocs(root, nil, time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	fact := plannedText(plan, MocRel("fact"))
	for _, want := range []string{
		"- [[agent/memory/semantic/shared-name|shared-name]] — shared-name",
		"- [[fact-0]] — fact-0",
	} {
		if !strings.Contains(fact, want) {
			t.Errorf("fact page lacks %q:\n%s", want, fact)
		}
	}
	memory := plannedText(plan, MocRel(MocMemorySlug))
	for _, want := range []string{
		"## Classes\n\n- [[agent/memory/semantic/_index|semantic]]\n- [[agent/memory/procedural/_index|procedural]]\n" +
			"- [[agent/memory/crystallized/_index|crystallized]]\n\n## Types\n\n- [[fact]] — 5 notes\n",
		// below the floor: listed in full, its own name unique
		"- workflow — 1 note, listed below",
		"## workflow\n\n- [[only-step]] — only-step",
	} {
		if !strings.Contains(memory, want) {
			t.Errorf("memory map lacks %q:\n%s", want, memory)
		}
	}
	if strings.Contains(memory, "members: 9") || !strings.Contains(memory, "members: 6") {
		t.Errorf("the class indexes were counted as members:\n%s", memory)
	}

	// A type page whose name another note shares is linked by its path.
	writeAt(t, vault, "projects/x/fact.md", "x\n")
	again, _ := PlanMocs(root, nil, time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC))
	if memory := plannedText(again, MocRel(MocMemorySlug)); !strings.Contains(memory, "- [[agent/memory/mocs/fact|fact]] — 5 notes") {
		t.Errorf("a shared type name stayed bare:\n%s", memory)
	}
}
