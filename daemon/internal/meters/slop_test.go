package meters

import (
	"math"
	"testing"
)

// The slop signals. Every property here is one the live corpus taught, and two
// of them are properties the first implementation got backwards.

func near(t *testing.T, got, want float64, what string) {
	t.Helper()
	if math.Abs(got-want) > 0.001 {
		t.Errorf("%s = %.3f, want %.3f", what, got, want)
	}
}

// ── template residual ──────────────────────────────────────────────────────

func TestAHeadingWithContentUnderItIsTheNotesTitle(t *testing.T) {
	// The case that inverted the signal. A reference note is a title, a line and
	// a source — and the first version scored it 0.50 because half its lines were
	// its own heading, putting every complete short reference note at the bottom
	// of the corpus. That is the stratum the design says must not be flagged.
	body := "# DeepSeek-R1\n\nA reasoning model trained with RL.\n\nSource: https://example.test/r1\n"
	near(t, TemplateResidualOf(body), 1.0, "residual")
}

func TestAHeadingWithNothingUnderItIsSkeleton(t *testing.T) {
	// The case the signal exists for.
	body := "# Findings\n\n## Method\n\n## Results\n"
	near(t, TemplateResidualOf(body), 0.0, "residual")
}

func TestAHalfFilledTemplateScoresBetween(t *testing.T) {
	body := "# Findings\n\nWe measured 268 notes.\n\n## Method\n\n## Results\n"
	// Four counted lines: `# Findings` (filled), the sentence, `## Method`
	// (empty), `## Results` (empty). Two of four carry content.
	near(t, TemplateResidualOf(body), 0.5, "residual")
}

func TestAnEmptyBodyScoresZero(t *testing.T) {
	// Zero rather than one. There is no content beyond the skeleton because
	// there is nothing at all, and the design's auto-expire band is for this.
	near(t, TemplateResidualOf(""), 0, "residual")
	near(t, TemplateResidualOf("   \n\n  \n"), 0, "residual")
}

func TestUnfilledPlaceholdersAreSkeleton(t *testing.T) {
	for _, body := range []string{
		"# Notes\n\nTODO\n", "# Notes\n\nTBD\n", "# Notes\n\nFIXME\n",
	} {
		if got := TemplateResidualOf(body); got >= 1.0 {
			t.Errorf("%q scored %.2f — the placeholder counted as content", body, got)
		}
	}
}

func TestAnEmptyListMarkerIsSkeletonAndAFilledOneIsNot(t *testing.T) {
	empty := TemplateResidualOf("# H\n\nsomething real\n\n- \n- \n")
	filled := TemplateResidualOf("# H\n\nsomething real\n\n- a point\n- another\n")
	if !(empty < filled) {
		t.Fatalf("empty %.2f, filled %.2f — bullets with content are content",
			empty, filled)
	}
	near(t, filled, 1.0, "filled")
}

func TestProseIsNeverSkeleton(t *testing.T) {
	// A sentence that happens to begin with a word a template also uses is a
	// sentence. The regex is deliberately narrow because the failure that matters
	// is calling a real note empty.
	body := "Placeholder addressing is how the daemon resolves a port it does not own.\n"
	near(t, TemplateResidualOf(body), 1.0, "residual")
}

// ── shingles and novelty ───────────────────────────────────────────────────

func TestIdenticalBodiesHaveNoNovelty(t *testing.T) {
	text := "the push always goes through even when the tool is missing entirely"
	near(t, Jaccard(Shingles(text), Shingles(text)), 1.0, "jaccard")
}

func TestUnrelatedBodiesOverlapNotAtAll(t *testing.T) {
	a := Shingles("the push always goes through when a tool is missing")
	b := Shingles("phyllotaxis initialization makes the layout deterministic")
	near(t, Jaccard(a, b), 0, "jaccard")
}

func TestATooShortBodyOverlapsNothing(t *testing.T) {
	// Zero rather than one. Two notes too short to shingle have not been shown to
	// be similar, and returning 1.0 would report every pair of them as copies.
	near(t, Jaccard(Shingles("two words"), Shingles("two words")), 0, "jaccard")
	near(t, Jaccard(Shingles(""), Shingles("anything at all here")), 0, "jaccard")
}

func TestASharedSentenceShowsUpAsOverlap(t *testing.T) {
	a := Shingles("a completed unit of work is never hard-stopped by a missing tool")
	b := Shingles("a completed unit of work is never hard-stopped by a missing binary")
	got := Jaccard(a, b)
	if got < 0.5 {
		t.Fatalf("jaccard %.2f — one word changed and the overlap vanished", got)
	}
	if got >= 1.0 {
		t.Fatalf("jaccard %.2f — one word changed and nothing moved", got)
	}
}

