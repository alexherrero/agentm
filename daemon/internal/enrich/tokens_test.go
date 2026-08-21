package enrich

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

func respond(t *testing.T, body string) string {
	t.Helper()
	r := Response{
		Title: "A note", Type: "fact", Altitude: "artifact",
		Body: body, Confidence: 0.9,
	}
	b, err := json.Marshal(r)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// One case per token class, because a gate that catches four of five is a gate
// whose gap nobody knows about until the fifth kind of thing goes missing.
func TestEachTokenClassIsPreserved(t *testing.T) {
	for _, tc := range []struct {
		class, source, rewrite string
	}{
		{
			"identifier",
			"Set `idx_timestamp_desc` before the pass runs.",
			"Configure the index ordering before the pass runs.",
		},
		{
			// Bare, with no backticks. The backtick extractor would catch the
			// case above on its own, so without this one neither extractor is
			// proved independently and removing either leaves the suite green.
			"bare identifier",
			"The daemon reads plugins.obsidian-vault.memory_root at boot.",
			"The daemon reads its configured root at boot.",
		},
		{
			// Backticked and *not* identifier-shaped, for the same reason in
			// reverse: a flag with a leading dash matches no identifier pattern.
			"backticked flag",
			"Pass `--dry-run` to see what the queue would offer.",
			"Use the dry run option to see what the queue would offer.",
		},
		{
			"CamelCase symbol",
			"Call SetDampenedSpaces at boot.",
			"Configure the dampened spaces at boot.",
		},
		{
			"number",
			"The sweep covered 125 points across the range.",
			"The sweep covered many points across the range.",
		},
		{
			"date",
			"The migration ran on 2026-08-10 and rewrote the tree.",
			"The migration ran last month and rewrote the tree.",
		},
		{
			"URL",
			"See https://example.com/docs/gate for the detail.",
			"See the documentation for the detail.",
		},
		{
			"acronym",
			"Ranking uses RRF over the two arms.",
			"Ranking fuses the two arms.",
		},
	} {
		t.Run(tc.class, func(t *testing.T) {
			g := DefaultTokens()
			req := Request{Rel: "x.md", Raw: tc.source}

			if err := g.Check(context.Background(), req, respond(t, tc.source)); err != nil {
				t.Fatalf("an unchanged body was rejected, so this case proves "+
					"nothing about dropping: %v", err)
			}
			err := g.Check(context.Background(), req, respond(t, tc.rewrite))
			if err == nil {
				t.Errorf("a rewrite dropping the %s was accepted", tc.class)
			}
		})
	}
}

// The rejection has to say what went missing. "Dropped 3 tokens" sends the
// reader to diff two blobs by hand.
func TestTheRejectionNamesWhatWasDropped(t *testing.T) {
	g := DefaultTokens()
	req := Request{Raw: "Set `idx_timestamp_desc` before 2026-08-10."}
	err := g.Check(context.Background(), req, respond(t, "Set the ordering beforehand."))
	if err == nil {
		t.Fatal("the drop was accepted")
	}
	for _, want := range []string{"idx_timestamp_desc", "2026-08-10"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the rejection does not name %q: %v", want, err)
		}
	}
}

// A token that moved into the title or an alias is still present, still indexed,
// and still findable. Requiring it in the body would be enforcing where the
// writer put it rather than that it survived.
func TestATokenThatMovedIntoAFieldStillCounts(t *testing.T) {
	g := DefaultTokens()
	req := Request{Raw: "The flag is `enrich_enabled` and it defaults off."}
	r := Response{
		Title: "The enrich_enabled flag", Type: "fact", Altitude: "artifact",
		Body: "The flag defaults to off.", Confidence: 0.9,
		Aliases: []string{"enrichment toggle"},
	}
	b, _ := json.Marshal(r)
	if err := g.Check(context.Background(), req, string(b)); err != nil {
		t.Errorf("a token preserved in the title was counted as dropped: %v", err)
	}
}

