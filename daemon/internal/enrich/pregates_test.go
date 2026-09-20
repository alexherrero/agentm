package enrich

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

func note(status, body string) string {
	return "---\ntitle: A note\nstatus: " + status + "\n---\n\n" + body
}

// --- eligibility ------------------------------------------------------------

func TestEligibilityRefusesWhatIsNotEnrichmentsBusiness(t *testing.T) {
	// The rule the contract supplies: `personal/` is never read by a background
	// model pass. Stated here rather than imported so the test says what it tests.
	mayRead := func(rel string) bool { return !strings.HasPrefix(rel, "personal/") }
	g := DefaultEligibility(mayRead)
	// The contract's `record_kinds` register, as the command wires it.
	g.IsRecordKind = func(k string) bool { return k == "session-trace" || k == "dir-index" }
	trace := "---\ntitle: a session\nkind: session-trace\nstatus: active\n---\n\n## Asked\n"
	card := "---\ntitle: a card\nkind: preference\nstatus: active\n---\n\nb\n"

	for _, tc := range []struct {
		name, rel, body string
		want            bool // eligible
	}{
		{"an unfiled memory", "agent/memory/semantic/x.md", note("unfiled", "b"), true},
		// Status is no longer a reason to refuse. A card is `active` because a
		// writer that knew said why, which says nothing about whether the pass
		// has ever run over it — and 283 notes in the corpus had been enriched
		// and scored below the floor while sitting `unfiled`.
		{"already active", "agent/memory/semantic/x.md", note("active", "b"), true},
		{"superseded", "agent/memory/semantic/x.md", note("superseded", "b"), true},
		{"no status at all", "agent/memory/semantic/x.md", "no frontmatter", true},
		{"the operator's own space", "personal/Church/x.md", note("unfiled", "b"), false},
		{"a derived class — entities", "agent/memory/entities/x.md", note("unfiled", "b"), false},
		{"a derived class — crystallized", "agent/memory/crystallized/x.md", note("unfiled", "b"), false},
		{"a derived class — mocs", "agent/memory/mocs/x.md", note("unfiled", "b"), false},
		// A record is its writer's shape, not a card: a pass that re-rendered a
		// trace's frontmatter would drop its session, day and touched fields.
		{"a session trace (a record kind)", "agent/memory/episodic/t.md", trace, false},
		{"a card whose kind is not a record", "agent/memory/semantic/c.md", card, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			err := g.Check(context.Background(), Request{Rel: tc.rel}, tc.body)
			if tc.want && err != nil {
				t.Errorf("refused an eligible note: %v", err)
			}
			if !tc.want {
				if err == nil {
					t.Error("accepted a note it should refuse")
				} else if !errors.Is(err, ErrNotEligible) {
					t.Errorf("refused with the wrong error kind: %v", err)
				}
			}
		})
	}
}

// The deep pass is owed once, and the stamp is what says whether it has
// happened. Status said nothing about it: a card is `active` because a writer
// that knew said why, and `unfiled` because none did.
func TestTheDepthComesFromTheStampNotTheStatus(t *testing.T) {
	// Stamped by this pass: the light pass is what a moved card is owed.
	stamped := "---\ntitle: A note\nstatus: unfiled\nenriched_by: " + PassVersion +
		"\nenriched_at: 2026-09-01T00:00:00Z\n---\n\nb\n"
	for _, tc := range []struct {
		name, body string
		want       Depth
	}{
		{"no stamp at all", note("unfiled", "b"), DepthDeep},
		{"no stamp, and active", note("active", "b"), DepthDeep},
		{"no frontmatter", "just prose\n", DepthDeep},
		{"stamped", stamped, DepthLight},
		{"stamped and active", strings.Replace(stamped, "unfiled", "active", 1), DepthLight},
		// A prompt change re-owes the deep pass (agentm-vault § Dreaming): a
		// stamp from an older prompt is not this pass's judgment.
		{"stamped by an older pass", strings.Replace(stamped, PassVersion,
			"enrich/1+prompt/a73ff0f4f5dc", 1), DepthDeep},
		{"stamped with no version", strings.Replace(stamped,
			"enriched_by: "+PassVersion+"\n", "", 1), DepthDeep},
		{"an empty stamp is no stamp", strings.Replace(stamped,
			"enriched_at: 2026-09-01T00:00:00Z", "enriched_at:", 1), DepthDeep},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := PassDepth(tc.body); got != tc.want {
				t.Errorf("PassDepth = %v, want %v", got, tc.want)
			}
		})
	}
}