// ── scoring the corpus ─────────────────────────────────────────────────────

func TestALoneNoteIsFullyNovel(t *testing.T) {
	// True rather than convenient: there is nothing it repeats.
	got := Score([]Scorable{{Rel: "a.md", Body: "some words about a subject here"}})
	near(t, got[0].Novelty, 1.0, "novelty")
	if got[0].NearestRel != "" {
		t.Errorf("NearestRel = %q with nothing to compare against", got[0].NearestRel)
	}
}

func TestAnEmptyCorpusScoresNothing(t *testing.T) {
	if got := Score(nil); len(got) != 0 {
		t.Fatalf("Score(nil) = %v", got)
	}
}

func TestANearCopyNamesWhatItCopies(t *testing.T) {
	// A low score nobody can check is a number nobody can act on.
	body := "the push always goes through even when the gh tool is entirely missing"
	got := Score([]Scorable{
		{Rel: "a.md", Body: body},
		{Rel: "b.md", Body: body},
		{Rel: "far.md", Body: "phyllotaxis seeding keeps the drawn layout stable"},
	})
	byRel := map[string]Signals{}
	for _, s := range got {
		byRel[s.Rel] = s
	}
	if byRel["a.md"].NearestRel != "b.md" {
		t.Errorf("a.md points at %q, want b.md", byRel["a.md"].NearestRel)
	}
	near(t, byRel["a.md"].Novelty, 0, "novelty of a copy")
	near(t, byRel["far.md"].Novelty, 1.0, "novelty of the unrelated note")
}

func TestTwoRunsAgree(t *testing.T) {
	notes := []Scorable{
		{Rel: "z.md", Body: "one body of text about a particular subject"},
		{Rel: "a.md", Body: "one body of text about a different subject"},
		{Rel: "m.md", Body: "something else entirely unrelated to those two"},
	}
	rev := []Scorable{notes[2], notes[1], notes[0]}
	first, second := Score(notes), Score(rev)
	if len(first) != len(second) {
		t.Fatalf("%d then %d", len(first), len(second))
	}
	for i := range first {
		if first[i] != second[i] {
			t.Fatalf("row %d: %+v vs %+v", i, first[i], second[i])
		}
	}
}

func TestScoresComeBackInPathOrder(t *testing.T) {
	got := Score([]Scorable{
		{Rel: "z.md", Body: "alpha beta gamma delta"},
		{Rel: "a.md", Body: "epsilon zeta eta theta"},
		{Rel: "m.md", Body: "iota kappa lambda mu"},
	})
	for i, want := range []string{"a.md", "m.md", "z.md"} {
		if got[i].Rel != want {
			t.Fatalf("row %d is %q, want %q", i, got[i].Rel, want)
		}
	}
}

func TestWordCountIsReportedAndDecidesNothing(t *testing.T) {
	// The length floor is an AND-gate, never alone: "this vault's best notes are
	// often its shortest". A short, dense, unique note must score clean on both
	// real signals — the number is there to be combined, not to judge.
	got := Score([]Scorable{
		{Rel: "short.md", Body: "# Metal buffers\n\nThey page-fault above ~2k " +
			"tokens and poison the server; chunk instead.\n"},
		{Rel: "other.md", Body: "an entirely different subject with no overlap"},
	})
	for _, s := range got {
		if s.Rel != "short.md" {
			continue
		}
		near(t, s.TemplateResidual, 1.0, "residual of a short dense note")
		near(t, s.Novelty, 1.0, "novelty of a short dense note")
		// Counted, not merely bounded. A first version only checked it was not
		// too large, so setting it to zero passed — and a length AND-gate reading
		// zero for every note is a gate that always agrees.
		if s.Words != 14 {
			t.Errorf("Words = %d, want 14", s.Words)
		}
	}
}

func TestNothingScoredIsAVerdict(t *testing.T) {
	// The type carries numbers and no decision. Bands live with the staging
	// machinery, so a scoring change cannot alter what gets deleted without
	// somebody reviewing the band.
	got := Score([]Scorable{{Rel: "a.md", Body: "some words here about things"}})
	if len(got) != 1 {
		t.Fatal("expected one row")
	}
	// If a verdict field is ever added, this is where it has to be argued for.
	_ = got[0].TemplateResidual
	_ = got[0].Novelty
	_ = got[0].Words
}