// Preservation is presence, not position. A rewrite is allowed to reorder and
// rephrase — that is the job — so long as nothing findable disappears.
func TestReorderingAndRephrasingArePermitted(t *testing.T) {
	g := DefaultTokens()
	source := "On 2026-08-10 the migration rewrote 9899 notes, setting `status` " +
		"to unfiled across the corpus."
	rewrite := "Setting `status` to unfiled, the migration touched 9899 notes; " +
		"it ran on 2026-08-10."
	if err := g.Check(context.Background(), Request{Raw: source}, respond(t, rewrite)); err != nil {
		t.Errorf("a faithful rephrasing was rejected: %v", err)
	}
}

// Case matters for code and not for prose. `SetDampenedSpaces` lowercased is a
// broken reference; "Antigravity" lowercased mid-sentence is ordinary writing.
func TestCaseMattersForCodeAndNotForProse(t *testing.T) {
	g := DefaultTokens()

	code := Request{Raw: "Call SetDampenedSpaces at boot."}
	if err := g.Check(context.Background(), code,
		respond(t, "Call setdampenedspaces at boot.")); err == nil {
		t.Error("a lowercased code symbol was accepted; that is a broken reference")
	}

	prose := Request{Raw: "The work moved from Antigravity to Claude."}
	if err := g.Check(context.Background(), prose,
		respond(t, "The work moved from antigravity to claude.")); err != nil {
		t.Errorf("a case change in ordinary prose was rejected: %v", err)
	}
}

// A single digit survives a rewrite as a word — "3 gates" becoming "three
// gates" is good writing, and failing it would be enforcing style.
func TestSingleDigitsAreNotDistinctive(t *testing.T) {
	g := DefaultTokens()
	req := Request{Raw: "There are 3 gates before the call."}
	if err := g.Check(context.Background(), req,
		respond(t, "There are three gates before the call.")); err != nil {
		t.Errorf("spelling out a single digit was rejected: %v", err)
	}
}

// A word is not a name because it opened a sentence. Treating it as one would
// make every rewritten opening a failure.
func TestASentenceOpenerIsNotAName(t *testing.T) {
	got := Distinctive("Deployment happens after the gate. Staging runs first.")
	for _, tok := range got {
		if tok == "Deployment" || tok == "Staging" {
			t.Errorf("%q was treated as a name because it opened a sentence: %v",
				tok, got)
		}
	}
}

// Frontmatter is not the source prose. The rewrite *replaces* the frontmatter,
// so requiring its keys to survive would fail every note.
func TestFrontmatterIsNotComparedAgainst(t *testing.T) {
	g := DefaultTokens()
	raw := "---\ntitle: Old Title\nstatus: unfiled\ncaptured: 2026-08-10T05:00:00Z\n" +
		"---\n\nThe gate runs first.\n"
	if err := g.Check(context.Background(), Request{Raw: raw},
		respond(t, "The gate runs first.")); err != nil {
		t.Errorf("frontmatter was compared as source prose: %v", err)
	}
}

// The extractor is inspectable, because a rejection quotes its output and a
// reader who cannot reproduce the list cannot tell a real drop from a bad
// extractor.
func TestDistinctiveFindsWhatItClaims(t *testing.T) {
	got := Distinctive("Set `idx_timestamp_desc` on 2026-08-10; see " +
		"https://example.com/x and call SetDampenedSpaces with 125 points. RRF fuses.")
	want := []string{
		"idx_timestamp_desc", "2026-08-10", "https://example.com/x",
		"SetDampenedSpaces", "125", "RRF",
	}
	for _, w := range want {
		found := false
		for _, g := range got {
			if g == w {
				found = true
			}
		}
		if !found {
			t.Errorf("Distinctive missed %q; it found %v", w, got)
		}
	}
}

// The gate runs on every note rather than a sample, and the reason is that a
// sample lets through exactly the note whose identifier was dropped.
func TestTheGateRunsOnEveryNoteThroughThePass(t *testing.T) {
	source := "Set `idx_timestamp_desc` before the pass runs."
	p := passWith(t, respond(t, "Configure the ordering beforehand."))
	p.AddPost(DefaultTokens())

	out, err := p.Run(context.Background(), Request{Rel: "x.md", Raw: source})
	if err == nil {
		t.Fatal("a response dropping an identifier was written")
	}
	if out.Enriched {
		t.Error("a rejected response reported the note enriched")
	}
	if !strings.Contains(out.Reason, "idx_timestamp_desc") {
		t.Errorf("the outcome does not say what was dropped: %q", out.Reason)
	}
}
