package enrich

import (
	"context"
	"errors"
	"strings"
	"sync"
	"testing"
)

// stubJudge answers however the test needs, and records what it was asked.
type stubJudge struct {
	mu       sync.Mutex
	prompts  []string
	verdicts []Verdict
	err      error
}

func (j *stubJudge) Judge(_ context.Context, prompt string) (Verdict, error) {
	j.mu.Lock()
	defer j.mu.Unlock()
	j.prompts = append(j.prompts, prompt)
	if j.err != nil {
		return Verdict{}, j.err
	}
	if len(j.verdicts) == 0 {
		return Verdict{Grounded: true}, nil
	}
	v := j.verdicts[0]
	if len(j.verdicts) > 1 {
		j.verdicts = j.verdicts[1:]
	}
	return v, nil
}

func (j *stubJudge) asked() int {
	j.mu.Lock()
	defer j.mu.Unlock()
	return len(j.prompts)
}

func TestAGroundedRewritePasses(t *testing.T) {
	j := &stubJudge{verdicts: []Verdict{{Grounded: true}}}
	g := &Grounding{Judge: j}
	err := g.Check(context.Background(),
		Request{Rel: "x.md", Raw: "The gate runs first."},
		respond(t, "The gate runs before anything else."))
	if err != nil {
		t.Errorf("a grounded rewrite was rejected: %v", err)
	}
}

// The failure this gate exists for: the model filled a gap with something
// plausible. Silent and permanent, because the note now asserts something nobody
// wrote and the raw text is one commit back where nobody looks.
func TestAnUngroundedClaimIsRejected(t *testing.T) {
	j := &stubJudge{verdicts: []Verdict{{
		Grounded:    false,
		Unsupported: []string{"the migration ran in March"},
	}}}
	g := &Grounding{Judge: j}

	err := g.Check(context.Background(),
		Request{Rel: "x.md", Raw: "The migration rewrote the tree."},
		respond(t, "The migration ran in March and rewrote the tree."))
	if err == nil {
		t.Fatal("an invented claim was written")
	}
	if !errors.Is(err, ErrNotEligible) {
		t.Errorf("wrong error kind: %v", err)
	}
	if !strings.Contains(err.Error(), "the migration ran in March") {
		t.Errorf("the rejection does not name the claim: %v", err)
	}
}

// A rejection with no claims is a judge that disliked the rewrite rather than
// one that found a problem. Treating that as a finding is how an LLM gate
// becomes a coin flip with a veto.
func TestARejectionWithoutClaimsIsNotAFinding(t *testing.T) {
	j := &stubJudge{verdicts: []Verdict{{Grounded: false}}}
	g := &Grounding{Judge: j}

	err := g.Check(context.Background(),
		Request{Rel: "x.md", Raw: "The gate runs first."},
		respond(t, "The gate runs first."))
	if err == nil {
		t.Fatal("an empty rejection was treated as a pass")
	}
	// It is an error rather than a decline: the note stays unfiled either way,
	// but a decline would count it as "correctly skipped" in the statistics and
	// hide a misbehaving judge behind a normal-looking number.
	if errors.Is(err, ErrNotEligible) {
		t.Error("a judge that named no claim was recorded as a legitimate " +
			"rejection rather than as a judge that failed to answer")
	}
	if !strings.Contains(err.Error(), "without naming a claim") {
		t.Errorf("the error does not say what was wrong with the verdict: %v", err)
	}
}

// A judge that could not answer is not a verdict of "unfaithful". Failing the
// note would make every usage-limit hour look like a corpus full of
// hallucinations — the wrong lesson and the wrong scorecard number.
func TestAJudgeThatCannotAnswerIsNotAVerdict(t *testing.T) {
	j := &stubJudge{err: errors.New("usage limit reached")}
	g := &Grounding{Judge: j}

	err := g.Check(context.Background(),
		Request{Rel: "x.md", Raw: "The gate runs first."},
		respond(t, "The gate runs first."))
	if err == nil {
		t.Fatal("an unanswerable judgment passed silently")
	}
	if errors.Is(err, ErrNotEligible) {
		t.Error("a judge outage was recorded as an ungrounded rewrite")
	}
	if !strings.Contains(err.Error(), "usage limit") {
		t.Errorf("the error loses why the judge could not answer: %v", err)
	}
}

