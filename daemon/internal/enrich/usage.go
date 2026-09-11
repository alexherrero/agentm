package enrich

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"sync"
)

// What a call spent, read from the call itself.
//
// `claude -p --output-format json` answers with an envelope: the model's text
// in `result`, and beside it `usage` and `total_cost_usd`. That envelope is the
// only place a call's spend is ever stated, so the pass reads it on every call
// rather than estimating from byte counts. Before this, nothing recorded what
// enrichment cost at all — the runner's spend line read $0.0000 every night
// because no job printed a number for it to read.
//
// The budget this feeds is a token line per night per tier: one million on the
// strong tier or two million on the cheap tier, with a 250-call guard. Those
// numbers are the operator's (agentm-vault § Dreaming, session 3, Q2); the code
// holds them and a flag may lower them, never raise them.

// Usage is one call's spend, or a sum of several.
type Usage struct {
	InputTokens         int64   `json:"input_tokens"`
	CacheCreationTokens int64   `json:"cache_creation_input_tokens"`
	CacheReadTokens     int64   `json:"cache_read_input_tokens"`
	OutputTokens        int64   `json:"output_tokens"`
	CostUSD             float64 `json:"total_cost_usd"`
	Calls               int     `json:"calls"`
}

// Tokens is every token the call processed, cache reads included. It is what
// the run reports and what a reader compares against a bill.
func (u Usage) Tokens() int64 {
	return u.InputTokens + u.CacheCreationTokens + u.CacheReadTokens + u.OutputTokens
}

// Added is what the token line counts: the tokens a call actually added, which
// is everything except the cached prefix it re-read.
//
// The line used to count cache reads too, on the reasoning that counting them
// in errs towards stopping early. Measured against the live corpus on
// 2026-09-11, that reasoning inverted: every call re-reads the same ~37,000
// tokens of Claude Code's own baseline, so a card cost ~107,000 tokens of which
// ~74,000 were the same prefix twice. The line stopped the night after nine
// cards while the corpus needed a hundred and eighty-one, and what it was
// measuring was one constant, over and over, rather than the night's work. The
// operator's number did not change; what it counts did (agentm-vault plan 04,
// the first supervised batch).
func (u Usage) Added() int64 {
	return u.InputTokens + u.CacheCreationTokens + u.OutputTokens
}

// Add sums two readings.
func (u Usage) Add(o Usage) Usage {
	return Usage{
		InputTokens:         u.InputTokens + o.InputTokens,
		CacheCreationTokens: u.CacheCreationTokens + o.CacheCreationTokens,
		CacheReadTokens:     u.CacheReadTokens + o.CacheReadTokens,
		OutputTokens:        u.OutputTokens + o.OutputTokens,
		CostUSD:             u.CostUSD + o.CostUSD,
		Calls:               u.Calls + o.Calls,
	}
}

// String is the one-line reading a person sees per call and per night. It
// carries both numbers: what the call processed, and the part of it the line
// counts.
func (u Usage) String() string {
	return fmt.Sprintf("%s tokens (in %d · cache read %d · cache write %d · out %d · "+
		"%s against the line) · $%.4f",
		commas(u.Tokens()), u.InputTokens, u.CacheReadTokens, u.CacheCreationTokens,
		u.OutputTokens, commas(u.Added()), u.CostUSD)
}

// envelope is the part of `--output-format json` the pass reads.
type envelope struct {
	Type         string  `json:"type"`
	Result       *string `json:"result"`
	IsError      bool    `json:"is_error"`
	Subtype      string  `json:"subtype"`
	TotalCostUSD float64 `json:"total_cost_usd"`
	Usage        *struct {
		InputTokens         int64 `json:"input_tokens"`
		CacheCreationTokens int64 `json:"cache_creation_input_tokens"`
		CacheReadTokens     int64 `json:"cache_read_input_tokens"`
		OutputTokens        int64 `json:"output_tokens"`
	} `json:"usage"`
}

