package extract

import "testing"

func targets(links []Link) []string {
	out := make([]string, 0, len(links))
	for _, l := range links {
		out = append(out, l.Target)
	}
	return out
}

// Both forms, because the corpus uses both: Obsidian writes wikilinks and
// everything generated writes markdown links. An extractor that read one form
// would report half the graph — and half a graph is worse than none, because it
// looks complete.
func TestBothLinkFormsAreRead(t *testing.T) {
	got := Links("See [[capture]] and also [the design](wiki/designs/filing.md).\n")
	eq(t, targets(got), []string{"capture", "wiki/designs/filing.md"})
}

func TestPipedWikilinkKeepsTargetAndDisplaySeparate(t *testing.T) {
	got := Links("See [[capture|the capture path]].\n")
	if len(got) != 1 {
		t.Fatalf("got %d links", len(got))
	}
	if got[0].Target != "capture" {
		t.Errorf("target %q, want capture", got[0].Target)
	}
	if got[0].Text != "the capture path" {
		t.Errorf("text %q, want the display text", got[0].Text)
	}
}

func TestBareWikilinkUsesTheTargetAsItsText(t *testing.T) {
	got := Links("See [[capture]].\n")
	if got[0].Text != "capture" {
		t.Errorf("text %q, want capture", got[0].Text)
	}
}

func TestAnchorIsStrippedFromTheTarget(t *testing.T) {
	got := Links("See [[capture#the-transaction]] and [x](notes/a.md#section).\n")
	eq(t, targets(got), []string{"capture", "notes/a.md"})
}

// A backlink without context tells you two notes are connected and nothing about
// how, which is most of what makes a backlink worth having.
func TestContextIsTheSurroundingLine(t *testing.T) {
	got := Links("Filing halts when the rules break — see [[storage-rules]] for why.\n")
	if got[0].Context != "Filing halts when the rules break — see [[storage-rules]] for why." {
		t.Errorf("context %q", got[0].Context)
	}
}

// A link inside a code block is a sample, not a reference. Indexing it would
// connect a page to whatever its examples happen to mention.
func TestLinksInFencedCodeAreSkipped(t *testing.T) {
	body := "Real [[one]].\n\n```markdown\nExample [[not-a-real-link]] here.\n```\n\nAlso [[two]].\n"
	got := Links(body)
	eq(t, targets(got), []string{"one", "two"})
}

// The backlink index is about the shape of the corpus, and a URL is not part of
// it.
func TestExternalTargetsAreNotLinks(t *testing.T) {
	body := "See [the docs](https://example.com/x) and [[https://example.com/y]].\n"
	if got := Links(body); len(got) != 0 {
		t.Errorf("external targets were indexed: %v", targets(got))
	}
}

// An image embed is not a reference between notes.
func TestImageEmbedsAreNotLinks(t *testing.T) {
	got := Links("![a diagram](assets/diagram.png)\n")
	if len(got) != 0 {
		t.Errorf("an image was indexed as a link: %v", targets(got))
	}
}

func TestPureAnchorLinksAreSkipped(t *testing.T) {
	got := Links("Jump to [the section](#the-section).\n")
	if len(got) != 0 {
		t.Errorf("a same-page anchor was indexed: %v", targets(got))
	}
}

func TestRepeatedIdenticalLinksAreDeduped(t *testing.T) {
	got := Links("[[a]] and [[a]] again.\n")
	eq(t, targets(got), []string{"a"})
}

// The same target under two different display texts is two facts about the
// graph, not one — the context differs, and that is what a reader wants.
func TestSameTargetWithDifferentTextIsKeptTwice(t *testing.T) {
	got := Links("[[a|first]] then [[a|second]].\n")
	if len(got) != 2 {
		t.Fatalf("got %d links, want both display texts kept: %v", len(got), got)
	}
}

// ── resolution ──────────────────────────────────────────────────────────────

func TestExactPathWins(t *testing.T) {
	known := []string{"memory/semantic/capture.md", "wiki/reference/capture.md"}
	got := ResolveTarget("wiki/reference/capture.md", "notes/x.md", known)
	if got != "wiki/reference/capture.md" {
		t.Errorf("got %q", got)
	}
}

// The disambiguation the design calls for: a target written with more path than
// a bare name is more specific, and the candidate matching more of it is meant.
func TestLongerPathSuffixWins(t *testing.T) {
	known := []string{"memory/semantic/capture.md", "wiki/reference/capture.md"}
	got := ResolveTarget("reference/capture", "notes/x.md", known)
	if got != "wiki/reference/capture.md" {
		t.Errorf("got %q, want the two-segment match", got)
	}
}

// A bare basename with two candidates: the nearer one wins, because a link is
// far more likely to mean the sibling than the far-away file with the same name.
func TestAmbiguousBasenameBreaksTowardTheSibling(t *testing.T) {
	known := []string{"memory/semantic/capture.md", "wiki/reference/capture.md"}
	got := ResolveTarget("capture", "wiki/reference/other.md", known)
	if got != "wiki/reference/capture.md" {
		t.Errorf("got %q, want the sibling", got)
	}
}

func TestTheMdSuffixIsOptionalInTheTarget(t *testing.T) {
	known := []string{"memory/semantic/capture.md"}
	for _, target := range []string{"capture", "capture.md", "semantic/capture"} {
		if got := ResolveTarget(target, "x.md", known); got != "memory/semantic/capture.md" {
			t.Errorf("%q resolved to %q", target, got)
		}
	}
}

// A dangling link is a fact about the corpus and the caller records it. Dropping
// it would lose exactly what the stub synthesis in a later part reads.
func TestAnUnresolvableTargetReturnsEmpty(t *testing.T) {
	known := []string{"memory/semantic/capture.md"}
	if got := ResolveTarget("nothing-like-this", "x.md", known); got != "" {
		t.Errorf("got %q, want empty", got)
	}
}

func TestResolutionIsCaseInsensitive(t *testing.T) {
	known := []string{"memory/semantic/Capture.md"}
	if got := ResolveTarget("capture", "x.md", known); got != "memory/semantic/Capture.md" {
		t.Errorf("got %q", got)
	}
}

func TestEmptyTargetResolvesToNothing(t *testing.T) {
	if got := ResolveTarget("  ", "x.md", []string{"a.md"}); got != "" {
		t.Errorf("got %q", got)
	}
}

// A basename must not match a path segment in the middle: `[[semantic]]` does not
// mean `memory/semantic/capture.md`.
func TestASegmentInTheMiddleIsNotAMatch(t *testing.T) {
	known := []string{"memory/semantic/capture.md"}
	if got := ResolveTarget("semantic", "x.md", known); got != "" {
		t.Errorf("got %q; a middle segment is not a target", got)
	}
}
