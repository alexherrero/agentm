package enrich

import (
	"context"
	"fmt"
	"strings"
	"testing"
	"time"
)

func stampedResponse() Response {
	return Response{
		Title: "T", Type: "fact", Altitude: "artifact", Body: "b", Confidence: 0.9,
	}
}

// The three durable fields, all written. They are the one record of a judgment
// that survives losing the index, and the coverage ledger's rebuild reads them
// back — a note carrying only one of the three is a note whose row cannot be
// recovered.
func TestAStampedNoteCarriesAllThreeFields(t *testing.T) {
	at := time.Date(2026, 3, 14, 14, 30, 5, 0, time.UTC)
	out := RenderNote(stampedResponse(), Stamp{
		Version: "enrich/1+prompt/abc", RulesHash: "rh123", At: at,
	})
	// Read through the parser rather than off the raw bytes: `yamlScalar` quotes
	// a date, because an unquoted one stops being a string, and quoting is a
	// property of the encoder rather than of the stamp.
	for key, want := range map[string]string{
		"enriched_by": "enrich/1+prompt/abc",
		"rules_hash":  "rh123",
		"enriched_at": "2026-03-14T14:30:05Z",
		// The moment the stamp names, not a second clock read.
		"updated": "2026-03-14",
	} {
		if got := FrontmatterValue(out, key); got != want {
			t.Errorf("%s = %q, want %q:\n%s", key, got, want, out)
		}
	}
}

// A field with nothing to say says nothing. An empty `rules_hash` reads as
// "judged under no contract", which is never true, and a guessed `enriched_at`
// would be a claim about when something happened that nobody made.
func TestAnUnstampedNoteOmitsWhatItDoesNotKnow(t *testing.T) {
	out := RenderNote(stampedResponse(), Stamp{})
	for _, absent := range []string{"rules_hash:", "enriched_at:"} {
		if strings.Contains(out, absent) {
			t.Errorf("an unstamped note claims %q:\n%s", absent, out)
		}
	}
	// The version still lands, because every real caller wants the current pass
	// and a forgotten field should not produce a note claiming nothing wrote it.
	if !strings.Contains(out, "enriched_by: "+PassVersion) {
		t.Errorf("an unstamped note does not record the pass version:\n%s", out)
	}
}

// A pinned stamp renders byte-identically every time. This is what lets the
// ledger's key over a rendered note mean anything: two renders of one response
// that differed would hash differently and the row would never match.
func TestAPinnedStampRendersIdentically(t *testing.T) {
	s := Stamp{Version: "v1", RulesHash: "rh",
		At: time.Date(2026, 3, 14, 14, 30, 5, 0, time.UTC)}
	first := RenderNote(stampedResponse(), s)
	for i := 0; i < 20; i++ {
		if got := RenderNote(stampedResponse(), s); got != first {
			t.Fatalf("render %d differs:\n%s\n---\n%s", i, first, got)
		}
	}
}

// The stamp is readable back by the same parser that wrote it. The rebuild reads
// these three fields out of the corpus, and a value that survived the write but
// not the read would make every rebuilt row wrong in a way nothing would notice.
func TestStampsSurviveTheRoundTripThroughFrontmatter(t *testing.T) {
	at := time.Date(2026, 3, 14, 14, 30, 5, 0, time.UTC)
	out := RenderNote(stampedResponse(), Stamp{Version: "v1", RulesHash: "rh", At: at})

	if got := FrontmatterValue(out, "enriched_by"); got != "v1" {
		t.Errorf("enriched_by read back as %q", got)
	}
	if got := FrontmatterValue(out, "rules_hash"); got != "rh" {
		t.Errorf("rules_hash read back as %q", got)
	}
	got, err := time.Parse(StampFormat, FrontmatterValue(out, "enriched_at"))
	if err != nil {
		t.Fatalf("enriched_at will not parse: %v", err)
	}
	if !got.Equal(at) {
		t.Errorf("enriched_at read back as %s, want %s", got, at)
	}
}

