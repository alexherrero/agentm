package enrich

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"regexp"
	"strings"
	"sync/atomic"
	"time"
)

// The five deterministic pre-gates, in the order they run.
//
// All five are cheap and none calls a model. That ordering is the point: the
// expensive thing happens once every free thing has agreed it should. They run
// in the order registered, and the order is deliberate — eligibility first
// because it is a path comparison and rejects the most notes; the budget last
// because it is the only one whose answer depends on what the earlier gates
// already spent.
//
//	1. Eligibility   — is this note ours to touch at all?
//	2. Privacy       — does it carry something no model should see?
//	3. Size          — is it small enough to send whole?
//	4. Fingerprint   — have we already done exactly this?
//	5. Budget        — is there anything left to spend?

// --- 1. eligibility ---------------------------------------------------------

// Eligibility answers whether a note is enrichment's business.
//
// The path rules come from the filing contract rather than from constants here,
// because "which spaces a background model pass may read" is the operator's
// call and it is recorded in `standards/storage-rules.md` with the rest of the
// filing rules. Part 3 shipped `rules.MayReadWithModel`; this consumes it rather
// than re-deriving the same predicate, which would be a second thing to keep
// true.
type Eligibility struct {
	// MayRead is `rules.MayReadWithModel` — supplied rather than imported so
	// this package does not depend on the rules package, and so a test can
	// state the rule it is testing.
	MayRead func(rel string) bool
	// Statuses are the statuses a note must carry to be enriched. A note that
	// is already `active` has been judged; re-enriching it would overwrite a
	// decision with a guess.
	Statuses map[string]bool
	// ForbiddenDirs are path segments enrichment may never write into. The
	// three derived classes: `entities/`, `crystallized/` and `mocs/` are
	// produced by other passes from notes enrichment already touched, so
	// enriching them would feed the output of one pass back into its own input.
	ForbiddenDirs map[string]bool
}

// DefaultEligibility is the shipped rule set.
func DefaultEligibility(mayRead func(string) bool) *Eligibility {
	return &Eligibility{
		MayRead:  mayRead,
		Statuses: map[string]bool{"unfiled": true, "inbox": true},
		ForbiddenDirs: map[string]bool{
			"entities": true, "crystallized": true, "mocs": true,
		},
	}
}

func (g *Eligibility) Name() string { return "eligibility" }

func (g *Eligibility) Check(_ context.Context, req Request, body string) error {
	if g.MayRead != nil && !g.MayRead(req.Rel) {
		return fmt.Errorf("%w: %s is in a space no background model pass may read",
			ErrNotEligible, req.Rel)
	}
	for _, seg := range strings.Split(strings.ReplaceAll(req.Rel, "\\", "/"), "/") {
		if g.ForbiddenDirs[strings.ToLower(seg)] {
			return fmt.Errorf("%w: %s is a derived class enrichment may not write",
				ErrNotEligible, seg)
		}
	}
	status := frontmatterValue(body, "status")
	if len(g.Statuses) > 0 && !g.Statuses[strings.ToLower(status)] {
		if status == "" {
			status = "(none)"
		}
		return fmt.Errorf("%w: status is %s, not one waiting to be filed",
			ErrNotEligible, status)
	}
	return nil
}

// --- 2. privacy -------------------------------------------------------------

// Privacy refuses a note carrying a secret.
//
// It refuses rather than redacting, and that is the whole design of it. A
// redaction that removes a key and sends the rest is a judgment about what else
// in the note was sensitive, made by a regex — and the failure mode is silent
// and permanent, because the model has already seen it by the time anyone looks.
// A refusal costs one unenriched note.
//
// The patterns are deliberately few. This is a last line rather than a scanner:
// the repository already runs `check-no-pii` and gitleaks over what is
// committed, and a long pattern list here would mostly produce false refusals on
// a corpus full of notes *about* credentials.
type Privacy struct {
	Patterns []*regexp.Regexp
}

// DefaultPrivacy matches the shapes that are unambiguously a live secret rather
// than a note about one.
func DefaultPrivacy() *Privacy {
	return &Privacy{Patterns: []*regexp.Regexp{
		// Provider-issued keys, which carry their own prefix and are therefore
		// distinguishable from prose about keys.
		regexp.MustCompile(`\bsk-[A-Za-z0-9_-]{20,}`),
		regexp.MustCompile(`\bghp_[A-Za-z0-9]{36}\b`),
		regexp.MustCompile(`\bgithub_pat_[A-Za-z0-9_]{22,}`),
		regexp.MustCompile(`\bAKIA[0-9A-Z]{16}\b`),
		regexp.MustCompile(`\bxox[baprs]-[A-Za-z0-9-]{10,}`),
		// A PEM private key block. The header alone is enough; nothing writes
		// that line by accident.
		regexp.MustCompile(`-----BEGIN [A-Z ]*PRIVATE KEY-----`),
	}}
}

