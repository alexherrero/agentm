package enrich

import (
	"context"
	"errors"
	"strings"
	"testing"
)

// agentm-vault plan 04, task 2: the batch spends against the operator's line,
// reads what each call spent from the call's own envelope, and stops at the
// line or the guard saying which.

// meteredPass is a pass over the stub whose every call reports this usage.
func meteredPass(t *testing.T, o stubOpts) (*Pass, *Meter) {
	t.Helper()
	c := newStubCaller(t, o)
	m := NewMeter()
	c.Meter = m
	p := NewPass(c, 1)
	p.SetEnabled(true)
	return p, m
}

// The envelope is where a call states its spend; the meter adds it up by tier.
func TestACallsUsageIsReadFromItsEnvelope(t *testing.T) {
	c := newStubCaller(t, stubOpts{stdout: "hello", inTokens: 1200, outTokens: 300})
	m := NewMeter()
	c.Meter, c.Tier = m, TierCheap

	got, err := c.Call(context.Background(), "prompt")
	if err != nil {
		t.Fatalf("Call: %v", err)
	}
	if got != "hello" {
		t.Errorf("the model's text came back as %q, want the envelope's result", got)
	}
	u := m.Tier(TierCheap)
	if u.Calls != 1 || u.InputTokens != 1200 || u.OutputTokens != 300 || u.Tokens() != 1500 {
		t.Errorf("the cheap tier read %+v, want one call of 1200 in and 300 out", u)
	}
	if u.CostUSD <= 0 {
		t.Errorf("the call's total_cost_usd was not read: %v", u.CostUSD)
	}
	if s := m.Tier(TierStrong); s.Calls != 0 {
		t.Errorf("a cheap call was counted against the strong tier: %+v", s)
	}
}

// Output that is not the envelope is refused. Read as plain text it would
// carry zero usage, and a changed CLI format would switch the budget off
// without an error.
func TestOutputWithoutTheEnvelopeIsRefused(t *testing.T) {
	c := newStubCaller(t, stubOpts{stdout: `{"title":"t"}`, raw: true})
	_, err := c.Call(context.Background(), "prompt")
	if err == nil {
		t.Fatal("raw output was accepted; its usage cannot be read, so the token line cannot count it")
	}
	if !errors.Is(err, ErrNoResponse) || !strings.Contains(err.Error(), "envelope") {
		t.Errorf("the refusal does not say why: %v", err)
	}
}

// An error envelope — a lapsed login, a usage limit — carries its reason in
// `result`, and the error says it rather than "exited 1".
func TestAnErrorEnvelopeSaysWhy(t *testing.T) {
	c := newStubCaller(t, stubOpts{
		stdout: "Failed to authenticate: OAuth session expired", exit: 1,
	})
	m := NewMeter()
	c.Meter = m
	_, err := c.Call(context.Background(), "prompt")
	if err == nil || !strings.Contains(err.Error(), "OAuth session expired") {
		t.Fatalf("the failure does not carry the envelope's reason: %v", err)
	}
	if got := m.Total().Calls; got != 1 {
		t.Errorf("a failed call was not counted: %d calls", got)
	}
}

// The command asks for the envelope; the flag is what makes the usage exist.
func TestTheCommandAsksForTheJSONEnvelope(t *testing.T) {
	c := DefaultCaller("opus")
	args := strings.Join(c.command(context.Background(), "p", t.TempDir()).Args, " ")
	if !strings.Contains(args, "--output-format json") {
		t.Errorf("the call does not ask for --output-format json: %s", args)
	}
}

// The call guard stops the run and the report names it.
func TestTheCallGuardStopsTheRunAndSaysSo(t *testing.T) {
	p, m := meteredPass(t, stubOpts{stdout: "enriched", inTokens: 10, outTokens: 10})
	write, written := collector()
	rep, err := p.RunBatch(context.Background(), queue(fixture(10)), write, "",
		Budget{MaxCalls: 3, PageSize: 4, Meter: m, TokenLines: DefaultTokenLines()})
	if err != nil {
		t.Fatal(err)
	}
	if len(written()) != 3 || rep.ModelCalls != 3 {
		t.Errorf("wrote %d notes in %d calls against a 3-call guard", len(written()), rep.ModelCalls)
	}
	if !rep.Deferred || !strings.Contains(rep.StoppedBy, "call guard (3 calls)") {
		t.Errorf("stopped by %q, deferred %v — want the call guard, named", rep.StoppedBy, rep.Deferred)
	}
}

// The guard counts every call the night makes, the judge's included, not the
// notes sent — it is a runaway guard, and a loop that spends in small calls is
// what it exists to catch.
func TestTheCallGuardCountsTheJudgesCallsToo(t *testing.T) {
	p, m := meteredPass(t, stubOpts{stdout: "enriched", inTokens: 10, outTokens: 10})
	judgeCaller := newStubCaller(t, stubOpts{stdout: `{"grounded": true}`, inTokens: 5, outTokens: 1})
	judgeCaller.Meter = m
	judge := NewJudge(judgeCaller)
	p.AddPost(gateFunc("judge", func(req Request) error {
		_, err := judge.Judge(context.Background(), "q")
		return err
	}))
	write, written := collector()
	rep, err := p.RunBatch(context.Background(), queue(fixture(10)), write, "",
		Budget{MaxCalls: 4, PageSize: 4, Meter: m})
	if err != nil {
		t.Fatal(err)
	}
	if rep.ModelCalls != 4 || len(written()) != 2 || rep.Calls != 2 {
		t.Errorf("notes %d, note calls %d, model calls %d — want two notes, each a call "+
			"and a judge, stopping at the 4-call guard", len(written()), rep.Calls, rep.ModelCalls)
	}
}

