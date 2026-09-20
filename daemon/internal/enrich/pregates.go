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

	// Aliased because the package's own tests declare a helper named `note`,
	// and Go lets no identifier be declared in both a file block and the
	// package block.
	notes "github.com/alexherrero/agentm/daemon/internal/note"
)

// The six deterministic pre-gates, in the order they run.
//
// All six are cheap and none calls a model. That ordering is the point: the
// expensive thing happens once every free thing has agreed it should. They run
// in the order registered, and the order is deliberate — the self-probe first
// because it is the only one asking whether the thing in front of it is a
// memory at all, and a question about what a note *is* comes before every
// question about where it sits or what it costs; eligibility next because it is
// a path comparison and rejects the most notes; the budget last because it is
// the only one whose answer depends on what the earlier gates already spent.
//
//	1. Self-probe    — is this a memory at all, or the daemon's own synthetic note?
//	2. Eligibility   — is this note ours to touch at all?
//	3. Privacy       — does it carry something no model should see?
//	4. Size          — is it small enough to send whole?
//	5. Fingerprint   — have we already done exactly this?
//	6. Budget        — is there anything left to spend?

// --- 1. self-probe ----------------------------------------------------------

// SelfProbe refuses the note the daemon writes to prove itself.
//
// The probe writes a synthetic memory once a day, asks for it back sideways and
// records whether it came. The note it leaves behind is the artifact of that
// check, not a memory: nobody wrote it, nothing was learned, and there is
// nothing in it for a judge to be right or wrong about. On the night of
// 2026-09-17 the pass judged one anyway — it spent a call summarizing the
// daemon's own test fixture, and the rewrite dropped the `probe:` marker that
// said what the note was.
//
// Losing that marker is the part that matters, and it is why this refuses
// rather than letting the carry handle it. The marker is the probe's identity:
// the next run retires yesterday's note by reading it back off the file, the
// card gate exempts a probe from naming a transport by reading it, and the
// classifier excludes a probe from every measurement by reading it. A note that
// arrives without it is a probe to nobody — so a stripped card is never
// retired, and one more arrives every night behind it.
//
// It runs first because it is the only gate asking what the note is rather than
// where it sits. A probe lives in a class directory that a model may read, at a
// size no ceiling catches, under a fingerprint nothing has seen — it passes
// every other gate on the merits, which is exactly how it got judged.
type SelfProbe struct{}

func (g *SelfProbe) Name() string { return "probe" }

// Check reads the marker through the corpus's own parser rather than a second
// reader of the same key: `note.Parse` decides what counts as a probe
// everywhere else — for retirement, for the classifier, for the card gate — and
// a gate that disagreed with it by a quoted value or a letter's case would
// refuse a different set of notes than the one the rest of the daemon calls
// synthetic.
func (g *SelfProbe) Check(_ context.Context, req Request, body string) error {
	if !notes.Parse(req.Rel, body, time.Time{}).Probe {
		return nil
	}
	return fmt.Errorf("%w: it carries `%s:` — the daemon's synthetic self-probe, "+
		"which is a test fixture and not a memory; enriching it spends a call on "+
		"nothing and a rewrite that dropped the marker would leave a note no "+
		"later run could recognize as a probe", ErrNotEligible, notes.ProbeMarker)
}

// --- 1b. settle -------------------------------------------------------------

// DefaultSettleWindow is how recently a drop-folder card may have changed and
// still be left alone for the night. Minutes rather than seconds, because a
// DriveFS download of a file a phone is still editing is not one event.
const DefaultSettleWindow = 5 * time.Minute

// Settle refuses a card that is still arriving.
//
// The drop folder has two writers and one file. A card reaches it over Google
// Drive, written by a chat surface on some other device, and the night rewrites
// what it finds there — so a run that starts while DriveFS is still bringing a
// file down reads half a card, enriches the half, and writes the result back
// over the whole one when the rest lands. The card the operator wrote is gone
// and nothing reports it, because from the pass's side every byte of that
// sequence was ordinary.
//
// Refusing anything touched inside the window costs one night on one card, and
// the card is still there in the morning. There is no cheaper check that is
// also honest: the pass cannot ask Drive whether a file is finished, and a
// content heuristic ("does this parse?") passes on the truncation that matters
// most — a card cut off after its frontmatter.
//
// Scoped by path rather than applied to everything, because it is a statement
// about *this folder's* transport. A card in a class directory was written by
// this machine, under the vault lock, and delaying it would be superstition.
type Settle struct {
	// Dir is the path prefix the rule covers, e.g. `agent/inbox/`. Empty
	// disables the gate, which is the honest state for a vault with no drop
	// folder rather than a rule that silently covers everything.
	Dir string
	// Window is how recently is too recently. Zero means DefaultSettleWindow.
	Window time.Duration
	// ModTime reports when the note last changed. Supplied so this package does
	// not own the vault; a nil ModTime, or one that answers with an error,
	// leaves the card offered — the failure of a *timing* check must not become
	// a reason a card is never enriched at all.
	ModTime func(rel string) (time.Time, error)
	// Now is the clock, injectable for the tests.
	Now func() time.Time
}

func (g *Settle) Name() string { return "settle" }

func (g *Settle) Check(_ context.Context, req Request, _ string) error {
	if g == nil || g.Dir == "" || g.ModTime == nil {
		return nil
	}
	if !strings.HasPrefix(req.Rel, g.Dir) {
		return nil
	}
	mod, err := g.ModTime(req.Rel)
	if err != nil {
		return nil
	}
	window := g.Window
	if window <= 0 {
		window = DefaultSettleWindow
	}
	now := time.Now
	if g.Now != nil {
		now = g.Now
	}
	age := now().Sub(mod)
	if age >= window {
		return nil
	}
	return fmt.Errorf("%w: it changed %s ago, inside the %s settle window — a "+
		"card syncing down from Drive may still be arriving, and enriching half "+
		"a card writes the half back over the whole one; it is offered again "+
		"tomorrow", ErrNotEligible, age.Round(time.Second), window)
}