// A note whose *prose* mentions a stamp is talking about one, not carrying one.
// This corpus is full of notes about its own frontmatter, so reading the body
// would make every note about enrichment look enriched.
func TestTheDepthReadsFrontmatterNotProse(t *testing.T) {
	body := "---\ntitle: About enrichment\nstatus: unfiled\n---\n\n" +
		"A note records its pass by writing\n\n```yaml\nenriched_at: 2026-09-01T00:00:00Z\n```\n\ninto its head.\n"
	if got := PassDepth(body); got != DepthDeep {
		t.Errorf("a note *about* `enriched_at` was read as carrying one: %v", got)
	}
}

// The pass reads the depth off the note, not off the caller. A caller that
// asked for a deep pass over an already-enriched note would be asking the
// model to redo work the stamp says is done.
func TestTheRequestsDepthIsOverwrittenByTheNote(t *testing.T) {
	seen := make(chan Depth, 1)
	p := passWith(t, "body")
	p.AddPre(gateFunc("record", func(req Request) error {
		seen <- req.Depth
		return nil
	}))

	// Stamped by this pass — a stamp from an older one is owed the deep pass.
	stamped := "---\ntitle: A note\nstatus: unfiled\nenriched_by: " + PassVersion +
		"\nenriched_at: 2026-09-01T00:00:00Z\n---\n\nb\n"
	if _, err := p.Run(context.Background(), Request{
		Rel: "x.md", Raw: stamped, Depth: DepthDeep,
	}); err != nil {
		t.Fatalf("run: %v", err)
	}
	if got := <-seen; got != DepthLight {
		t.Errorf("the gate saw depth %v; the note carries a stamp and is owed %v",
			got, DepthLight)
	}
}

// --- privacy ----------------------------------------------------------------

func TestPrivacyRefusesRatherThanRedacts(t *testing.T) {
	g := DefaultPrivacy()
	secret := "sk-" + strings.Repeat("a", 40)
	body := note("unfiled", "Here is the key: "+secret+"\nand more prose after it.\n")

	err := g.Check(context.Background(), Request{Rel: "x.md"}, body)
	if err == nil {
		t.Fatal("a note carrying a live key was sent to the model")
	}
	if !errors.Is(err, ErrNotEligible) {
		t.Errorf("wrong error kind: %v", err)
	}
	// The point of refusing over redacting: nothing partial goes anywhere. And
	// the secret must not be moved into a log in the act of noticing it.
	if strings.Contains(err.Error(), secret) {
		t.Errorf("the refusal quoted the secret into an error message: %v", err)
	}
}

func TestPrivacyPassesANoteMerelyAboutCredentials(t *testing.T) {
	g := DefaultPrivacy()
	// The corpus is full of these. A scanner tuned tight enough to catch them
	// would refuse most of the notes worth enriching.
	body := note("unfiled",
		"Store the API key in the keychain, never in frontmatter. A `sk-` prefix "+
			"means an OpenAI key; `ghp_` a GitHub token.\n")
	if err := g.Check(context.Background(), Request{Rel: "x.md"}, body); err != nil {
		t.Errorf("a note about credentials was refused as one: %v", err)
	}
}

func TestPrivacyCoversEachShape(t *testing.T) {
	g := DefaultPrivacy()
	for name, secret := range map[string]string{
		"openai": "sk-" + strings.Repeat("x", 32),
		"github": "ghp_" + strings.Repeat("A", 36),
		"pat":    "github_pat_" + strings.Repeat("B", 30),
		"aws":    "AKIA" + strings.Repeat("C", 16),
		"slack":  "xoxb-" + strings.Repeat("1", 20),
		"pem":    "-----BEGIN RSA PRIVATE KEY-----",
	} {
		t.Run(name, func(t *testing.T) {
			if err := g.Check(context.Background(), Request{Rel: "x.md"},
				note("unfiled", secret)); err == nil {
				t.Errorf("%s slipped through", name)
			}
		})
	}
}

// --- size -------------------------------------------------------------------

func TestSizeDefersRatherThanTruncating(t *testing.T) {
	g := &Size{MaxBytes: 100}
	big := note("unfiled", "# One\n\n"+strings.Repeat("word ", 40)+"\n# Two\n\nmore\n")

	err := g.Check(context.Background(), Request{Rel: "x.md"}, big)
	if err == nil {
		t.Fatal("an oversized note was sent whole")
	}
	// The refusal has to say how it would split, because the number is what
	// makes the deferral actionable rather than a dead end.
	if !strings.Contains(err.Error(), "section") {
		t.Errorf("the refusal does not say how it pre-splits: %v", err)
	}
	if g.Check(context.Background(), Request{Rel: "x.md"}, note("unfiled", "short")) != nil {
		t.Error("an ordinary note was refused by the size ceiling")
	}
}

