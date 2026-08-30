package enrich

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
)

// Two-way grounding: faithfulness per note, completeness sampled.
//
// The two directions fail differently, which is why they are checked
// differently. **Faithfulness** asks whether the rewrite added anything the
// source did not contain — the model filling a gap with something plausible, an
// invented date, a inferred reason. That failure is silent and permanent: the
// note now asserts something nobody wrote, and the raw text it came from is one
// commit back where nobody looks. **Completeness** asks whether the rewrite left
// something out, and that failure is loud by comparison, because the
// deterministic token gate already catches the part of it that matters most.
//
// So faithfulness runs on every note and completeness runs on a sample. That is
// the operator's ruling, taken with the queue drain deferred: per-note is
// affordable because it lands on a handful of eager captures a day rather than
// on 8,407.
//
// # A model judging a model
//
// This is a real departure from the rule that deterministic checks gate and LLM
// judgment augments, and it is worth naming rather than burying. Three things
// bound it. The deterministic gates run first, so a response that fails a
// mechanical check never reaches a judge. The judge is asked a narrow,
// checkable question — "is every claim in B present in A" — rather than "is B
// good". And a rejection leaves the note `unfiled`, which is a state the system
// already handles, rather than deleting or corrupting anything.

// Judge asks a model a yes/no question about a rewrite.
//
// Separate from Caller because the two want different things: enrichment wants
// prose and gets a long answer, while a judge wants a verdict and anything long
// is a sign it is reasoning its way out of a clear answer.
type Judge interface {
	// Judge returns the verdict for one prompt.
	Judge(ctx context.Context, prompt string) (Verdict, error)
}

// Verdict is what a judge returns.
type Verdict struct {
	// Grounded is the answer.
	Grounded bool `json:"grounded"`
	// Unsupported names the claims the judge could not find in the source. It
	// is required when Grounded is false: a rejection without one is a judge
	// that disliked the rewrite rather than one that found a problem, and those
	// are exactly the rejections worth ignoring.
	Unsupported []string `json:"unsupported,omitempty"`
}

// callerJudge adapts a Caller.
type callerJudge struct{ c *Caller }

// NewJudge wraps a model caller as a judge.
func NewJudge(c *Caller) Judge { return &callerJudge{c: c} }

func (j *callerJudge) Judge(ctx context.Context, prompt string) (Verdict, error) {
	var v Verdict
	raw, err := j.c.Call(ctx, prompt)
	if err != nil {
		return v, err
	}
	obj, err := extractJSON(raw)
	if err != nil {
		return v, err
	}
	dec := json.NewDecoder(strings.NewReader(obj))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&v); err != nil {
		return v, fmt.Errorf("enrich: judge returned an unexpected shape: %w", err)
	}
	return v, nil
}

const faithfulnessPrompt = `You are checking one rewrite of a note against its source.

Answer exactly one question: does every factual claim in the REWRITE appear in
the SOURCE?

Not whether the rewrite is good. Not whether it is complete. Not whether you
would have written it differently. Only whether it asserts anything the source
does not contain — an added date, an inferred reason, a filled-in gap, a
plausible detail that is not there.

Rephrasing is fine. Reordering is fine. Condensing is fine. Dropping something
is NOT your concern here.

Return a single JSON object and nothing else:

  {"grounded": true}

or, when the rewrite asserts something the source does not:

  {"grounded": false, "unsupported": ["the exact claim", "another one"]}

If grounded is false you must list the claims. A rejection with no claims is not
an answer.`

// Grounding is the post-gate.
type Grounding struct {
	// Judge is the model asked the question. Nil disables the gate, which is
	// what a caller with no second model configured gets — the note is written
	// and the deterministic gates are what stood between it and the corpus.
	Judge Judge
	// Sample decides whether this note also gets the completeness half. Nil
	// means never. Faithfulness does not consult it: that half is per note.
	Sample func(rel string) bool
	// OnCompleteness receives a sampled completeness result for the scorecard.
	// Reporting rather than gating, because dropping something is what the
	// deterministic token gate already refuses.
	OnCompleteness func(rel string, missing []string)
}

func (g *Grounding) Name() string { return "grounding" }

