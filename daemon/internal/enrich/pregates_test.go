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
	// The rule the contract supplies: `Personal/` is never read by a background
	// model pass. Stated here rather than imported so the test says what it tests.
	mayRead := func(rel string) bool { return !strings.HasPrefix(rel, "Personal/") }
	g := DefaultEligibility(mayRead)

	for _, tc := range []struct {
		name, rel, body string
		want            bool // eligible
	}{
		{"an unfiled memory", "Agent/memory/semantic/x.md", note("unfiled", "b"), true},
		// Status is no longer a reason to refuse. A card is `active` because a
		// writer that knew said why, which says nothing about whether the pass
		// has ever run over it — and 283 notes in the corpus had been enriched
		// and scored below the floor while sitting `unfiled`.
		{"already active", "Agent/memory/semantic/x.md", note("active", "b"), true},
		{"superseded", "Agent/memory/semantic/x.md", note("superseded", "b"), true},
		{"no status at all", "Agent/memory/semantic/x.md", "no frontmatter", true},
		{"the operator's own space", "Personal/Church/x.md", note("unfiled", "b"), false},
		{"a derived class — entities", "Agent/memory/entities/x.md", note("unfiled", "b"), false},
		{"a derived class — crystallized", "Agent/memory/crystallized/x.md", note("unfiled", "b"), false},
		{"a derived class — mocs", "Agent/memory/mocs/x.md", note("unfiled", "b"), false},
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
	stamped := "---\ntitle: A note\nstatus: unfiled\nenriched_at: 2026-09-01T00:00:00Z\n---\n\nb\n"
	for _, tc := range []struct {
		name, body string
		want       Depth
	}{
		{"no stamp at all", note("unfiled", "b"), DepthDeep},
		{"no stamp, and active", note("active", "b"), DepthDeep},
		{"no frontmatter", "just prose\n", DepthDeep},
		{"stamped", stamped, DepthLight},
		{"stamped and active", strings.Replace(stamped, "unfiled", "active", 1), DepthLight},
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

	stamped := "---\ntitle: A note\nstatus: unfiled\nenriched_at: 2026-09-01T00:00:00Z\n---\n\nb\n"
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
		Rel: "Agent/memory/x.md", Raw: note("unfiled", "b"),
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
		Rel: "Agent/memory/mocs/x.md", Raw: note("unfiled", "b"),
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
