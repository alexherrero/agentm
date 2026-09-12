package enrich

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"
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

// respond is a model response carrying this body — moved here from the
// retired token gate's tests.
func respond(t *testing.T, body string) string {
	t.Helper()
	b, err := json.Marshal(Response{Title: "A note", Type: "fact", Body: body, Confidence: 0.9})
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
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
	g := &Grounding{Judge: j}

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
		"Not whether the proposal is good",
		"Leaving something out is NOT your concern",
	} {
		if !strings.Contains(strings.Join(strings.Fields(p), " "),
			strings.Join(strings.Fields(want), " ")) {
			t.Errorf("the faithfulness prompt does not narrow the question (%q "+
				"missing)", want)
		}
	}
	// And both texts are in there, or the judge is answering about nothing.
	if !strings.Contains(p, "SOURCE — the card:") || !strings.Contains(p, "PROPOSAL:") {
		t.Error("the prompt does not carry both texts")
	}
}

// The neighbours the pass was shown are part of the source: drawing on them is
// what the deep pass is for, so a claim that comes from one is grounded.
func TestTheNeighboursAreInTheJudgesSource(t *testing.T) {
	j := &stubJudge{}
	g := &Grounding{Judge: j}
	req := Request{Rel: "x.md", Raw: "The gate runs first.", Neighbours: []Neighbour{
		{ID: "staging-gate", Title: "The staging gate", Summary: "It refuses a push with secrets."},
	}}
	if err := g.Check(context.Background(), req, respond(t, "It pairs with the staging gate.")); err != nil {
		t.Fatal(err)
	}
	p := j.prompts[0]
	src := p[strings.Index(p, "SOURCE — the card:"):strings.Index(p, "PROPOSAL:")]
	if !strings.Contains(src, "The staging gate: It refuses a push with secrets.") {
		t.Errorf("the neighbour is not in the judge's source:\n%s", src)
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

// keyShapes are families of regularly-structured keys, which is what the
// sampler actually sees. A vault path is not an arbitrary string: it carries a
// fixed prefix, a timestamp, and a counter that moves one digit at a time.
var keyShapes = []struct {
	name string
	key  func(i int) string
}{
	{"session:ts pairs", func(i int) string {
		return fmt.Sprintf("s%d:t%d", i, i)
	}},
	{"numbered inbox proposals", func(i int) string {
		return fmt.Sprintf("desk/scratch/inbox-20260813T074616Z-16856bac/"+
			"%d-inbox_collapse-collapse.proposal.md", i)
	}},
	{"sequential note names", func(i int) string {
		return fmt.Sprintf("notes/note-%04d.md", i)
	}},
	// A daily series: the day digit moves every step, the month every 28, the
	// year every 336. Mixed-radix on i, so every key is distinct — a shape that
	// repeats keys measures its own granularity rather than the hash.
	{"dated captures", func(i int) string {
		return fmt.Sprintf("desk/projects/agentm/capture-%04d%02d%02d.md",
			2020+i/336, 1+(i/28)%12, 1+i%28)
	}},
}

// A shape that collides with itself cannot measure a hash: 4000 keys drawn from
// 84 distinct strings land in buckets quantised to ~48, which reads as drift no
// hash could fix. This caught exactly that bug in the dated-captures shape.
func TestTheKeyShapesAreDistinct(t *testing.T) {
	const keys = 4000
	for _, shape := range keyShapes {
		seen := make(map[string]bool, keys)
		for i := 0; i < keys; i++ {
			seen[shape.key(i)] = true
		}
		if len(seen) != keys {
			t.Errorf("the %s shape yields %d distinct keys from %d indices; a "+
				"repeating shape measures its own granularity, not the hash",
				shape.name, len(seen), keys)
		}
	}
}

// A sampler must reach every residue class, not merely select about the right
// number of keys. Those are different properties and only the second is
// obvious: `h % n` over an unfinalized FNV-1a hash selects one key in five for
// n=10 while leaving every odd residue unreachable, because FNV-1a's lowest bit
// tracks the parity of its input bytes and a modulus inherits that structure.
//
// A count-only test passes straight through that — half the key space is
// unreachable and the count still lands near target for some shapes. So this
// asserts occupancy per class, which is the property that actually fails.
func TestTheSamplerReachesEveryResidueClass(t *testing.T) {
	// n=20 is the daemon's default rate, and even n is where parity structure
	// bites. n=10 is the shape the Python side measured.
	for _, n := range []int{2, 10, 16, 20} {
		for _, shape := range keyShapes {
			t.Run(fmt.Sprintf("n=%d/%s", n, shape.name), func(t *testing.T) {
				const keys = 4000
				residues := make([]int, n)
				for i := 0; i < keys; i++ {
					residues[mix(fnv1a(shape.key(i)))%uint32(n)]++
				}
				want := float64(keys) / float64(n)
				for r, got := range residues {
					if got == 0 {
						t.Fatalf("residue %d of %d is unreachable — no key maps "+
							"to it, so that share of the corpus can never be "+
							"sampled; histogram %v", r, n, residues)
					}
					// Half of uniform. Loose enough that an honest hash never
					// trips it, tight enough to catch a starved class.
					if drift := float64(got) / want; drift < 0.5 || drift > 1.5 {
						t.Errorf("residue %d of %d holds %d keys, want about "+
							"%.0f (drift %.2fx); histogram %v",
							r, n, got, want, drift, residues)
					}
				}
			})
		}
	}
}

// The rate is the other half of the contract: about one key in n, and the
// occupancy test above says nothing about it on its own.
func TestTheSamplerSelectsAboutOneKeyInN(t *testing.T) {
	for _, n := range []int{2, 10, 20} {
		sample := SampleEvery(n)
		for _, shape := range keyShapes {
			const keys = 4000
			taken := 0
			for i := 0; i < keys; i++ {
				if sample(shape.key(i)) {
					taken++
				}
			}
			want := float64(keys) / float64(n)
			if drift := float64(taken) / want; drift < 0.75 || drift > 1.25 {
				t.Errorf("one-in-%d over %s took %d of %d keys, want about "+
					"%.0f (drift %.2fx)", n, shape.name, taken, keys, want, drift)
			}
		}
	}
}

// Deterministic on the path is the whole reason the sampler is not random: a
// completeness number that moves means the corpus moved rather than the dice.
func TestTheSamplerIsDeterministicAndHandlesTheEdges(t *testing.T) {
	sample := SampleEvery(20)
	for i := 0; i < 200; i++ {
		p := fmt.Sprintf("notes/note-%04d.md", i)
		if sample(p) != sample(p) {
			t.Fatalf("the sampler disagreed with itself about %q", p)
		}
		if sample(p) != SampleEvery(20)(p) {
			t.Fatalf("a second sampler disagreed about %q; a re-run would "+
				"sample a different set", p)
		}
	}
	if SampleEvery(0)("x.md") || SampleEvery(-1)("x.md") {
		t.Error("a non-positive rate sampled something; it samples nothing")
	}
	if !SampleEvery(1)("x.md") {
		t.Error("a rate of one skipped a note; it samples everything")
	}
}

// The asymmetry that produced false refusals: the enricher is handed the whole
// card and told every claim must trace to it, while the judge used to be shown
// the body alone. Three of the nineteen refusals on 2026-09-11 were a card
// being refused for naming a fact written in its own frontmatter.
func TestTheJudgeSeesTheFactsTheCardCarries(t *testing.T) {
	j := &stubJudge{}
	g := &Grounding{Judge: j}
	raw := "---\n" +
		"type: idea\n" +
		"captured: '2026-05-22'\n" +
		"lifecycle: active\n" +
		"source_id: idea-incubator:collective-memory\n" +
		"tags: [idea-incubator-graduate]\n" +
		"---\n\nThe queue is vault-backed.\n"
	if err := g.Check(context.Background(), Request{Rel: "x.md", Raw: raw},
		respond(t, "It graduated from the incubator.")); err != nil {
		t.Fatal(err)
	}
	src := judgeSourceOf(t, j.prompts[0])
	for _, want := range []string{
		"captured: '2026-05-22'",
		"lifecycle: active",
		"source_id: idea-incubator:collective-memory",
		// Never enriched, so nothing on this card is a previous pass's answer.
		"tags: [idea-incubator-graduate]",
		"The queue is vault-backed.",
	} {
		if !strings.Contains(src, want) {
			t.Errorf("the judge cannot see %q, which the card plainly carries:\n%s", want, src)
		}
	}
}

// The other half of the same rule. On a card the pass has already enriched, its
// own `title`, `summary` and `tags` are its previous answer — handing those
// back as source would let a hallucination one pass let through ground the
// next pass's restatement of it.
func TestTheJudgeIsNotShownThePassesOwnPreviousAnswer(t *testing.T) {
	j := &stubJudge{}
	g := &Grounding{Judge: j}
	raw := "---\n" +
		"title: A title an earlier pass wrote\n" +
		"summary: A summary an earlier pass wrote\n" +
		"tags: [a-tag-an-earlier-pass-wrote]\n" +
		"captured: '2026-05-22'\n" +
		"source: conversation\n" +
		"enriched_by: enrich/1+prompt/older\n" +
		"enriched_at: \"2026-08-27T13:52:16Z\"\n" +
		"---\n\nThe queue is vault-backed.\n"
	if err := g.Check(context.Background(), Request{Rel: "x.md", Raw: raw},
		respond(t, "Something.")); err != nil {
		t.Fatal(err)
	}
	src := judgeSourceOf(t, j.prompts[0])
	for _, gone := range []string{
		"A title an earlier pass wrote",
		"A summary an earlier pass wrote",
		"a-tag-an-earlier-pass-wrote",
	} {
		if strings.Contains(src, gone) {
			t.Errorf("the judge is being shown %q, which the pass wrote itself:\n%s", gone, src)
		}
	}
	// What the card carried from before the pass is still evidence.
	for _, kept := range []string{"captured: '2026-05-22'", "source: conversation",
		"The queue is vault-backed."} {
		if !strings.Contains(src, kept) {
			t.Errorf("the judge lost %q, which no pass wrote:\n%s", kept, src)
		}
	}
}

// A dropped key takes its block-list items with it. Keeping them would leave
// the judge reading bare values under whichever key happened to come before.
func TestDroppingAKeyDropsTheLinesThatBelongToIt(t *testing.T) {
	fm := "---\ntags:\n  - one\n  - two\ncaptured: '2026-05-22'\nwhy: the room said so\n---\n"
	got := carriedFrontmatter(fm)
	for _, gone := range []string{"tags:", "- one", "- two"} {
		if strings.Contains(got, gone) {
			t.Errorf("%q survived a dropped key:\n%s", gone, got)
		}
	}
	for _, kept := range []string{"captured: '2026-05-22'", "why: the room said so", "---"} {
		if !strings.Contains(got, kept) {
			t.Errorf("%q was dropped with something else:\n%s", kept, got)
		}
	}
}

// The register the filter reads has to know every field the render can write,
// or a newly added field quietly becomes evidence for itself.
func TestEveryFieldTheRenderWritesIsInThePassWrittenRegister(t *testing.T) {
	r := Response{
		Title: "T", Type: "reference", Summary: "S", Confidence: 0.1,
		ImportanceProposed: 7, Tags: []string{"a"}, Aliases: []string{"b"},
		Related: []string{"c"},
	}
	s := Stamp{Version: "v", RulesHash: "h", ConfidenceFloor: 0.65, At: time.Now()}
	// Below the floor and judged below before, so the sink fields are written
	// too — those are the two only a sinking card ever shows.
	v := VerdictFor("---\nstatus: unfiled\nenriched_at: \"2026-01-01T00:00:00Z\"\n---\n", r, 0.65)
	if !v.Sank {
		t.Fatal("the fixture did not sink, so lifecycle_since is never rendered")
	}
	for _, line := range strings.Split(RenderFrontmatter(r, s, v), "\n") {
		if line == "" || line == "---" {
			continue
		}
		key, _, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		if !passWrittenFields[strings.ToLower(strings.TrimSpace(key))] {
			t.Errorf("RenderFrontmatter writes %q and passWrittenFields does not "+
				"list it, so the judge would be shown the pass's own answer as source",
				strings.TrimSpace(key))
		}
	}
}

// judgeSourceOf is the SOURCE section of a faithfulness prompt.
func judgeSourceOf(t *testing.T, prompt string) string {
	t.Helper()
	i := strings.Index(prompt, "SOURCE — the card:")
	j := strings.Index(prompt, "PROPOSAL:")
	if i < 0 || j < 0 {
		t.Fatalf("the prompt carries no source section:\n%s", prompt)
	}
	return prompt[i:j]
}