// --- settle -----------------------------------------------------------------

func TestSettleLeavesACardThatIsStillArriving(t *testing.T) {
	now := time.Date(2026, 9, 19, 22, 0, 0, 0, time.UTC)
	mod := map[string]time.Time{
		"agent/inbox/just-landed.md": now.Add(-30 * time.Second),
		"agent/inbox/settled.md":     now.Add(-2 * time.Hour),
		// Same recency, in a class directory: written by this machine under the
		// vault lock, so the rule does not apply to it.
		"agent/memory/semantic/local.md": now.Add(-30 * time.Second),
	}
	g := &Settle{
		Dir:     "agent/inbox/",
		Window:  5 * time.Minute,
		Now:     func() time.Time { return now },
		ModTime: func(rel string) (time.Time, error) { return mod[rel], nil },
	}
	err := g.Check(context.Background(), Request{Rel: "agent/inbox/just-landed.md"}, "")
	if err == nil {
		t.Fatal("a card that changed 30s ago was offered to a model")
	}
	if !errors.Is(err, ErrNotEligible) {
		t.Errorf("the refusal is not an eligibility refusal: %v", err)
	}
	// The refusal has to say it is about timing, or a reader takes it for a
	// judgment about the card.
	for _, want := range []string{"settle window", "offered again tomorrow"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the refusal never says %q: %v", want, err)
		}
	}
	if g.Check(context.Background(), Request{Rel: "agent/inbox/settled.md"}, "") != nil {
		t.Error("a card two hours old was refused")
	}
	if g.Check(context.Background(), Request{Rel: "agent/memory/semantic/local.md"}, "") != nil {
		t.Error("the settle rule reached outside the drop folder")
	}
}

func TestSettleOffersTheCardWhenItCannotTellTheTime(t *testing.T) {
	// A failing *timing* check must never become a reason a card is never
	// enriched at all: the cost of enriching one card early is a rewrite the
	// journal can undo, and the cost of the other direction is a card that
	// waits forever for a stat that keeps failing.
	boom := &Settle{
		Dir:     "agent/inbox/",
		ModTime: func(string) (time.Time, error) { return time.Time{}, errors.New("no") },
	}
	if boom.Check(context.Background(), Request{Rel: "agent/inbox/a.md"}, "") != nil {
		t.Error("an unreadable mtime refused the card")
	}
	// And an unconfigured gate covers nothing rather than everything: a vault
	// with no drop folder gets the behaviour it had before the folder existed.
	for _, g := range []*Settle{nil, {}, {Dir: "agent/inbox/"}} {
		if g.Check(context.Background(), Request{Rel: "agent/inbox/a.md"}, "") != nil {
			t.Errorf("an unconfigured settle gate refused a card: %+v", g)
		}
	}
}

func TestHeaderSectionsSplitsAtHeadings(t *testing.T) {
	got := headerSections("intro\n# One\na\n## Two\nb\n")
	if len(got) != 3 {
		t.Errorf("split into %d sections, want 3 (preamble, One, Two): %q", len(got), got)
	}
	// A note with no headings is one section, which is 94% of this corpus.
	if n := len(headerSections("just a body\nwith lines\n")); n != 1 {
		t.Errorf("a headingless note split into %d sections", n)
	}
}

// --- fingerprint ------------------------------------------------------------

// The claim this gate makes true, stated as the number it is: zero.
func TestAnUnchangedNoteCostsZeroCalls(t *testing.T) {
	seen := map[string]bool{}
	fp := &Fingerprint{
		Version: "v1", RulesHash: "abc",
		Seen: func(_, key string) bool { return seen[key] },
	}
	body := note("unfiled", "The staging gate runs first.\n")

	p := passWith(t, "enriched")
	p.AddPre(fp)

	out, err := p.Run(context.Background(), Request{Rel: "x.md", Raw: body})
	if err != nil {
		t.Fatalf("first run: %v", err)
	}
	if !out.Enriched {
		t.Fatalf("the first run did not enrich: %+v", out)
	}
	seen[fp.Key(body)] = true

	out, err = p.Run(context.Background(), Request{Rel: "x.md", Raw: body})
	if err != nil {
		t.Fatalf("second run: %v", err)
	}
	if out.Calls != 0 {
		t.Errorf("re-running an unchanged note cost %d call(s); the claim is zero",
			out.Calls)
	}
	if !out.Skipped {
		t.Errorf("an already-enriched note was not skipped: %+v", out)
	}
	if p.Stats().Calls != 1 {
		t.Errorf("the pass spent %d calls across two runs of one note",
			p.Stats().Calls)
	}
}