// Faithfulness is per note. That is the operator's ruling, and it is affordable
// precisely because the queue drain is deferred.
func TestFaithfulnessRunsOnEveryNote(t *testing.T) {
	j := &stubJudge{}
	g := &Grounding{Judge: j, Sample: func(string) bool { return false }}

	for i := 0; i < 5; i++ {
		if err := g.Check(context.Background(),
			Request{Rel: "x.md", Raw: "The gate runs first."},
			respond(t, "The gate runs first.")); err != nil {
			t.Fatalf("run %d: %v", i, err)
		}
	}
	if j.asked() != 5 {
		t.Errorf("the judge was asked %d times for 5 notes; faithfulness is per "+
			"note, not sampled", j.asked())
	}
}

// Completeness is the sampled half, and it reports rather than gates — its
// failure mode is what the deterministic token gate already refuses.
func TestCompletenessIsSampledAndReportsRatherThanGates(t *testing.T) {
	var reported []string
	j := &stubJudge{verdicts: []Verdict{
		{Grounded: true}, // faithfulness
		{Grounded: false, Unsupported: []string{"the caveat about X"}}, // completeness
	}}
	g := &Grounding{
		Judge:  j,
		Sample: func(string) bool { return true },
		OnCompleteness: func(_ string, missing []string) {
			reported = append(reported, missing...)
		},
	}

	err := g.Check(context.Background(),
		Request{Rel: "x.md", Raw: "The gate runs first, unless X."},
		respond(t, "The gate runs first."))
	if err != nil {
		t.Errorf("a completeness finding blocked the write; it is measured, not "+
			"enforced: %v", err)
	}
	if len(reported) != 1 || reported[0] != "the caveat about X" {
		t.Errorf("the completeness finding did not reach the scorecard: %v", reported)
	}
	if j.asked() != 2 {
		t.Errorf("the judge was asked %d times; a sampled note gets both halves",
			j.asked())
	}
}

func TestAnUnsampledNoteSkipsTheCompletenessHalf(t *testing.T) {
	j := &stubJudge{}
	called := false
	g := &Grounding{
		Judge:          j,
		Sample:         func(string) bool { return false },
		OnCompleteness: func(string, []string) { called = true },
	}
	if err := g.Check(context.Background(),
		Request{Rel: "x.md", Raw: "The gate runs first."},
		respond(t, "The gate runs first.")); err != nil {
		t.Fatal(err)
	}
	if j.asked() != 1 {
		t.Errorf("an unsampled note cost %d judgments, want 1", j.asked())
	}
	if called {
		t.Error("the completeness callback fired for an unsampled note")
	}
}

// The judge is asked a narrow, checkable question rather than "is this good".
// That is one of the three things bounding a model judging a model.
func TestTheJudgeIsAskedANarrowQuestion(t *testing.T) {
	j := &stubJudge{}
	g := &Grounding{Judge: j}
	if err := g.Check(context.Background(),
		Request{Rel: "x.md", Raw: "The gate runs first."},
		respond(t, "The gate runs first.")); err != nil {
		t.Fatal(err)
	}
	p := j.prompts[0]
	for _, want := range []string{
		"does every factual claim",
		"Not whether the rewrite is good",
		"Dropping something is NOT your concern",
	} {
		if !strings.Contains(strings.Join(strings.Fields(p), " "),
			strings.Join(strings.Fields(want), " ")) {
			t.Errorf("the faithfulness prompt does not narrow the question (%q "+
				"missing)", want)
		}
	}
	// And both texts are in there, or the judge is answering about nothing.
	if !strings.Contains(p, "SOURCE:") || !strings.Contains(p, "REWRITE:") {
		t.Error("the prompt does not carry both texts")
	}
}

// No judge configured means the gate stands aside rather than blocking every
// note. The deterministic gates are what stood between the note and the corpus.
func TestNoJudgeMeansTheGateStandsAside(t *testing.T) {
	g := &Grounding{}
	if err := g.Check(context.Background(),
		Request{Rel: "x.md", Raw: "The gate runs first."},
		respond(t, "invented nonsense")); err != nil {
		t.Errorf("the gate blocked with no judge configured: %v", err)
	}
}

// A rejection leaves the note unfiled, which is a state the system already
// handles — the third thing bounding a model judging a model.
func TestARejectionLeavesTheNoteUnfiled(t *testing.T) {
	j := &stubJudge{verdicts: []Verdict{{
		Grounded: false, Unsupported: []string{"an invented date"},
	}}}
	p := passWith(t, respond(t, "The migration ran in March."))
	p.AddPost(&Grounding{Judge: j})

	out, err := p.Run(context.Background(), Request{
		Rel: "x.md", Raw: "The migration rewrote the tree.",
	})
	if err == nil {
		t.Fatal("an ungrounded rewrite was written")
	}
	if out.Enriched {
		t.Error("an ungrounded rewrite reported the note enriched")
	}
	if out.Body != "" {
		t.Errorf("a rejected rewrite returned a body: %q", out.Body)
	}
}
