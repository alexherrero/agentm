package watch

import (
	"path/filepath"
	"testing"

	"github.com/fsnotify/fsnotify"

	"github.com/alexherrero/agentm/daemon/internal/config"
)

// The indexer has skipped dot directories during its walk since it was written,
// so `.tmp.driveupload` — the directory Google Drive stages every upload through
// — never reached the index. The notifier had no equivalent: it checked only the
// base name of the event path, and Drive's staging files are named like ordinary
// notes one level down. During the git-transport cutover's bulk upload that gap
// fed the commit path more than 1,400 files that existed for less than a debounce
// window.
func TestRelevant_DriveStagingChurnIsIgnored(t *testing.T) {
	vault := t.TempDir()
	w := New(&config.Config{VaultPath: vault}, nil, nil, nil)

	cases := []struct {
		name  string
		rel   string
		op    fsnotify.Op
		want  string // the path to index; "" means nothing is indexed
		index bool
		wake  bool // whether the event should start a debounce window
		why   string
	}{
		{
			name:  "an ordinary note",
			rel:   "personal/2026/08/a-note.md",
			op:    fsnotify.Write,
			want:  "personal/2026/08/a-note.md",
			index: true,
			wake:  true,
		},
		{
			name: "drive staging at the vault root",
			rel:  ".tmp.driveupload/3781.md",
			op:   fsnotify.Create,
			why:  "the base name looks like an ordinary note; only the path says otherwise",
		},
		{
			name: "drive staging beside a note",
			rel:  "personal/2026/08/.tmp.driveupload/3781.md",
			op:   fsnotify.Write,
			why:  "Drive stages beside the file it is replacing, not only at the root",
		},
		{
			name: "obsidian's own state",
			rel:  ".obsidian/workspace.md",
			op:   fsnotify.Write,
			why: "a dot directory never wakes the committer — a long Drive sync " +
				"would otherwise keep resetting the debounce and starve the commit. " +
				"Tracked files under one reach a commit via the reconcile tick.",
		},
		{
			name: "a dotfile",
			rel:  ".hidden.md",
			op:   fsnotify.Write,
		},
		{
			name: "a non-markdown file",
			rel:  "personal/attachment.png",
			op:   fsnotify.Write,
			wake: true,
			why: "not indexed — FTS5 has no use for a PNG — but a real change to a " +
				"tracked tree, so it wakes the committer, which then asks git what " +
				"is dirty. Before this split the same .md test answered both " +
				"questions, and every non-markdown file sat uncommitted forever " +
				"while the gate refused on it.",
		},
		{
			name: "a permission change on a real note",
			rel:  "personal/a-note.md",
			op:   fsnotify.Chmod,
			why:  "chmod does not change content, so there is nothing to commit",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			ev := fsnotify.Event{
				Name: filepath.Join(vault, filepath.FromSlash(tc.rel)),
				Op:   tc.op,
			}
			rel, index, wake := w.relevant(ev)
			if index != tc.index {
				t.Errorf("%s: indexed=%v, want %v.\n  %s", tc.rel, index, tc.index, tc.why)
			}
			if wake != tc.wake {
				t.Errorf("%s: woke the committer=%v, want %v.\n  %s",
					tc.rel, wake, tc.wake, tc.why)
			}
			if tc.want == "" {
				if rel != "" {
					t.Errorf("%s resolved to %q but must not be indexed.\n  %s",
						tc.rel, rel, tc.why)
				}
				return
			}
			if rel != tc.want {
				t.Errorf("%s was dropped or mis-resolved: rel=%q, want %q",
					tc.rel, rel, tc.want)
			}
		})
	}
}

// TestRelevant_ACreatedDotDirectoryIsNotWatched closes the loop on the same gap.
// relevant() returns ok=true only when it has just registered a watch for a new
// directory, so a dropped event is also proof no watch was added — which is what
// keeps the children of a staging directory out of the stream in the first place.
func TestRelevant_ACreatedDotDirectoryIsNotWatched(t *testing.T) {
	vault := t.TempDir()
	w := New(&config.Config{VaultPath: vault}, nil, nil, nil)

	ev := fsnotify.Event{
		Name: filepath.Join(vault, ".tmp.driveupload"),
		Op:   fsnotify.Create,
	}
	if rel, index, wake := w.relevant(ev); index || wake || rel != "" {
		t.Errorf("a staging directory was picked up as a new subtree to watch "+
			"(rel=%q, index=%v, wake=%v)", rel, index, wake)
	}
}

// TestRelevant_PathsOutsideTheVaultAreDropped guards the filepath.Rel result
// itself: Rel succeeds for a sibling directory and answers with "..", which is
// not a vault-relative path and must never be handed to the index.
func TestRelevant_PathsOutsideTheVaultAreDropped(t *testing.T) {
	vault := t.TempDir()
	w := New(&config.Config{VaultPath: vault}, nil, nil, nil)

	outside := filepath.Join(filepath.Dir(vault), "elsewhere", "a-note.md")
	if rel, index, wake := w.relevant(fsnotify.Event{Name: outside, Op: fsnotify.Write}); index || wake {
		t.Errorf("a path outside the vault was accepted as %q (index=%v, wake=%v)",
			rel, index, wake)
	}
}

func TestHasDotSegment(t *testing.T) {
	cases := map[string]bool{
		"personal/2026/a.md":                false,
		"a.md":                              false,
		".tmp.driveupload/1.md":             true,
		"personal/.tmp.driveupload/1.md":    true,
		"personal/.obsidian/plugins/x.md":   true,
		".hidden.md":                        true,
		"personal/note.with.dots.md":        false,
		"personal/2026/08/.trash/a-note.md": true,
	}
	for rel, want := range cases {
		if got := hasDotSegment(rel); got != want {
			t.Errorf("hasDotSegment(%q) = %v, want %v", rel, got, want)
		}
	}
}
