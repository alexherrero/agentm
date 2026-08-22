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