// Formatting is not content. A reformatting pass over the corpus must not
// re-enrich everything it touched.
func TestFormattingVariantsShareAKey(t *testing.T) {
	fp := &Fingerprint{Version: "v1", RulesHash: "abc"}
	a := "The  gate   runs\n\n\nfirst.\n"
	b := "The gate runs\nfirst."
	if fp.Key(a) != fp.Key(b) {
		t.Errorf("two formatting variants of the same text have different keys")
	}
	if fp.Key(a) == fp.Key("The gate runs second.") {
		t.Error("different content shares a key")
	}
}

// A voice change is a version bump that re-queues work. That is the mechanism,
// and this is the assertion that makes it a mechanism rather than an intention.
func TestAPromptChangeRequeuesEveryNote(t *testing.T) {
	body := note("unfiled", "The staging gate runs first.\n")
	v1 := &Fingerprint{Version: "v1-prompthashA", RulesHash: "r1"}
	v2 := &Fingerprint{Version: "v2-prompthashB", RulesHash: "r1"}
	if v1.Key(body) == v2.Key(body) {
		t.Error("a prompt change left the key unchanged, so a voice change would " +
			"silently apply to new notes only")
	}
	// And so does a contract change, for the same reason.
	rules2 := &Fingerprint{Version: "v1-prompthashA", RulesHash: "r2"}
	if v1.Key(body) == rules2.Key(body) {
		t.Error("a rules change left the key unchanged")
	}
}

// --- budget -----------------------------------------------------------------

func TestTheCycleBudgetDefersRatherThanFails(t *testing.T) {
	g := NewCycleBudget(2, 0)
	for i := 0; i < 2; i++ {
		if err := g.Check(context.Background(), Request{}, ""); err != nil {
			t.Fatalf("call %d refused inside the budget: %v", i, err)
		}
	}
	err := g.Check(context.Background(), Request{}, "")
	if err == nil {
		t.Fatal("the third call was authorized against a budget of two")
	}
	if !errors.Is(err, ErrNotEligible) {
		t.Errorf("an overrun was reported as a failure rather than a deferral: %v", err)
	}
	if !strings.Contains(err.Error(), "deferred") {
		t.Errorf("the refusal does not say the work is deferred: %v", err)
	}
}

// Counted before the call, not after. Counting afterwards lets a crash between
// the two lose the record and re-spend it.
func TestTheBudgetCountsBeforeTheCallNotAfter(t *testing.T) {
	g := NewCycleBudget(5, 0)
	if err := g.Check(context.Background(), Request{}, ""); err != nil {
		t.Fatal(err)
	}
	if g.Spent() != 1 {
		t.Errorf("spent = %d immediately after authorizing one call; the budget "+
			"counts after the fact, so a crash loses the record", g.Spent())
	}
}

func TestATimeWindowClosesTheBudget(t *testing.T) {
	g := NewCycleBudget(0, 30*time.Millisecond)
	if err := g.Check(context.Background(), Request{}, ""); err != nil {
		t.Fatalf("refused inside the window: %v", err)
	}
	time.Sleep(50 * time.Millisecond)
	if err := g.Check(context.Background(), Request{}, ""); err == nil {
		t.Error("the window never closed")
	}
}

// --- the sandwich -----------------------------------------------------------

// The order is the specification, and the reason for it is that the expensive
// thing happens only after every free thing has agreed.
func TestTheFiveGatesRunInTheSpecifiedOrder(t *testing.T) {
	var order []string
	wrap := func(g Gate) Gate {
		return gateFunc(g.Name(), func(Request) error {
			order = append(order, g.Name())
			return nil
		})
	}
	p := passWith(t, "enriched")
	p.AddPre(
		wrap(DefaultEligibility(nil)),
		wrap(DefaultPrivacy()),
		wrap(DefaultSize()),
		wrap(&Fingerprint{}),
		wrap(NewCycleBudget(10, 0)),
	)
	if _, err := p.Run(context.Background(), Request{
		Rel: "agent/memory/x.md", Raw: note("unfiled", "b"),
	}); err != nil {
		t.Fatalf("run: %v", err)
	}
	want := "eligibility,privacy,size,fingerprint,budget"
	if got := strings.Join(order, ","); got != want {
		t.Errorf("gates ran %q, want %q", got, want)
	}
}