// parseEnvelope reads a call's stdout as the JSON envelope.
//
// Anything else is refused rather than read as plain text. A call whose usage
// cannot be read is a call the token line cannot count, and a fallback that
// treated it as zero would let a changed CLI format switch the budget off
// without a single error.
func parseEnvelope(stdout string) (string, Usage, error) {
	if strings.TrimSpace(stdout) == "" {
		return "", Usage{}, fmt.Errorf("%w: the call printed nothing", ErrNoResponse)
	}
	var e envelope
	if err := json.Unmarshal([]byte(strings.TrimSpace(stdout)), &e); err != nil ||
		e.Result == nil || e.Usage == nil {
		return "", Usage{}, fmt.Errorf("%w: the call did not answer with the "+
			"--output-format json envelope, so its usage cannot be read and the "+
			"token line cannot count it: %q", ErrNoResponse, truncate(stdout, 200))
	}
	u := Usage{
		InputTokens:         e.Usage.InputTokens,
		CacheCreationTokens: e.Usage.CacheCreationTokens,
		CacheReadTokens:     e.Usage.CacheReadTokens,
		OutputTokens:        e.Usage.OutputTokens,
		CostUSD:             e.TotalCostUSD,
		Calls:               1,
	}
	if e.IsError {
		return "", u, fmt.Errorf("enrich: the call reported an error: %s",
			truncate(strings.TrimSpace(*e.Result), 400))
	}
	return *e.Result, u, nil
}

// CallRecord is one call as the meter saw it.
type CallRecord struct {
	Label string
	Model string
	Tier  string
	Usage Usage
	Err   error
}

// Meter adds up what every call spent, by tier.
//
// Shared by every Caller a run uses — the pass and its faithfulness judge both
// spend from the same night — so the token line reads one number rather than
// one per caller. Safe for concurrent use.
type Meter struct {
	mu     sync.Mutex
	byTier map[string]Usage
	// OnCall, when set, is told about every call as it finishes. The batch
	// prints a line per call from it.
	OnCall func(CallRecord)
}

// NewMeter starts an empty meter.
func NewMeter() *Meter { return &Meter{byTier: map[string]Usage{}} }

// Record adds one call.
func (m *Meter) Record(r CallRecord) {
	if m == nil {
		return
	}
	m.mu.Lock()
	m.byTier[r.Tier] = m.byTier[r.Tier].Add(r.Usage)
	cb := m.OnCall
	m.mu.Unlock()
	if cb != nil {
		cb(r)
	}
}

// Tier is what one tier has spent so far.
func (m *Meter) Tier(tier string) Usage {
	if m == nil {
		return Usage{}
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.byTier[tier]
}

// Total is what every tier has spent together.
func (m *Meter) Total() Usage {
	if m == nil {
		return Usage{}
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	var t Usage
	for _, u := range m.byTier {
		t = t.Add(u)
	}
	return t
}

// ByTier is a copy of the per-tier readings.
func (m *Meter) ByTier() map[string]Usage {
	out := map[string]Usage{}
	if m == nil {
		return out
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	for k, v := range m.byTier {
		out[k] = v
	}
	return out
}

// Tiers is the tiers that spent, in a stable order.
func (m *Meter) Tiers() []string {
	byTier := m.ByTier()
	out := make([]string, 0, len(byTier))
	for k := range byTier {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// The operator's nightly budget (agentm-vault § Dreaming, session 3, Q2;
// raised to one line of 2,000,000 on 2026-09-11).
//
// The lines started apart — a million strong or two million cheap — because
// the pair was written as one night's money at two prices. They are now the
// same number, so the night's allowance is two million tokens whichever tier
// spends them, and the tier decides what those tokens cost rather than how
// many there are. Both constants stay, because a later ruling may part them
// again and the metering reads a line per tier either way.
const (
	// StrongTokenLine is the most the strong tier may spend in one night.
	StrongTokenLine int64 = 2_000_000
	// CheapTokenLine is the most the cheap tier may spend in one night.
	CheapTokenLine int64 = 2_000_000
	// CallGuard stops a run after this many model calls whatever the tokens
	// say. A runaway guard, not the budget: it counts every call, the
	// faithfulness judge's included, because a loop that spends in small
	// calls is exactly what it exists to catch.
	CallGuard = 250
)

// TierStrong and TierCheap name the two tiers the lines are kept for. The same
// strings the tier table uses.
const (
	TierStrong = "strong"
	TierCheap  = "cheap"
)

// DefaultTokenLines is the operator's line per tier.
func DefaultTokenLines() map[string]int64 {
	return map[string]int64{TierStrong: StrongTokenLine, TierCheap: CheapTokenLine}
}

func commas(n int64) string {
	s := fmt.Sprint(n)
	if n < 0 {
		return s
	}
	var b strings.Builder
	for i, r := range s {
		if i > 0 && (len(s)-i)%3 == 0 {
			b.WriteByte(',')
		}
		b.WriteRune(r)
	}
	return b.String()
}
