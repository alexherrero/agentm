package enrich

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// The claim these hold: a card a post-gate refused is not bought a second
// answer to the same question, and it comes back the moment the question
// changes.

func refusalRow(rel, key string) Refusal {
	return Refusal{
		Rel: rel, Gate: "grounding", Key: key,
		Version: "enrich/1+prompt/aaa", RulesHash: "rules-1", Gates: GatesVersion,
		Reason: "the proposal asserts what the card does not",
		At:     time.Date(2026, 9, 11, 23, 45, 0, 0, time.UTC),
	}
}

func TestARefusalStandsOnlyWhileEveryPartOfItsKeyHolds(t *testing.T) {
	r, err := NewRefusals(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if err := r.Record(refusalRow("memory/semantic/a.md", "key-1")); err != nil {
		t.Fatal(err)
	}

	if _, ok := r.Standing("memory/semantic/a.md", "key-1", GatesVersion); !ok {
		t.Fatal("the card was refused at this key under these gates and is not standing")
	}
	// The body moved, so the key moved: the card is owed another look.
	if _, ok := r.Standing("memory/semantic/a.md", "key-2", GatesVersion); ok {
		t.Error("a card whose body changed is still being treated as refused")
	}
	// The judge's question moved, so yesterday's answer is not today's.
	if _, ok := r.Standing("memory/semantic/a.md", "key-1", "gates/2+faith/zzz"); ok {
		t.Error("a refusal survived a change to the gate that made it")
	}
	if _, ok := r.Standing("memory/semantic/b.md", "key-1", GatesVersion); ok {
		t.Error("a refusal of one card is standing against another")
	}
}

func TestARecordedRefusalSurvivesTheProcessThatWroteIt(t *testing.T) {
	dir := t.TempDir()
	first, err := NewRefusals(dir)
	if err != nil {
		t.Fatal(err)
	}
	if err := first.Record(refusalRow("memory/semantic/a.md", "key-1")); err != nil {
		t.Fatal(err)
	}

	// A second run, which is the case that matters: the nightly job is a fresh
	// process every night, and a record only kept in memory would save nothing.
	second, err := NewRefusals(dir)
	if err != nil {
		t.Fatal(err)
	}
	row, ok := second.Standing("memory/semantic/a.md", "key-1", GatesVersion)
	if !ok {
		t.Fatal("the refusal did not survive into the next run")
	}
	if !strings.Contains(row.Reason, "asserts what the card does not") {
		t.Errorf("the reason did not survive: %q", row.Reason)
	}
	if row.Gate != "grounding" {
		t.Errorf("gate = %q, want grounding", row.Gate)
	}
}

func TestTheNewestRowWinsAndCompactionLeavesOnePerCard(t *testing.T) {
	dir := t.TempDir()
	r, err := NewRefusals(dir)
	if err != nil {
		t.Fatal(err)
	}
	old := refusalRow("memory/semantic/a.md", "key-1")
	newer := refusalRow("memory/semantic/a.md", "key-2")
	newer.At = old.At.Add(24 * time.Hour)
	for _, row := range []Refusal{old, newer, refusalRow("memory/semantic/b.md", "key-9")} {
		if err := r.Record(row); err != nil {
			t.Fatal(err)
		}
	}
	if _, ok := r.Standing("memory/semantic/a.md", "key-1", GatesVersion); ok {
		t.Error("the superseded key is still standing")
	}
	if _, ok := r.Standing("memory/semantic/a.md", "key-2", GatesVersion); !ok {
		t.Error("the newest key is not standing")
	}

	if err := r.Compact(); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(dir, RefusalsName))
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	if len(lines) != 2 {
		t.Fatalf("compaction left %d row(s), want one per card:\n%s", len(lines), raw)
	}
	var got Refusal
	if err := json.Unmarshal([]byte(lines[0]), &got); err != nil {
		t.Fatal(err)
	}
	if got.Key != "key-2" {
		t.Errorf("compaction kept key %q, want the newest", got.Key)
	}
}

