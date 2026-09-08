package note

import "testing"

// The name is spelled with an escape, not typed: a real carriage return does
// not survive an editor or a clipboard, and a constant that arrived here as
// "Icon" would match nothing on disk while looking right in the diff.
func TestSyncArtifactMatchesWhatTheSyncLayersWrite(t *testing.T) {
	for _, name := range []string{"Icon\r", "Icon", ".DS_Store"} {
		if !SyncArtifact(name) {
			t.Errorf("SyncArtifact(%q) = false; this is a file Drive or Finder writes", name)
		}
	}
}

// The prefix test this replaced also skipped these. A note about iconography
// is a note.
func TestSyncArtifactLeavesContentAlone(t *testing.T) {
	for _, name := range []string{
		"Iconography.md", "Icons.md", "Icon-design.md",
		"a-durable-fact.md", "_index.md", "",
	} {
		if SyncArtifact(name) {
			t.Errorf("SyncArtifact(%q) = true; that is a note", name)
		}
	}
}