// A split fragment carries the stamp too. Fragments are new notes enrichment
// wrote, so a rebuild has to be able to account for them the same way — and the
// parent edge has to survive the extra fields being there.
func TestSplitOutputCarriesTheStamp(t *testing.T) {
	at := time.Date(2026, 3, 14, 14, 30, 5, 0, time.UTC)
	s := Stamp{Version: "v1", RulesHash: "rh", At: at}

	frag := RenderFragment(SplitFragment{Slug: "part-one", Response: stampedResponse()},
		"memory/parent.md", s)
	for _, want := range []string{"derived_from: memory/parent.md",
		"enriched_by: v1", "rules_hash: rh", "enriched_at:"} {
		if !strings.Contains(frag, want) {
			t.Errorf("the fragment is missing %q:\n%s", want, frag)
		}
	}

	sup := SupersededNote("---\ntitle: T\n---\n\nbody\n",
		SplitPlan{Reason: "two subjects"}, []string{"a.md"}, s)
	for _, want := range []string{"status: superseded", "enriched_by: v1",
		"rules_hash: rh", "enriched_at:"} {
		if !strings.Contains(sup, want) {
			t.Errorf("the superseded note is missing %q:\n%s", want, sup)
		}
	}
}

// --- the observer -----------------------------------------------------------

// The observer is how the coverage ledger gets written without this package
// knowing a ledger exists. It has to fire for every outcome, or the outcomes it
// misses become rows nobody writes.
func TestTheObserverFiresForEveryOutcome(t *testing.T) {
	type call struct {
		req Request
		out Outcome
		err error
	}

	t.Run("skip", func(t *testing.T) {
		p := NewPass(newStubCaller(t, stubOpts{stdout: "{}"}), 1)
		p.SetEnabled(true)
		p.AddPre(declining{})
		var got []call
		p.SetObserver(func(r Request, o Outcome, e error) {
			got = append(got, call{r, o, e})
		})
		if _, err := p.Run(context.Background(),
			Request{Rel: "a.md", Raw: "raw"}); err != nil {
			t.Fatalf("Run: %v", err)
		}
		if len(got) != 1 {
			t.Fatalf("the observer fired %d times, want 1", len(got))
		}
		if !got[0].out.Skipped {
			t.Errorf("a declined note was not reported as skipped: %+v", got[0].out)
		}
		// The Request comes through, because an observer that only saw the
		// Outcome could not key the content that was read.
		if got[0].req.Raw != "raw" {
			t.Errorf("the observer did not receive the request body: %+v", got[0].req)
		}
	})

	t.Run("failure", func(t *testing.T) {
		// The failure comes from the caller rather than from a post-gate, so it
		// is a failure no gate configuration could turn into a success.
		p := NewPass(newStubCaller(t, stubOpts{exit: 1, stderr: "the model refused"}), 1)
		p.SetEnabled(true)
		var got []call
		p.SetObserver(func(r Request, o Outcome, e error) {
			got = append(got, call{r, o, e})
		})
		if _, err := p.Run(context.Background(),
			Request{Rel: "a.md", Raw: "raw"}); err == nil {
			t.Fatal("a run whose model call failed was reported as a success")
		}
		if len(got) != 1 {
			t.Fatalf("the observer fired %d times, want 1", len(got))
		}
		if got[0].err == nil {
			t.Error("the observer was told a failure was a success")
		}
	})

	t.Run("disabled", func(t *testing.T) {
		p := NewPass(newStubCaller(t, stubOpts{stdout: "{}"}), 1)
		var got int
		p.SetObserver(func(Request, Outcome, error) { got++ })
		if _, err := p.Run(context.Background(), Request{Rel: "a.md"}); err != nil {
			t.Fatal(err)
		}
		if got != 1 {
			t.Errorf("the observer fired %d times on a disabled pass, want 1 — a "+
				"disabled run is still an outcome the ledger has to be able to see", got)
		}
	})
}

// A pass with no observer runs exactly as before. The hook is optional, and a
// nil one must not be a panic in the middle of a batch that has already spent
// money.
func TestAPassWithNoObserverStillRuns(t *testing.T) {
	p := NewPass(newStubCaller(t, stubOpts{stdout: "{}"}), 1)
	p.SetEnabled(true)
	p.AddPre(declining{})
	if _, err := p.Run(context.Background(), Request{Rel: "a.md"}); err != nil {
		t.Fatalf("Run with no observer: %v", err)
	}
}

// declining is a pre-gate that always says no.
type declining struct{}

func (declining) Name() string { return "declining" }
func (declining) Check(context.Context, Request, string) error {
	return fmt.Errorf("%w: declined for the test", ErrNotEligible)
}
