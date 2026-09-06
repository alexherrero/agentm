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

// The provenance ruling of 2026-09-06 split the field: `source:` names the
// transport a memory arrived by, and the unit it came from lives in
// `source_id:` (a registry identity) or `source_url:` (a fetched page). The
// reader prefers those and falls back to `source:` for the corpus that
// predates the ruling — writers strict, readers tolerant.
func TestTheReferenceFieldsWinOverTheLegacySource(t *testing.T) {
	at := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	for name, tc := range map[string]struct{ body, want string }{
		"source_id wins over both": {
			"---\nsource: conversation\nsource_id: email:<abc@example.com>\n" +
				"source_url: https://example.com/a\n---\n\nbody\n",
			"email:<abc@example.com>",
		},
		"source_url when there is no identity": {
			"---\nsource: external-fetch\nsource_url: https://example.com/a\n---\n\nbody\n",
			"https://example.com/a",
		},
		// The legacy shape: a URL sitting in the transport's field. Still read,
		// because refusing it would make the registry blind to the population
		// it exists to cover until the migration has run everywhere.
		"legacy source carries the unit": {
			"---\nsource: https://example.com/a\n---\n\nbody\n",
			"https://example.com/a",
		},
		// A migrated note: the transport alone. It names no unit, and reading
		// `conversation` as one would put a watermark on a source nothing can
		// ever match — but that is the registry's judgment, not this parser's,
		// so the parser reports what the field holds.
		"a transport is what the field holds": {
			"---\nsource: conversation\n---\n\nbody\n",
			"conversation",
		},
		"quoted reference": {
			"---\n" + `source_url: "https://example.com/a"` + "\n---\n\nbody\n",
			"https://example.com/a",
		},
		"an empty reference field falls through": {
			"---\nsource_id:\nsource: conversation\n---\n\nbody\n",
			"conversation",
		},
	} {
		t.Run(name, func(t *testing.T) {
			if got := Parse("a.md", tc.body, at).Source; got != tc.want {
				t.Errorf("Source = %q, want %q", got, tc.want)
			}
		})
	}
}