// --- 2. eligibility ---------------------------------------------------------

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
	// ForbiddenDirs are path segments enrichment may never write into. The
	// three derived classes: `entities/`, `crystallized/` and `mocs/` are
	// produced by other passes from notes enrichment already touched, so
	// enriching them would feed the output of one pass back into its own input.
	ForbiddenDirs map[string]bool
	// IsRecordKind is the contract's `record_kinds` register — supplied rather
	// than imported, like MayRead. A note whose `kind` is a record kind is a
	// record (a session trace, a directory index, a digest), not a card: its
	// shape is its writer's, and a pass that re-rendered its frontmatter would
	// drop the fields that shape is made of.
	IsRecordKind func(string) bool
	// ProjectRecord is the place rule inside the projects space (agentm-vault
	// § Projects and tasks): a pass writes a project's charter and its
	// decisions/, designs/ and research/ notes, and nothing else there.
	// Supplied like MayRead; nil leaves the projects space to the other rules.
	ProjectRecord func(rel string) bool
	// IsWalled is `rules.IsRecallExempt` — the contract's `recall_exempt_areas`.
	// Supplied like MayRead, and checked first: the wall is not a ranking rule
	// or a space policy but an area the corpus does not hold at all, and a pass
	// that read one to summarize it would be the exact leak the wall exists to
	// prevent. The queue should never offer such a path, since the index holds
	// no row for it — this is the refusal for the day something else does.
	IsWalled func(rel string) bool
}

// DefaultEligibility is the shipped rule set.
func DefaultEligibility(mayRead func(string) bool) *Eligibility {
	return &Eligibility{
		MayRead: mayRead,
		ForbiddenDirs: map[string]bool{
			"entities": true, "crystallized": true, "mocs": true,
		},
	}
}

func (g *Eligibility) Name() string { return "eligibility" }

func (g *Eligibility) Check(_ context.Context, req Request, body string) error {
	if g.IsWalled != nil && g.IsWalled(req.Rel) {
		return fmt.Errorf("%w: %s is in an area walled from the corpus entirely",
			ErrNotEligible, req.Rel)
	}
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
	if g.ProjectRecord != nil && InProjectsSpace(req.Rel) {
		if !g.ProjectRecord(req.Rel) {
			return fmt.Errorf("%w: %s is project state a pass does not write; only a "+
				"charter and decisions/, designs/ and research/ notes take its section",
				ErrNotEligible, req.Rel)
		}
		// A record keeps its writer's shape, so the record-kind refusal does not
		// apply: ComposeRecord merges into the record rather than rendering a card.
		return nil
	}
	if kind := strings.TrimSpace(frontmatterValue(body, "kind")); kind != "" &&
		g.IsRecordKind != nil && g.IsRecordKind(kind) {
		return fmt.Errorf("%w: kind %q is a record, not a card", ErrNotEligible, kind)
	}
	// No status check. Eligibility is a question about the stamp, not about
	// the verdict — see PassDepth. What this replaces refused any note that
	// was not `unfiled`, on the reasoning that an `active` note had been
	// judged; the corpus then held 283 notes that had been enriched and scored
	// below the floor, and a further set that was `active` because a writer
	// asserted it rather than because anything read it. Status said nothing
	// about whether the pass had run.
	return nil
}

// Depth says how much of the pass a note is owed.
type Depth int

const (
	// DepthDeep is the whole pass. A note with no `enriched_at` has never been
	// through it, whatever its status says.
	DepthDeep Depth = iota
	// DepthLight is the pass over a note that has been enriched before and has
	// moved since. Its shape is the night's to decide; what is decided here is
	// which notes are owed which.
	DepthLight
)

func (d Depth) String() string {
	switch d {
	case DepthDeep:
		return "deep"
	case DepthLight:
		return "light"
	}
	return fmt.Sprintf("depth(%d)", int(d))
}

// PassDepth reads the stamp and says what the note is owed.
//
// The third case — enriched before and unchanged since — is not this
// function's to answer and is deliberately not one of its return values. The
// fingerprint gate already refuses it for free, keyed on the pass version, the
// rules hash and the body together, so a note that has genuinely not moved
// costs zero model calls and a note whose *prompt* moved is correctly owed
// another pass. Answering it twice, in two places, is how the two answers
// start to disagree.
//
// A stamp from an older pass is owed the deep pass again, not the light one:
// "a prompt change re-owes the deep pass to every note" (agentm-vault
// § Dreaming). The version is the prompt's hash, so a stamp that names a
// different version was judged by a different prompt, and what this prompt
// asks for — the neighbours, `related`, `importance_proposed` — that judgment
// never gave.
func PassDepth(body string) Depth {
	if strings.TrimSpace(frontmatterValue(body, "enriched_at")) == "" {
		return DepthDeep
	}
	if strings.TrimSpace(frontmatterValue(body, "enriched_by")) != PassVersion {
		return DepthDeep
	}
	return DepthLight
}

// --- 3. privacy -------------------------------------------------------------

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

// Clean reports whether text carries none of the credential shapes. For text
// that is going into a prompt beside the card — a neighbour's title and
// summary — where the answer to a match is to leave that text out.
func (g *Privacy) Clean(text string) bool {
	for _, re := range g.Patterns {
		if re.MatchString(text) {
			return false
		}
	}
	return true
}

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

// --- 4. size ----------------------------------------------------------------

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

// --- 5. fingerprint ---------------------------------------------------------

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

// --- 6. budget --------------------------------------------------------------

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
