package enrich

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
)

// Grounding: faithfulness, per note.
//
// **Faithfulness** asks whether what the pass proposes — a title, a summary, a
// paragraph added below the card — asserts anything the card and its
// neighbours did not contain: the model filling a gap with something
// plausible, an invented date, an inferred reason. That failure is silent and
// permanent, which is why it is checked on every note. Its twin, completeness —
// whether a rewrite left something out — retired with the rewrite: the card's
// own text is carried byte for byte now, so nothing of it can go missing.
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

const faithfulnessPrompt = `You are checking what a pass proposes to write onto one card of a
personal memory vault, against the card and the neighbouring notes it was shown.

Answer exactly one question: does every factual claim in the PROPOSAL appear in
the SOURCE?

Not whether the proposal is good. Not whether it is complete. Not whether you
would have written it differently. Only whether it asserts anything the source
does not contain — an added date, an inferred reason, a filled-in gap, a
plausible detail that is not there.

Rephrasing is fine. Connecting two things the source says is fine. Leaving
something out is NOT your concern here.

Return a single JSON object and nothing else:

  {"grounded": true}

or, when the proposal asserts something the source does not:

  {"grounded": false, "unsupported": ["the exact claim", "another one"]}

If grounded is false you must list the claims. A rejection with no claims is not
an answer.`

// Grounding is the post-gate.
//
// One direction only now. It used to check completeness on a sample too — what
// a rewrite left out — and that half retired with the rewrite (agentm-vault
// plan 04): the card's own text is carried byte for byte, so nothing of it can
// be left out. What can still go wrong is the other direction, and it is the
// silent one: a summary or an added paragraph asserting something neither the
// card nor its neighbours said.
type Grounding struct {
	// Judge is the model asked the question. Nil disables the gate, which is
	// what a caller with no second model configured gets — the note is written
	// and the deterministic gates are what stood between it and the corpus.
	Judge Judge
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
	v, err := g.Judge.Judge(ctx, faithfulnessQuestion(req, r))
	if err != nil {
		// A judge that could not answer is not a verdict of "unfaithful". Failing
		// the note here would make every usage-limit hour look like a corpus full
		// of hallucinations, which is the wrong lesson to draw and the wrong
		// number to put on a scorecard.
		return fmt.Errorf("enrich: the faithfulness judge could not answer: %w", err)
	}
	if !v.Grounded {
		if len(v.Unsupported) == 0 {
			// A rejection with no claims is a judge that disliked the proposal
			// rather than one that found a problem. Those are the rejections
			// worth ignoring, and treating them as findings is how an LLM gate
			// becomes a coin flip with a veto.
			return fmt.Errorf("enrich: the judge rejected the proposal without "+
				"naming a claim, which is not an answer; %s left as it was", req.Rel)
		}
		return fmt.Errorf("%w: the proposal asserts what the card and its "+
			"neighbours do not: %s", ErrNotEligible, strings.Join(quoteAll(v.Unsupported), ", "))
	}
	return nil
}

// faithfulnessQuestion is the source — the card's own text and the neighbours
// it was shown — and the proposal: the title, the summary, and any prose the
// deep pass would add. The neighbours are part of the source because drawing on
// them is what the deep pass is for.
func faithfulnessQuestion(req Request, r Response) string {
	var b strings.Builder
	b.WriteString(faithfulnessPrompt)
	b.WriteString("\n\nSOURCE — the card:\n\n")
	b.WriteString(judgeSource(req.Raw))
	if len(req.Neighbours) > 0 {
		b.WriteString("\n\nSOURCE — its neighbours:\n\n")
		for _, n := range req.Neighbours {
			fmt.Fprintf(&b, "- %s", oneLine(n.Title))
			if n.Summary != "" {
				fmt.Fprintf(&b, ": %s", oneLine(n.Summary))
			}
			b.WriteString("\n")
		}
	}
	b.WriteString("\n\nPROPOSAL:\n\n")
	b.WriteString(r.Title)
	if r.Summary != "" {
		b.WriteString("\n\nSummary: ")
		b.WriteString(r.Summary)
	}
	if strings.TrimSpace(r.Body) != "" {
		b.WriteString("\n\nAdded below the card:\n\n")
		b.WriteString(r.Body)
	}
	return b.String()
}

// judgeSource is the card as the judge sees it: its own text, and above it the
// frontmatter facts it carries that no pass put there.
//
// It used to be the body alone, and that was a false-refusal machine. The
// enricher is handed `req.Raw` — the whole card, frontmatter included — and the
// prompt tells it every claim must be traceable to the card. So a proposal
// naming the card's `source_id`, its `lifecycle`, or the date it was captured
// is doing exactly what it was asked, while a judge shown only the body cannot
// find any of it and correctly-by-its-lights calls it invented. Three of the
// nineteen refusals on the night of 2026-09-11 were that and nothing else:
// `agentm-v7-multi-agent-collective-memory` refused for naming its own
// `source_id` and its own tag, `docker-inventory` for a capture date sitting in
// its `captured:` field, `deliberate-capture-lands-active-when-the-caller-says-so`
// for a `lifecycle: active` written directly above the line the judge read.
//
// Not the whole block, though. The pass writes `title`, `summary`, `tags` and
// the rest of passWrittenFields, so on a card it has already enriched those are
// its own previous answer — and handing them back as source would let a
// hallucination the last pass let through ground the next pass's restatement of
// it. A card with no enrichment stamp has no previous pass, so every field on
// it is the capture's and all of it is evidence.
func judgeSource(raw string) string {
	fm, body := splitNote(raw)
	if fm == "" {
		return body
	}
	if strings.TrimSpace(frontmatterValue(raw, "enriched_at")) == "" {
		return fm + body
	}
	return carriedFrontmatter(fm) + body
}

// carriedFrontmatter is the frontmatter block with the pass's own fields taken
// out, fences kept so it still reads as frontmatter.
//
// A line that does not open a key — an indented block-list item, a wrapped
// value — belongs to whichever key last opened, and is kept or dropped with it.
// Dropping a key and keeping its items would leave the judge reading a list of
// bare values under whatever came before.
func carriedFrontmatter(fm string) string {
	var b strings.Builder
	keep := true
	for _, line := range strings.SplitAfter(fm, "\n") {
		trimmed := strings.TrimRight(line, "\n")
		switch {
		case trimmed == "---":
			keep = true
		case trimmed == "" || strings.HasPrefix(trimmed, " ") ||
			strings.HasPrefix(trimmed, "\t") || strings.HasPrefix(trimmed, "-"):
			// A continuation: it keeps the previous key's decision.
		default:
			k, _, ok := strings.Cut(trimmed, ":")
			keep = !ok || !passWrittenFields[strings.ToLower(strings.TrimSpace(k))]
		}
		if keep {
			b.WriteString(line)
		}
	}
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
