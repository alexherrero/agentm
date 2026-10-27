package note

import (
	"reflect"
	"testing"
	"time"
)

// `derived_from` is read in every form it is written in (task 193 LD-5): a
// wikilink's brackets and label are not part of the entry it names.
func TestDerivedFromReadsTheWikilinkForm(t *testing.T) {
	for value, want := range map[string][]string{
		`[memory/episodic/a.md, memory/episodic/b.md]`: {"memory/episodic/a.md", "memory/episodic/b.md"},
		`["[[agent/memory/episodic/a|A]]", "[[b]]"]`:   {"agent/memory/episodic/a", "b"},
		`"[[agent/memory/episodic/a]]"`:                {"agent/memory/episodic/a"},
		`memory/episodic/a.md`:                         {"memory/episodic/a.md"},
	} {
		n := Parse("agent/memory/semantic/x.md", "---\ntitle: x\nderived_from: "+value+"\n---\n\nBody.\n", time.Time{})
		if !reflect.DeepEqual(n.DerivedFrom, want) {
			t.Errorf("derived_from %s → %q, want %q", value, n.DerivedFrom, want)
		}
	}
}