// The token line stops the run before the note that would start over it, and
// says which tier's line it was.
func TestTheTokenLineStopsTheRunAndSaysWhichTier(t *testing.T) {
	p, m := meteredPass(t, stubOpts{stdout: "enriched", inTokens: 300, outTokens: 100})
	write, written := collector()
	rep, err := p.RunBatch(context.Background(), queue(fixture(10)), write, "",
		Budget{MaxCalls: 250, PageSize: 4, Meter: m,
			TokenLines: map[string]int64{TierStrong: 1000, TierCheap: 2000}})
	if err != nil {
		t.Fatal(err)
	}
	// 400 a note: after three the strong tier has spent 1,200, over its 1,000.
	if len(written()) != 3 {
		t.Errorf("wrote %d notes; the line should stop the run after the third", len(written()))
	}
	if !strings.Contains(rep.StoppedBy, "strong-tier token line (1,000 tokens)") {
		t.Errorf("stopped by %q, want the strong tier's line, named", rep.StoppedBy)
	}
	if rep.Tokens != 1200 || rep.Usage[TierStrong].Tokens() != 1200 {
		t.Errorf("the report reads %d tokens, strong %d; want 1,200 on the strong tier",
			rep.Tokens, rep.Usage[TierStrong].Tokens())
	}
	if rep.TotalCostUSD <= 0 {
		t.Error("the report carries no total_cost_usd, so the runner's spend line reads zero")
	}
}

// A note routed to the cheap tier spends from the cheap line, and the strong
// tier's line does not stop it.
func TestEachTierSpendsFromItsOwnLine(t *testing.T) {
	p, m := meteredPass(t, stubOpts{stdout: "enriched", inTokens: 300, outTokens: 100})
	p.SetRouter(func(d Depth) Route {
		if d == DepthLight {
			return Route{Model: "sonnet", Tier: TierCheap}
		}
		return Route{Model: "opus", Tier: TierStrong}
	})
	stamped := "---\nenriched_at: 2026-09-01T00:00:00Z\nenriched_by: " + PassVersion + "\n---\nbody\n"
	notes := make([]Candidate, 5)
	for i := range notes {
		notes[i] = Candidate{Rel: fixture(5)[i].Rel, Raw: stamped}
	}
	write, written := collector()
	rep, err := p.RunBatch(context.Background(), queue(notes), write, "",
		Budget{MaxCalls: 250, PageSize: 4, Meter: m,
			TokenLines: map[string]int64{TierStrong: 100, TierCheap: 100000}})
	if err != nil {
		t.Fatal(err)
	}
	if len(written()) != 5 || rep.StoppedBy != "" {
		t.Errorf("wrote %d of 5 light notes, stopped by %q — the strong line should "+
			"not stop cheap work", len(written()), rep.StoppedBy)
	}
	if m.Tier(TierCheap).Calls != 5 || m.Tier(TierStrong).Calls != 0 {
		t.Errorf("cheap %d calls, strong %d; want every light note on the cheap tier",
			m.Tier(TierCheap).Calls, m.Tier(TierStrong).Calls)
	}
}

// A model that has stopped answering stops the run rather than spending the
// window producing the same error for every note.
func TestARunOfFailedCallsStopsTheRun(t *testing.T) {
	p, m := meteredPass(t, stubOpts{stdout: "Failed to authenticate", exit: 1})
	write, _ := collector()
	rep, err := p.RunBatch(context.Background(), queue(fixture(20)), write, "",
		Budget{MaxCalls: 250, PageSize: 4, Meter: m, MaxFailuresInARow: 3})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Failed != 3 || rep.ModelCalls != 3 {
		t.Errorf("failed %d notes in %d calls; want the fuse at 3", rep.Failed, rep.ModelCalls)
	}
	if !strings.Contains(rep.StoppedBy, "3 notes in a row") {
		t.Errorf("stopped by %q, want the fuse, named", rep.StoppedBy)
	}
}

// A post-gate rejection is the model answering badly, not the model gone, and
// it does not burn the fuse.
func TestRejectedResponsesDoNotBurnTheFuse(t *testing.T) {
	p, m := meteredPass(t, stubOpts{stdout: "enriched", inTokens: 1, outTokens: 1})
	p.AddPost(gateFunc("reject", func(Request) error {
		return errors.New("rejected")
	}))
	write, _ := collector()
	rep, err := p.RunBatch(context.Background(), queue(fixture(6)), write, "",
		Budget{MaxCalls: 250, PageSize: 4, Meter: m, MaxFailuresInARow: 2})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Failed != 6 || rep.StoppedBy != "" {
		t.Errorf("failed %d, stopped by %q — six rejections should run to the end", rep.Failed, rep.StoppedBy)
	}
}

// The shipped budget is the operator's: 250 calls, a million strong, two
// million cheap.
func TestTheDefaultBudgetIsTheOperatorsLine(t *testing.T) {
	b := DefaultBudget()
	if b.MaxCalls != 250 || b.TokenLines[TierStrong] != 1_000_000 ||
		b.TokenLines[TierCheap] != 2_000_000 {
		t.Errorf("default budget %+v, want 250 calls, 1M strong, 2M cheap", b)
	}
}