func (g *Grounding) Check(ctx context.Context, req Request, body string) error {
	if g.Judge == nil {
		return nil
	}
	r, err := ParseResponse(body)
	if err != nil {
		return fmt.Errorf("%w: %v", ErrNotEligible, err)
	}

	v, err := g.Judge.Judge(ctx, faithfulnessQuestion(sourceBody(req.Raw), r))
	if err != nil {
		// A judge that could not answer is not a verdict of "unfaithful". Failing
		// the note here would make every usage-limit hour look like a corpus full
		// of hallucinations, which is the wrong lesson to draw and the wrong
		// number to put on a scorecard.
		return fmt.Errorf("enrich: the faithfulness judge could not answer: %w", err)
	}
	if !v.Grounded {
		if len(v.Unsupported) == 0 {
			// A rejection with no claims is a judge that disliked the rewrite
			// rather than one that found a problem. Those are the rejections
			// worth ignoring, and treating them as findings is how an LLM gate
			// becomes a coin flip with a veto.
			return fmt.Errorf("enrich: the judge rejected the rewrite without "+
				"naming a claim, which is not an answer; %s left unfiled", req.Rel)
		}
		return fmt.Errorf("%w: the rewrite asserts what the source does not: %s",
			ErrNotEligible, strings.Join(quoteAll(v.Unsupported), ", "))
	}

	if g.Sample != nil && g.Sample(req.Rel) && g.OnCompleteness != nil {
		// Completeness is measured, not enforced. Its failure mode is already
		// covered mechanically by the token gate; what a sample adds is a number
		// for the scorecard about the part a regex cannot see.
		if cv, err := g.Judge.Judge(ctx, completenessQuestion(sourceBody(req.Raw), r)); err == nil {
			g.OnCompleteness(req.Rel, cv.Unsupported)
		}
	}
	return nil
}

func faithfulnessQuestion(source string, r Response) string {
	var b strings.Builder
	b.WriteString(faithfulnessPrompt)
	b.WriteString("\n\nSOURCE:\n\n")
	b.WriteString(source)
	b.WriteString("\n\nREWRITE:\n\n")
	b.WriteString(r.Title)
	b.WriteString("\n\n")
	b.WriteString(r.Body)
	if r.Summary != "" {
		b.WriteString("\n\nSummary: ")
		b.WriteString(r.Summary)
	}
	return b.String()
}

const completenessPrompt = `You are checking one rewrite of a note against its source.

Answer exactly one question: what does the SOURCE say that the REWRITE leaves
out entirely?

Only substantive omissions — a fact, a caveat, a reason, a consequence. Not
wording, not length, not style. Condensing is expected and is not an omission.

Return a single JSON object and nothing else:

  {"grounded": true}

when nothing substantive was lost, or:

  {"grounded": false, "unsupported": ["what was left out", "and this"]}`

func completenessQuestion(source string, r Response) string {
	var b strings.Builder
	b.WriteString(completenessPrompt)
	b.WriteString("\n\nSOURCE:\n\n")
	b.WriteString(source)
	b.WriteString("\n\nREWRITE:\n\n")
	b.WriteString(r.Body)
	return b.String()
}

// SampleEvery returns a sampler that selects roughly one note in n.
//
// Deterministic on the path rather than random, and that is deliberate: a run
// re-run over the same queue samples the same notes, so a completeness number
// that moves means the corpus moved rather than the dice did. `n <= 1` samples
// everything; `n == 0` samples nothing.
func SampleEvery(n int) func(string) bool {
	if n <= 0 {
		return func(string) bool { return false }
	}
	if n == 1 {
		return func(string) bool { return true }
	}
	return func(rel string) bool {
		return mix(fnv1a(rel))%uint32(n) == 0
	}
}

// mix finalizes a hash before a small modulus.
//
// FNV-1a's lowest bit is close to the parity of its input bytes, so `h % n` for
// any even `n` inherits that structure instead of spreading over it. Half the
// residue classes come out unreachable: on keys shaped `s0:t0, s1:t1, …` the
// residues mod 10 are `[794 0 770 0 774 0 842 0 820 0]`, so a one-in-ten sample
// takes one in five. At the daemon's default rate of 20 it took 704 of 4000
// such keys rather than 200, and a one-in-two sample of them takes all 4000.
//
// Note-path shapes measured clean without this, so the daemon's own sampling
// was probably not skewed in practice — numbered, sequential, and dated paths
// all reached every residue class on the raw hash. But which shapes escape is a
// property of how the keys happen to look rather than of anything this code
// controls, and a sampler whose bias depends on its input's shape is not one.
// The `s0:t0` shape that fails here is the Python side's turn key.
//
// This is Murmur3's fmix32, which measured flat on every key shape tried. The
// constants are hex for the same reason FNV's are: the repository's PII scanner
// reads the decimal forms as phone numbers. Mirrors `_mix` in
// `scripts/health/sufficient_context.py`, where the same bias was found first.
func mix(h uint32) uint32 {
	h ^= h >> 16
	h *= 0x85EBCA6B
	h ^= h >> 13
	h *= 0xC2B2AE35
	h ^= h >> 16
	return h
}

// fnv1a is a small non-cryptographic hash. The sampler only needs an even
// spread over paths, and sha256 here would be paying for collision resistance
// nothing depends on.
//
// The constants are written in hex, which is how FNV is conventionally
// specified and also keeps the decimal offset basis from reading as a US phone
// number to the repository's PII scanner.
func fnv1a(s string) uint32 {
	const (
		offsetBasis uint32 = 0x811C9DC5
		prime       uint32 = 0x01000193
	)
	h := offsetBasis
	for i := 0; i < len(s); i++ {
		h ^= uint32(s[i])
		h *= prime
	}
	return h
}