// An early gate declining means the later ones never run — which is what makes
// "the expensive thing happens last" true of cost and not just of order.
func TestAnEarlyDeclineShortCircuitsTheRest(t *testing.T) {
	later := &stubGate{name: "later"}
	p := passWith(t, "enriched")
	p.AddPre(DefaultEligibility(nil), later)

	out, err := p.Run(context.Background(), Request{
		// A derived class: produced by another pass from notes enrichment
		// already touched, so enriching it feeds a pass its own output.
		Rel: "agent/memory/mocs/x.md", Raw: note("unfiled", "b"),
	})
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if !out.Skipped {
		t.Fatal("an ineligible note was not skipped")
	}
	if later.calls() != 0 {
		t.Errorf("a later gate ran %d time(s) after an earlier one declined",
			later.calls())
	}
	if out.Calls != 0 {
		t.Errorf("a declined note cost %d call(s)", out.Calls)
	}
}

// --- self-probe -------------------------------------------------------------

// The probe note is the daemon's own test fixture, and the pass has no business
// judging it. On 2026-09-17 it judged one: the rewrite spent a call summarizing
// a synthetic note and dropped the `probe:` marker on the way out.
//
// The marker is the whole identity. Retirement reads it back off the file
// before deleting yesterday's card, so a stripped card is never retired and the
// vault gains one a night. That is why this refuses rather than trusting the
// carry: a note that reaches the model at all has already lost the argument.
func TestTheSelfProbeIsRefusedBeforeAnyModelCall(t *testing.T) {
	g := &SelfProbe{}
	probe := func(marker string) string {
		return "---\ntitle: AgentM self-probe 2026-09-17T06:11:19Z\ntype: reference\n" +
			"status: active\naliases: [\"pa412db75aab66\"]\n" + marker + "\n---\n\n" +
			"Synthetic round-trip probe written by the daemon.\n"
	}
	for _, tc := range []struct {
		name, body string
		want       bool // eligible
	}{
		// What the daemon actually writes.
		{"the marker as capture writes it", probe("probe: self-probe"), false},
		// `true` and `yes` are accepted so a note marked by hand is not
		// silently judged — the parser's rule, not a second one here.
		{"marked by hand as true", probe("probe: true"), false},
		{"marked by hand as yes", probe("probe: yes"), false},
		{"quoted", probe(`probe: "self-probe"`), false},
		{"the marker upper-cased", probe("probe: SELF-PROBE"), false},
		// An ordinary memory is untouched by this gate, including one that
		// talks about the probe: the marker is read out of the frontmatter
		// block, and this corpus is full of notes about its own frontmatter.
		{"an ordinary card", note("unfiled", "a thought worth keeping"), true},
		{"a card about the probe", note("unfiled", "the daemon writes `probe: self-probe`"), true},
		{"a card whose marker says no", probe("probe: false"), true},
		{"no frontmatter at all", "just prose\n", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			err := g.Check(context.Background(), Request{
				Rel: "agent/memory/semantic/x.md",
			}, tc.body)
			if tc.want && err != nil {
				t.Errorf("refused a note that is not a probe: %v", err)
			}
			if !tc.want {
				if err == nil {
					t.Fatal("accepted the daemon's own synthetic note")
				}
				if !errors.Is(err, ErrNotEligible) {
					t.Errorf("refused with the wrong error kind: %v", err)
				}
				if !strings.Contains(err.Error(), "probe") {
					t.Errorf("the refusal does not name the marker: %v", err)
				}
			}
		})
	}
}

// The refusal is a skip, not a failure and not work still owed: zero calls
// spent, the gate named, and the note left exactly as the daemon wrote it.
func TestARefusedProbeCostsNothingAndIsCountedAsASkip(t *testing.T) {
	p := passWith(t, "a summary no probe asked for")
	p.AddPre(&SelfProbe{})

	raw := "---\ntitle: AgentM self-probe\ntype: reference\nstatus: active\n" +
		"probe: self-probe\n---\n\nSynthetic round-trip probe.\n"
	out, err := p.Run(context.Background(), Request{
		Rel: "agent/memory/semantic/agentm-self-probe-2026-09-17t06-11-19z.md", Raw: raw,
	})
	if err != nil {
		t.Fatalf("a refusal was reported as an error: %v", err)
	}
	if !out.Skipped {
		t.Error("the probe was not marked skipped")
	}
	if out.Enriched {
		t.Error("the probe was rewritten")
	}
	if out.Calls != 0 || p.Stats().Calls != 0 {
		t.Errorf("the probe cost %d model calls (the pass counted %d)",
			out.Calls, p.Stats().Calls)
	}
	if out.SkippedBy != (&SelfProbe{}).Name() {
		t.Errorf("the skip names %q, not the gate that made it", out.SkippedBy)
	}
	if out.Body != "" {
		t.Errorf("a skip must not hand back a body:\n%s", out.Body)
	}
}
