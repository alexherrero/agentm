package enrich

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

func withAliases(t *testing.T, body string, aliases ...string) string {
	t.Helper()
	r := Response{
		Title: "A note", Type: "fact", Altitude: "artifact",
		Body: body, Confidence: 0.9, Aliases: aliases,
	}
	b, err := json.Marshal(r)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// The rule that cost something to learn, enforced rather than documented.
func TestAnInventedAliasIsRejectedAtTheBatchTrigger(t *testing.T) {
	g := DefaultAliases()
	src := "Reciprocal rank fusion combines the lexical and dense arms."
	req := Request{Rel: "x.md", Raw: src, Trigger: TriggerBatch}

	err := g.Check(context.Background(), req,
		withAliases(t, src, "how do I make search better"))
	if err == nil {
		t.Fatal("an invented alias was written at the batch trigger")
	}
	// The rejection carries its evidence, so a future reader loosening the rule
	// knows what it costs.
	if !strings.Contains(err.Error(), "3.85") {
		t.Errorf("the rejection states a ban without its measurement: %v", err)
	}
	if !strings.Contains(err.Error(), "how do I make search better") {
		t.Errorf("the rejection does not name the alias: %v", err)
	}
}

// At the eager trigger there is an asker, and their words are evidence rather
// than invention. That is the entire difference between the two triggers.
func TestAskerPhrasingIsAcceptedAtTheEagerTriggerOnly(t *testing.T) {
	g := DefaultAliases()
	src := "Reciprocal rank fusion combines the lexical and dense arms."
	alias := "how do I make search better"

	eager := Request{
		Rel: "x.md", Raw: src, Trigger: TriggerEager,
		AskerPhrasing: "remember how do I make search better",
	}
	if err := g.Check(context.Background(), eager, withAliases(t, src, alias)); err != nil {
		t.Errorf("the asker's own words were rejected at the eager trigger: %v", err)
	}

	// The same alias, the same phrasing in the struct, at the batch trigger —
	// where there is no asker and the field is whatever happened to be there.
	batch := eager
	batch.Trigger = TriggerBatch
	if err := g.Check(context.Background(), batch, withAliases(t, src, alias)); err == nil {
		t.Error("asker phrasing was accepted at the batch trigger, where there is " +
			"no asker to have said it")
	}
}

// The three derivation routes, each with its OWN source so that exactly one route
// can account for it.
//
// A single shared fixture was the first version and it proved nothing: a source
// containing "Reciprocal rank fusion (RRF)" satisfies the presence route, the
// acronym route and the expansion route at once, so deleting any two left the
// suite green. The same pass found a fourth route that could never decide
// anything and it was deleted rather than given a fixture. Each case below is built so that removing its route is the only
// way to fail it.
func TestTheDerivationRoutes(t *testing.T) {
	for _, tc := range []struct {
		route, alias, src string
		want              bool
	}{
		{
			"present outright", "staging gate",
			"The staging gate runs before deployment.", true,
		},
		{
			// The expansion appears and the acronym does not, so only the
			// acronym route can account for it.
			"acronym of a phrase", "rrf",
			"Reciprocal rank fusion combines the two arms.", true,
		},
		{
			// The acronym appears and the expansion does not.
			"expansion of an acronym", "reciprocal rank fusion",
			"The RRF pass combines the two arms.", true,
		},
		{
			// Both words appear, in the other order and apart.
			"words all present, reordered", "gate staging",
			"The gate is checked during staging.", true,
		},
		{"invented entirely", "make my search faster",
			"The staging gate runs before deployment.", false},
		{"plausible but absent", "vector similarity",
			"The staging gate runs before deployment.", false},
	} {
		t.Run(tc.route, func(t *testing.T) {
			got := Derivable(tc.alias, tc.src, 3)
			if got != tc.want {
				t.Errorf("Derivable(%q, %q) = %v, want %v",
					tc.alias, tc.src, got, tc.want)
			}
		})
	}
}

// An acronym the note spells out is the same term a searcher will type, in
// either direction. Both directions matter: the note may contain either half.
func TestAcronymsResolveBothWays(t *testing.T) {
	if !Derivable("RRF", "Reciprocal rank fusion combines the arms.", 3) {
		t.Error("an acronym of a phrase in the note was called invented")
	}
	if !Derivable("reciprocal rank fusion", "The RRF pass combines the arms.", 3) {
		t.Error("the expansion of an acronym in the note was called invented")
	}
	// And a coincidence is not a derivation.
	if Derivable("XYZ", "Reciprocal rank fusion combines the arms.", 3) {
		t.Error("an unrelated acronym was accepted")
	}
}

// A very short alias is not worth a derivation argument either way — demanding
// it would reject legitimate rewrites over two letters.
func TestVeryShortAliasesArePermitted(t *testing.T) {
	if !Derivable("ML", "A note about something else entirely.", 3) {
		t.Error("a two-letter alias was rejected as invented")
	}
}

// No aliases is not a failure. A note with no structure to surface gets none,
// which is what the deterministic extractor at capture already does.
func TestNoAliasesPasses(t *testing.T) {
	g := DefaultAliases()
	if err := g.Check(context.Background(),
		Request{Rel: "x.md", Raw: "A note.", Trigger: TriggerBatch},
		withAliases(t, "A note.")); err != nil {
		t.Errorf("a response with no aliases was rejected: %v", err)
	}
}

// The cold scheduled backfill is banned structurally rather than by a check:
// there are two triggers and neither is it. This asserts the shape rather than
// a behaviour, because the ban is the absence of a third option.
func TestThereAreOnlyTwoTriggers(t *testing.T) {
	// If a third is ever added, this fails and whoever adds it has to decide
	// what the alias rule is for it — which is the conversation the −3.85
	// measurement exists to force.
	for _, tr := range []Trigger{TriggerEager, TriggerBatch} {
		if tr.String() == "" || strings.HasPrefix(tr.String(), "trigger(") {
			t.Errorf("trigger %d has no name", int(tr))
		}
	}
	if got := Trigger(2).String(); !strings.HasPrefix(got, "trigger(") {
		t.Errorf("a third trigger exists and is named %q; the cold scheduled "+
			"backfill is banned, and a new trigger needs its own alias rule", got)
	}
}

// A rejection leaves the note alone, like every other post-gate.
func TestAnInventedAliasLeavesTheNoteUnfiled(t *testing.T) {
	src := "The staging gate runs first."
	p := passWith(t, withAliases(t, src, "completely unrelated phrasing"))
	p.AddPost(DefaultAliases())

	out, err := p.Run(context.Background(), Request{
		Rel: "x.md", Raw: src, Trigger: TriggerBatch,
	})
	if err == nil {
		t.Fatal("an invented alias was written")
	}
	if out.Enriched {
		t.Error("a rejected response reported the note enriched")
	}
}
