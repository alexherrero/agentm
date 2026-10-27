package restated

import (
	"path/filepath"
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/fmlist"
)

// A merge names each card from the other as a quoted wikilink by vault path
// (task 193 step 7, LD-5), and step 6's parser reads it back as that card.
func TestAMergesLinksReadBackAsTheCards(t *testing.T) {
	vault := t.TempDir()
	sem := filepath.Join(vault, "agent", "memory", "semantic")
	writeNote(t, sem, "tests-are-sacred.md", sacred)
	writeNote(t, sem, "never-edit-or-delete-a-failing-test.md", neverEdit)
	notes, _ := Gather(vault, "agent")
	older, newer := Order(Pair{A: notes[0], B: notes[1]})
	survivor, superseded, err := Merge(older, newer, "2026-09-29")
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range []struct{ text, key, want string }{
		{survivor, "supersedes", newer.MemoryRel},
		{superseded, "superseded_by", older.MemoryRel},
	} {
		l, ok := fmlist.First(fmlist.LinksOf(c.text, c.key))
		if !ok || l.MemoryRel("agent") != c.want {
			t.Errorf("%s reads back as %+v, want %s", c.key, l, c.want)
		}
	}
}