func (g *Privacy) Name() string { return "privacy" }

func (g *Privacy) Check(_ context.Context, req Request, body string) error {
	for _, re := range g.Patterns {
		if loc := re.FindStringIndex(body); loc != nil {
			// The matched text is deliberately not in the error. An error
			// message is written to a log, and a log is a place a secret should
			// not be moved to in the act of noticing it.
			return fmt.Errorf("%w: a credential-shaped string at byte %d; refusing "+
				"rather than redacting, because a redaction is a regex deciding "+
				"what else was sensitive", ErrNotEligible, loc[0])
		}
	}
	return nil
}

// --- 3. size ----------------------------------------------------------------

// Size keeps a note inside what one call can carry.
//
// It splits rather than truncating. A truncated note is enriched from a
// fragment and the result claims to be the whole thing, which is the quiet kind
// of wrong; a split leaves a note that is too big for now and says so, and task
// 9's additive splitting is what eventually turns it into several right-sized
// memories.
//
// The split point is a header boundary because that is where the corpus already
// divides, and because a window split through the middle of a section produces
// two halves that neither stands alone.
type Size struct {
	// MaxBytes is the largest note sent whole.
	MaxBytes int
}

// DefaultSize is generous. The ceiling exists to catch the 200,000-token
// `_meta` documents rather than to ration ordinary notes.
func DefaultSize() *Size { return &Size{MaxBytes: 32 * 1024} }

func (g *Size) Name() string { return "size" }

func (g *Size) Check(_ context.Context, req Request, body string) error {
	if len(body) <= g.MaxBytes {
		return nil
	}
	n := len(headerSections(body))
	return fmt.Errorf("%w: %d bytes over the %d-byte ceiling; it pre-splits into "+
		"%d section(s) and belongs to the splitting pass rather than to one call",
		ErrNotEligible, len(body), g.MaxBytes, n)
}

// headerSections counts the markdown sections a body would pre-split into.
//
// Exported behaviour rather than an internal detail because the size gate's
// refusal quotes the number, and a number in an error message that nobody can
// reproduce is a number nobody trusts.
func headerSections(body string) []string {
	var out []string
	var cur strings.Builder
	for _, line := range strings.Split(body, "\n") {
		if strings.HasPrefix(line, "#") && cur.Len() > 0 {
			out = append(out, cur.String())
			cur.Reset()
		}
		cur.WriteString(line)
		cur.WriteString("\n")
	}
	if cur.Len() > 0 {
		out = append(out, cur.String())
	}
	return out
}

// PassVersion identifies this pass — its code and its prompt together.
//
// Bump it when either changes. The prompt is part of it because the prompt
// carries the voice specification, and a voice change means every note enriched
// under the old one has not been enriched under the new one. That is how "a
// voice change re-queues work" is a mechanism rather than an intention: the
// version is in the idempotency key, so changing it invalidates the whole
// corpus's keys at once.
var PassVersion = "enrich/1+prompt/" + PromptHash()

// --- 4. fingerprint ---------------------------------------------------------

// Fingerprint is the idempotency gate, and it is the one that saves the money.
//
// The claim it makes true is exact: an unchanged note, at the current pass
// version and rules hash, makes **zero** model calls. Not one cheap call, not a
// cached response — zero, because the gate answers before the call exists.
//
// The version is part of the key on purpose. A prompt change is a different
// pass, and a note enriched by the old prompt has not been enriched by the new
// one. That is the mechanism by which "a voice change re-queues work" is true
// rather than aspirational: the prompt's hash is in the version, so changing the
// voice changes every note's key at once.
type Fingerprint struct {
	// Version identifies the pass — code version and prompt hash together.
	Version string
	// RulesHash identifies the filing contract the note was enriched against.
	RulesHash string
	// Seen answers "has this note, at this key, already been enriched?".
	// Supplied rather than owned because the record belongs to the ledger, which
	// is a later part; until then a caller can pass an in-memory set.
	Seen func(rel, key string) bool
}

func (g *Fingerprint) Name() string { return "fingerprint" }

