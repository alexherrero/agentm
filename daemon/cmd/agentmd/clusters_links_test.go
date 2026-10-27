package main

import "testing"

// A cluster's provenance reads a `derived_from` entry in every form (task 193
// LD-5): the memory-root path and the wikilink's vault path are one unit.
func TestDerivedUnitReadsBothPathForms(t *testing.T) {
	for _, c := range []struct{ entry, want string }{
		{"memory/episodic/2026-10-01-trace.md", "memory/episodic/2026-10-01-trace.md"},
		{"agent/memory/episodic/2026-10-01-trace", "memory/episodic/2026-10-01-trace.md"},
		{"2026-10-01-trace", "2026-10-01-trace"},
	} {
		if got := derivedUnit(c.entry, "agent"); got != c.want {
			t.Errorf("derivedUnit(%q) = %q, want %q", c.entry, got, c.want)
		}
	}
}
