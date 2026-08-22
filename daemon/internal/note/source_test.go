package note

import (
	"testing"
	"time"
)

// A memory names the unit it was distilled from, and the whole source-scoped
// half of re-ingestion reads that field. Nothing asserted it was parsed.
func TestSourceIsReadFromFrontmatter(t *testing.T) {
	for name, tc := range map[string]struct{ body, want string }{
		"plain": {
			"---\ntitle: A\nsource: email:<abc@example.com>\n---\n\nbody\n",
			"email:<abc@example.com>",
		},
		"quoted": {
			`---` + "\n" + `source: "https://example.com/a"` + "\n---\n\nbody\n",
			"https://example.com/a",
		},
		"absent": {"---\ntitle: A\n---\n\nbody\n", ""},
		// A note whose prose mentions a source is talking about one, not
		// carrying one. The frontmatter block is where the field lives.
		"in the body only": {
			"---\ntitle: A\n---\n\nsource: email:<not-frontmatter@example.com>\n",
			"",
		},
	} {
		t.Run(name, func(t *testing.T) {
			n := Parse("a.md", tc.body, time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC))
			if n.Source != tc.want {
				t.Errorf("Source = %q, want %q", n.Source, tc.want)
			}
		})
	}
}

// The rest of the provenance: what the source contained when it was read, and
// the pass that read it. These are what make the source registry rebuildable
// from the corpus rather than being the only copy of what has been mined.
func TestSourceProvenanceIsReadFromFrontmatter(t *testing.T) {
	body := "---\ntitle: A\nsource: email:<abc@example.com>\n" +
		"source_hash: abc123\nsource_version: ingest/1\n---\n\nbody\n"
	n := Parse("a.md", body, time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC))
	if n.SourceHash != "abc123" {
		t.Errorf("SourceHash = %q; a rebuild would recover the name of this "+
			"source and not whether it can be skipped", n.SourceHash)
	}
	if n.SourceVersion != "ingest/1" {
		t.Errorf("SourceVersion = %q", n.SourceVersion)
	}

	// Absent is empty rather than invented. Most of the corpus predates these
	// fields entirely.
	bare := Parse("b.md", "---\nsource: email:<x@example.com>\n---\n\nb\n",
		time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC))
	if bare.SourceHash != "" || bare.SourceVersion != "" {
		t.Errorf("a note predating these fields reports %q/%q",
			bare.SourceHash, bare.SourceVersion)
	}
}