func (g *Fingerprint) Check(_ context.Context, req Request, body string) error {
	if g.Seen == nil {
		return nil
	}
	key := g.Key(body)
	if g.Seen(req.Rel, key) {
		return fmt.Errorf("%w: already enriched at %s", ErrNotEligible, key[:12])
	}
	return nil
}

// Key is the idempotency key for a body under this pass version.
//
// Normalization is a port of `fingerprint.normalize_body` so the Go and Python
// halves agree on what "the same note" means: line endings unified, runs of
// horizontal whitespace collapsed, blank lines dropped, case folded. Two notes
// differing only in formatting share a key, which is what makes a reformatting
// pass free rather than a full re-enrichment.
func (g *Fingerprint) Key(body string) string {
	h := sha256.New()
	fmt.Fprintf(h, "v=%s;rules=%s;body=%s", g.Version, g.RulesHash, normalizeBody(body))
	return hex.EncodeToString(h.Sum(nil))
}

var wsRunRe = regexp.MustCompile(`[ \t\f\v]+`)

func normalizeBody(body string) string {
	body = strings.ReplaceAll(strings.ReplaceAll(body, "\r\n", "\n"), "\r", "\n")
	var lines []string
	for _, line := range strings.Split(body, "\n") {
		if c := wsRunRe.ReplaceAllString(strings.TrimSpace(line), " "); c != "" {
			lines = append(lines, c)
		}
	}
	return strings.ToLower(strings.Join(lines, "\n"))
}

// --- 5. budget --------------------------------------------------------------

// CycleBudget is the last gate, and it is last because its answer depends on what the
// four before it already let through.
//
// It defers rather than failing. An overrun is not an error — it is the pass
// working exactly as intended on a queue larger than one cycle — so the note
// stays `unfiled` and the cursor says where the next run picks up.
type CycleBudget struct {
	MaxCalls    int
	MaxDuration time.Duration

	calls   atomic.Int64
	started time.Time
}

// NewCycleBudget starts a budget window now.
func NewCycleBudget(maxCalls int, maxDuration time.Duration) *CycleBudget {
	return &CycleBudget{MaxCalls: maxCalls, MaxDuration: maxDuration, started: time.Now()}
}

func (g *CycleBudget) Name() string { return "budget" }

func (g *CycleBudget) Check(_ context.Context, _ Request, _ string) error {
	if g.MaxCalls > 0 && g.calls.Load() >= int64(g.MaxCalls) {
		return fmt.Errorf("%w: the cycle's %d-call budget is spent; deferred to the "+
			"next run", ErrNotEligible, g.MaxCalls)
	}
	if g.MaxDuration > 0 && !g.started.IsZero() &&
		time.Since(g.started) >= g.MaxDuration {
		return fmt.Errorf("%w: the cycle's %s window has closed; deferred to the "+
			"next run", ErrNotEligible, g.MaxDuration)
	}
	// Counted here rather than after the call, because the gate is the last
	// thing between this note and the spend. Counting afterwards would let a
	// crash between the two lose the record and re-spend it.
	g.calls.Add(1)
	return nil
}

// Spent is how many calls this cycle has authorized.
func (g *CycleBudget) Spent() int64 { return g.calls.Load() }

// --- shared -----------------------------------------------------------------

// FrontmatterValue reads one scalar key out of a note's frontmatter block.
//
// Exported because the ledger's rebuild reads this package's own stamps back
// out of the corpus, and a second parser of the same three fields is a second
// thing to keep true. The rebuild has to agree with the writer about what
// `enriched_by` means down to the whitespace, and the only way to guarantee that
// is to use the writer's reader.
func FrontmatterValue(raw, key string) string { return frontmatterValue(raw, key) }

// frontmatterValue reads one scalar key out of a note's frontmatter block.
//
// Frontmatter only, not the body: a note whose prose contains the line
// `status: unfiled` inside a fenced example is talking about a status, not
// carrying one, and this corpus is full of notes about its own frontmatter.
func frontmatterValue(raw, key string) string {
	if !strings.HasPrefix(raw, "---") {
		return ""
	}
	rest := raw[3:]
	i := strings.Index(rest, "\n---")
	if i < 0 {
		return ""
	}
	for _, line := range strings.Split(rest[:i], "\n") {
		k, v, ok := strings.Cut(line, ":")
		if ok && strings.EqualFold(strings.TrimSpace(k), key) {
			return strings.Trim(strings.TrimSpace(v), `'"`)
		}
	}
	return ""
}
