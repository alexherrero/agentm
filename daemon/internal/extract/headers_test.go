package extract

import (
	"strings"
	"testing"
)

func paths(chunks []HeaderChunk) []string {
	out := make([]string, 0, len(chunks))
	for _, c := range chunks {
		out = append(out, c.HeaderPath)
	}
	return out
}

func eq(t *testing.T, got, want []string) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("got %d chunks %v, want %d %v", len(got), got, len(want), want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("chunk %d: header path %q, want %q", i, got[i], want[i])
		}
	}
}

// The overwhelming majority of captures are a paragraph with no structure. They
// must come out as one chunk carrying no path, which is what keeps this
// compatible with everything written before chunking existed.
func TestNoHeadingsIsOneChunk(t *testing.T) {
	got := HeaderChunks("Filing is a frontmatter edit, so nothing moves.\n")
	if len(got) != 1 {
		t.Fatalf("got %d chunks, want 1", len(got))
	}
	if got[0].HeaderPath != "" {
		t.Errorf("header path %q, want empty", got[0].HeaderPath)
	}
	if !strings.Contains(got[0].Content, "frontmatter edit") {
		t.Errorf("content lost: %q", got[0].Content)
	}
}

func TestEmptyBodyProducesNothing(t *testing.T) {
	if got := HeaderChunks(""); got != nil {
		t.Errorf("empty body produced %v", got)
	}
	if got := HeaderChunks("\n\n   \n"); got != nil {
		t.Errorf("whitespace-only body produced %v", got)
	}
}

func TestNestedHeadingsBuildTheAncestry(t *testing.T) {
	body := `# Architecture

Overview text.

## Ingestion Pipeline

How capture works.

### Chunking

Split along headers.

## Retrieval

How search works.
`
	got := HeaderChunks(body)
	eq(t, paths(got), []string{
		"Architecture",
		"Architecture > Ingestion Pipeline",
		"Architecture > Ingestion Pipeline > Chunking",
		"Architecture > Retrieval",
	})
}

// Content before the first heading is a real section of the note and belongs in
// a chunk of its own, not folded into whatever heading happens to follow it.
func TestPreambleBeforeTheFirstHeadingIsItsOwnChunk(t *testing.T) {
	body := "Some framing before any heading.\n\n# First\n\nBody.\n"
	got := HeaderChunks(body)
	eq(t, paths(got), []string{"", "First"})
	if !strings.Contains(got[0].Content, "framing") {
		t.Errorf("preamble content lost: %q", got[0].Content)
	}
}

// A chunk read on its own should still say what it is about.
func TestAChunkKeepsItsOwnHeadingLine(t *testing.T) {
	got := HeaderChunks("# Architecture\n\nOverview.\n")
	if !strings.HasPrefix(got[0].Content, "# Architecture") {
		t.Errorf("the heading line is missing from its own chunk: %q", got[0].Content)
	}
}

// The failure this guards is not subtle: a chunker that split on a `#` inside a
// fenced block would cut the block in half and label the remainder a section.
func TestHashInsideAFenceIsNotAHeading(t *testing.T) {
	body := "# Real\n\n```bash\n# this is a shell comment\necho hi\n```\n\nMore body.\n"
	got := HeaderChunks(body)
	eq(t, paths(got), []string{"Real"})
	if !strings.Contains(got[0].Content, "echo hi") {
		t.Errorf("the fenced block was cut: %q", got[0].Content)
	}
}

func TestTildeFencesAreTrackedToo(t *testing.T) {
	body := "# Real\n\n~~~\n# not a heading\n~~~\n\nMore.\n"
	got := HeaderChunks(body)
	eq(t, paths(got), []string{"Real"})
}

// A jump from `#` to `###` should describe the document as written. Inventing an
// intermediate heading would be a claim about structure the author never made.
func TestASkippedLevelDoesNotInventAHeading(t *testing.T) {
	body := "# Top\n\nA.\n\n### Deep\n\nB.\n"
	got := HeaderChunks(body)
	eq(t, paths(got), []string{"Top", "Top > Deep"})
}

// Going back up a level must drop the deeper ancestry rather than keep it.
func TestReturningToAShallowerLevelTruncatesTheAncestry(t *testing.T) {
	body := "# A\n\n1.\n\n## B\n\n2.\n\n### C\n\n3.\n\n# D\n\n4.\n"
	got := HeaderChunks(body)
	eq(t, paths(got), []string{"A", "A > B", "A > B > C", "D"})
}

// An empty section contributes nothing. A heading with no body under it is
// navigation, not content, and a chunk holding only a heading line would compete
// for a top slot on the strength of its title alone.
func TestAHeadingWithNoBodyStillChunksItsOwnLine(t *testing.T) {
	body := "# A\n\n## B\n\nOnly B has text.\n"
	got := HeaderChunks(body)
	// `# A` carries its own heading line, so it is not empty — but it must not
	// swallow B's text.
	eq(t, paths(got), []string{"A", "A > B"})
	if strings.Contains(got[0].Content, "Only B") {
		t.Errorf("section A swallowed B's body: %q", got[0].Content)
	}
}

// Nothing may be silently dropped: every non-heading line of the input has to
// appear in exactly one chunk.
func TestNoContentIsLost(t *testing.T) {
	body := `Preamble line.

# One

Alpha line.

## Two

Beta line.

` + "```\nfenced line\n```" + `

# Three

Gamma line.
`
	got := HeaderChunks(body)
	joined := ""
	for _, c := range got {
		joined += c.Content + "\n"
	}
	for _, want := range []string{
		"Preamble line.", "Alpha line.", "Beta line.", "fenced line", "Gamma line.",
	} {
		if !strings.Contains(joined, want) {
			t.Errorf("%q is in no chunk", want)
		}
		if strings.Count(joined, want) != 1 {
			t.Errorf("%q appears %d times across chunks, want once",
				want, strings.Count(joined, want))
		}
	}
}

func TestHeaderChunksAreDeterministic(t *testing.T) {
	body := "# A\n\n1.\n\n## B\n\n2.\n"
	first := HeaderChunks(body)
	for i := 0; i < 10; i++ {
		again := HeaderChunks(body)
		if len(again) != len(first) {
			t.Fatalf("run %d produced %d chunks, first produced %d", i, len(again), len(first))
		}
		for j := range first {
			if again[j] != first[j] {
				t.Fatalf("run %d differs at %d", i, j)
			}
		}
	}
}