func TestATruncatedLastLineDoesNotCostTheRowsBeforeIt(t *testing.T) {
	dir := t.TempDir()
	r, err := NewRefusals(dir)
	if err != nil {
		t.Fatal(err)
	}
	if err := r.Record(refusalRow("memory/semantic/a.md", "key-1")); err != nil {
		t.Fatal(err)
	}
	// What a machine dying mid-append leaves behind.
	f, err := os.OpenFile(filepath.Join(dir, RefusalsName), os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.WriteString(`{"rel":"memory/semantic/b.md","ga`); err != nil {
		t.Fatal(err)
	}
	f.Close()

	again, err := NewRefusals(dir)
	if err != nil {
		t.Fatalf("a half-written last line failed the whole read: %v", err)
	}
	if _, ok := again.Standing("memory/semantic/a.md", "key-1", GatesVersion); !ok {
		t.Error("the complete row before the torn one was lost")
	}
}

func TestOpenCountsOnlyWhatThisPassAndTheseGatesWouldRefuseAgain(t *testing.T) {
	r, err := NewRefusals(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	current := refusalRow("memory/semantic/current.md", "key-1")
	stale := refusalRow("memory/semantic/old-prompt.md", "key-2")
	stale.Version = "enrich/1+prompt/older"
	otherRules := refusalRow("memory/semantic/old-rules.md", "key-3")
	otherRules.RulesHash = "rules-0"
	for _, row := range []Refusal{current, stale, otherRules} {
		if err := r.Record(row); err != nil {
			t.Fatal(err)
		}
	}
	if n := r.Open("enrich/1+prompt/aaa", "rules-1", GatesVersion); n != 1 {
		t.Errorf("open = %d, want 1 — a refusal under a retired prompt or contract "+
			"is a card that will be offered again, not a backlog", n)
	}
	r.Resolve("memory/semantic/current.md")
	if n := r.Open("enrich/1+prompt/aaa", "rules-1", GatesVersion); n != 0 {
		t.Errorf("open = %d after the card was enriched, want 0", n)
	}
}

func TestTheGateDeclinesARefusedCardAndSaysWhoRefusedItAndWhen(t *testing.T) {
	fp := &Fingerprint{Version: "enrich/1+prompt/aaa", RulesHash: "rules-1"}
	raw := "---\ntype: reference\n---\n\nA card.\n"
	r, err := NewRefusals(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	row := refusalRow("memory/semantic/a.md", fp.Key(raw))
	if err := r.Record(row); err != nil {
		t.Fatal(err)
	}

	g := &Refused{Key: fp.Key, Standing: func(rel, key string) (Refusal, bool) {
		return r.Standing(rel, key, GatesVersion)
	}}
	err = g.Check(context.Background(), Request{Rel: "memory/semantic/a.md", Raw: raw}, raw)
	if !errors.Is(err, ErrNotEligible) {
		t.Fatalf("the gate did not decline a standing refusal: %v", err)
	}
	for _, want := range []string{"grounding", "2026-09-11", "asserts what the card does not"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the refusal reason does not carry %q: %v", want, err)
		}
	}

	// The same card, one word changed. Nothing is standing against it.
	moved := "---\ntype: reference\n---\n\nA card, edited.\n"
	if err := g.Check(context.Background(),
		Request{Rel: "memory/semantic/a.md", Raw: moved}, moved); err != nil {
		t.Errorf("an edited card was declined on a refusal of what it used to say: %v", err)
	}
}

func TestTheGateIsInertWithNoRecord(t *testing.T) {
	g := &Refused{}
	if err := g.Check(context.Background(), Request{Rel: "a.md"}, "body"); err != nil {
		t.Errorf("a gate with nothing to remember declined a card: %v", err)
	}
}

// The two gates have to agree about what "the same card under the same pass"
// means, and the only way to guarantee that is for there to be one answer.
func TestTheRefusalGateIsKeyedByTheFingerprintsOwnKey(t *testing.T) {
	fp := &Fingerprint{Version: "enrich/1+prompt/aaa", RulesHash: "rules-1"}
	g := &Refused{Key: fp.Key}
	body := "---\ntype: reference\n---\n\nA card.\n"
	if g.Key(body) != fp.Key(body) {
		t.Fatal("the refusal gate's key and the fingerprint's key have diverged")
	}
	// And it inherits the normalization, so a reflow is not a new card.
	reflowed := "---\ntype:  reference\n---\n\n\nA   Card.\n"
	if g.Key(reflowed) != fp.Key(body) {
		t.Error("a card that was only reformatted reads as a different card")
	}
}

// --- what the pass reports, so the record has something to key on -----------

// Only a rejection that named a claim is a fact about the card. The two other
// ways a post-gate can fail — a judge that could not answer, a judge that said
// no without saying why — are facts about the judge, and recording them would
// blacklist every card touched during a bad hour.
func TestOnlyAnIneligibleRejectionNamesTheGateThatRefused(t *testing.T) {
	for _, tc := range []struct {
		name     string
		err      error
		wantGate string
	}{
		{"a claim the source does not contain",
			fmt.Errorf("%w: the proposal asserts what the card does not", ErrNotEligible),
			"grounding"},
		{"a judge that could not answer",
			errors.New("enrich: the faithfulness judge could not answer: 429"), ""},
		{"a judge that rejected without naming a claim",
			errors.New("enrich: the judge rejected the proposal without naming a claim"), ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			p := passWith(t, "body")
			p.AddPost(&stubGate{name: "grounding", err: tc.err})
			out, err := p.Run(context.Background(), Request{Rel: "x.md", Raw: "raw"})
			if err == nil {
				t.Fatal("the post-gate rejected and the run reported success")
			}
			if out.RefusedBy != tc.wantGate {
				t.Errorf("RefusedBy = %q, want %q", out.RefusedBy, tc.wantGate)
			}
		})
	}
}

func TestASkipNamesThePreGateThatDeclined(t *testing.T) {
	p := passWith(t, "body")
	p.AddPre(&stubGate{name: GateRefusal,
		err: fmt.Errorf("%w: already refused", ErrNotEligible)})
	out, err := p.Run(context.Background(), Request{Rel: "x.md", Raw: "raw"})
	if err != nil {
		t.Fatalf("a decline is a skip, not an error: %v", err)
	}
	if !out.Skipped || out.SkippedBy != GateRefusal {
		t.Errorf("SkippedBy = %q (skipped %v), want %q", out.SkippedBy, out.Skipped, GateRefusal)
	}
	if out.Calls != 0 {
		t.Errorf("a refused card cost %d call(s); the whole point is zero", out.Calls)
	}
}

// The number that says what the record saved, counted apart from the rest of
// the skips so a growing refused set is not hidden inside them.
func TestTheBatchCountsRefusalSkipsApartFromOtherSkips(t *testing.T) {
	p := passWith(t, "body")
	p.AddPre(gateFunc(GateRefusal, func(req Request) error {
		if req.Rel == "refused.md" {
			return fmt.Errorf("%w: already refused", ErrNotEligible)
		}
		return nil
	}))
	p.AddPre(gateFunc("eligibility", func(req Request) error {
		if req.Rel == "ineligible.md" {
			return fmt.Errorf("%w: not ours", ErrNotEligible)
		}
		return nil
	}))

	queue := []Candidate{
		{Rel: "fresh.md", Raw: "raw"},
		{Rel: "ineligible.md", Raw: "raw"},
		{Rel: "refused.md", Raw: "raw"},
	}
	list := func(_ context.Context, after string, limit int) ([]Candidate, error) {
		var out []Candidate
		for _, c := range queue {
			if c.Rel > after && len(out) < limit {
				out = append(out, c)
			}
		}
		return out, nil
	}
	rep, err := p.RunBatch(context.Background(), list,
		func(context.Context, string, Outcome) error { return nil }, "", Budget{PageSize: 10})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Skipped != 2 {
		t.Errorf("skipped = %d, want 2", rep.Skipped)
	}
	if rep.Refused != 1 {
		t.Errorf("refused = %d, want 1 — only the refusal gate's skips count here", rep.Refused)
	}
	if rep.Enriched != 1 {
		t.Errorf("enriched = %d, want 1", rep.Enriched)
	}
}

// The claim the whole thing exists for, end to end: a card the judge refused
// costs two model calls once and nothing on every night after, until its body
// or the prompt moves.
//
// Before this, eligibility was decided by the `enriched_at` stamp and a refused
// card never gets one, so the same nineteen cards came back every night at
// about forty cents each.
func TestARefusedCardCostsNothingOnTheNextRun(t *testing.T) {
	fp := &Fingerprint{Version: "enrich/1+prompt/aaa", RulesHash: "rules-1"}
	record, err := NewRefusals(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	card := Candidate{Rel: "memory/semantic/a.md",
		Raw: "---\ntype: reference\n---\n\nA card.\n"}
	list := func(_ context.Context, after string, _ int) ([]Candidate, error) {
		if after < card.Rel {
			return []Candidate{card}, nil
		}
		return nil, nil
	}
	write := func(context.Context, string, Outcome) error { return nil }

	night := func() (BatchReport, int64) {
		p := passWith(t, "body")
		p.AddPre(&Refused{Key: fp.Key, Standing: func(rel, key string) (Refusal, bool) {
			return record.Standing(rel, key, GatesVersion)
		}})
		p.AddPost(&stubGate{name: "grounding",
			err: fmt.Errorf("%w: the proposal asserts what the card does not", ErrNotEligible)})
		// What the command's observer does, in the two lines of it that matter.
		p.SetObserver(func(req Request, out Outcome, _ error) {
			if out.RefusedBy == "" {
				return
			}
			if err := record.Record(Refusal{
				Rel: req.Rel, Gate: out.RefusedBy, Key: fp.Key(req.Raw),
				Version: fp.Version, RulesHash: fp.RulesHash, Gates: GatesVersion,
				Reason: out.Reason,
			}); err != nil {
				t.Error(err)
			}
		})
		rep, err := p.RunBatch(context.Background(), list, write, "", Budget{PageSize: 10})
		if err != nil {
			t.Fatal(err)
		}
		return rep, p.Stats().Calls
	}

	first, calls := night()
	if calls != 1 {
		t.Fatalf("the first night made %d call(s), want 1 — the card had to be asked "+
			"about once for there to be a refusal at all", calls)
	}
	if first.Failed != 1 || first.Refused != 0 {
		t.Errorf("first night: failed %d refused %d, want 1 and 0",
			first.Failed, first.Refused)
	}

	second, calls := night()
	if calls != 0 {
		t.Errorf("the second night made %d call(s) over a card already refused; the "+
			"whole point is zero", calls)
	}
	if second.Refused != 1 || second.Enriched != 0 || second.Failed != 0 {
		t.Errorf("second night: refused %d enriched %d failed %d, want 1, 0 and 0",
			second.Refused, second.Enriched, second.Failed)
	}
	if n := record.Open(fp.Version, fp.RulesHash, GatesVersion); n != 1 {
		t.Errorf("open = %d, want 1", n)
	}
}
