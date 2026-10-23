package ideajudge

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"
)

// The trace in testdata/ is what episodic_trace.py renders, byte for byte
// (scripts/test_episodic_trace.py `TheIdeasSectionIsTheJudgesShape` renders it
// again and compares), so the reader below is tested against the writer's
// real output, not against a shape this file invented.
func fixtureTrace(t *testing.T) string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("testdata", "trace.md"))
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

var now = time.Date(2026, 10, 17, 9, 30, 0, 0, time.UTC)

const playon = "We should also look into automating the PlayOn recordings, as that's tedious by hand."

const streamingCard = "---\ntitle: \"Streaming, but not 4K Blu-ray yet\"\ntype: idea\narea: home-tech\nstatus: active\n" +
	"slug: streaming-but-not-4k-bluray-yet\n---\n\nStream the everyday catalogue, keep buying 4K discs.\n"

// vault is a vault with the memory root `agent`, one idea card, and the traces
// given, each written as `memory/episodic/<name>`.
func vault(t *testing.T, traces map[string]string) string {
	t.Helper()
	v := t.TempDir()
	for _, d := range []string{".obsidian", "agent/memory/episodic", "agent/inbox", "personal/ideas"} {
		if err := os.MkdirAll(filepath.Join(v, d), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	write(t, v, "personal/ideas/streaming-but-not-4k-bluray-yet.md", streamingCard)
	for name, text := range traces {
		write(t, v, "agent/memory/episodic/"+name, text)
	}
	return v
}

func write(t *testing.T, v, rel, text string) {
	t.Helper()
	p := filepath.Join(v, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

func read(t *testing.T, v, rel string) string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(v, filepath.FromSlash(rel)))
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

// trace is a session trace carrying the given `## Ideas` lines.
func trace(session string, entries ...string) string {
	return "---\ntitle: \"a session\"\nkind: session-trace\ncreated: 2026-10-16\nday: 2026-10-16\nsession: \"" +
		session + "\"\nslug: s-" + session + "\n---\n\n## Asked\n\nsomething\n\n## Ideas\n\n```jsonl\n" +
		strings.Join(entries, "\n") + "\n```\n"
}

func entry(remark, message, filedKey string) string {
	return fmt.Sprintf(`{"remark": %q, "rule": "explicit follow-up suggestion", "first_request": false, `+
		`"transcript": "~/.claude/projects/p/s.jsonl", "message": %q, "context": "operator: %s", "filed_key": %q}`,
		remark, message, strings.ReplaceAll(remark, `"`, `'`), filedKey)
}

// fake answers each prompt by the first key its remark contains, and counts
// the calls.
type fake struct {
	replies map[string]string
	calls   int
	prompts []string
}

func (f *fake) call(prompt string) (string, error) {
	f.calls++
	f.prompts = append(f.prompts, prompt)
	remark := strings.SplitN(strings.TrimPrefix(prompt, "## The remark\n\n"), "\n", 2)[0]
	// The longest key first, then by name: a remark holding two keys gets the
	// same reply on every run, whatever order the map gives.
	keys := make([]string, 0, len(f.replies))
	for k := range f.replies {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		if len(keys[i]) != len(keys[j]) {
			return len(keys[i]) > len(keys[j])
		}
		return keys[i] < keys[j]
	})
	for _, k := range keys {
		if strings.Contains(remark, k) {
			return f.replies[k], nil
		}
	}
	return `{"verdict": "steering", "reason": "it directs the session"}`, nil
}

func decide(t *testing.T, v string, f *fake, st *State, cap int) Plan {
	t.Helper()
	opt := Options{Vault: v, MemoryRoot: "agent", Cap: cap, Now: now, Ledger: map[string]bool{}}
	if f != nil {
		opt.Call = f.call
	}
	plan, err := Decide(opt, st)
	if err != nil {
		t.Fatal(err)
	}
	return plan
}

// apply writes a plan's intents as the journal would: a creation lands, an
// edit lands only over the bytes it was planned against.
func apply(t *testing.T, v string, plan Plan) []string {
	t.Helper()
	var landed []string
	for _, in := range plan.Intents {
		p := filepath.Join(v, "agent", filepath.FromSlash(in.Rel))
		cur, err := os.ReadFile(p)
		if in.Before == nil {
			if err == nil {
				t.Fatalf("%s: a creation over an existing note", in.Rel)
			}
		} else if string(cur) != string(in.Before) {
			t.Fatalf("%s: planned against other bytes", in.Rel)
		}
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, in.After, 0o644); err != nil {
			t.Fatal(err)
		}
		landed = append(landed, in.Rel)
	}
	return landed
}

func TestTheReaderReadsTheTraceTheWriterWrites(t *testing.T) {
	v := vault(t, map[string]string{"2026-10-16-how-far-has-the-playon-job-got-5b2c8e10.md": fixtureTrace(t)})
	got, err := Gather(v, "agent")
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 {
		t.Fatalf("%d remarks, want 2: %+v", len(got), got)
	}
	r := got[0]
	if r.Remark != playon || r.Message != "u2-0f3c9a7e" || r.Session != "5b2c8e10-aaaa" || r.Day != "2026-10-16" ||
		r.FirstRequest || r.FiledKey != "we-should-also-look-into-automating|0123456789abcdef" ||
		!strings.HasPrefix(r.Context, "assistant: The Netflix films") || r.Trace != "memory/episodic/2026-10-16-how-far-has-the-playon-job-got-5b2c8e10.md" {
		t.Errorf("the first remark read as %+v", r)
	}
	if !got[1].FirstRequest || !strings.Contains(got[1].Remark, "``` numbers from #676 and [[a-link]]") {
		t.Errorf("the second remark read as %+v", got[1])
	}
}

func TestEachVerdictWritesWhatItShouldAndNothingElse(t *testing.T) {
	v := vault(t, map[string]string{"a.md": trace("s1",
		entry("We should also update the tests for the parser later.", "m1", ""),
		entry("Stream the catalogue, but the Apple TV does Dolby Vision now so revisit it later.", "m2", ""),
		entry(playon, "m3", ""))})
	f := &fake{replies: map[string]string{
		"Dolby Vision": `{"verdict": "repeat", "slug": "streaming-but-not-4k-bluray-yet", "reason": "the streaming idea"}`,
		"PlayOn":       `{"verdict": "new", "title": "Automate the PlayOn recordings", "area": "home-tech", "reason": "a project"}`,
	}}
	st := &State{}
	plan := decide(t, v, f, st, 0)
	if plan.Count(Steering) != 1 || plan.Count(Repeat) != 1 || plan.Count(New) != 1 || f.calls != 3 {
		t.Fatalf("verdicts %+v, %d calls", plan.Rows, f.calls)
	}
	if len(plan.Intents) != 2 {
		t.Fatalf("%d intents, want 2 (the steering remark writes nothing)", len(plan.Intents))
	}
	apply(t, v, plan)

	card := read(t, v, "personal/ideas/streaming-but-not-4k-bluray-yet.md")
	if !strings.HasPrefix(card, streamingCard) {
		t.Errorf("the card above the addition changed:\n%s", card)
	}
	if !strings.Contains(card, "\n## Added by dreaming\n\n**2026-10-17 · [[a]] · message m2**\n\nStream the catalogue") {
		t.Errorf("the repeat is not under a dated, sourced heading:\n%s", card)
	}

	inbox, _ := filepath.Glob(filepath.Join(v, "agent", "inbox", "*.md"))
	if len(inbox) != 1 || filepath.Base(inbox[0]) != "automate-the-playon-recordings.md" {
		t.Fatalf("inbox holds %v", inbox)
	}
	newCard := read(t, v, "agent/inbox/automate-the-playon-recordings.md")
	for _, want := range []string{"title: \"Automate the PlayOn recordings\"\n", "type: idea\n", "area: home-tech\n",
		"status: unfiled\n", "source: conversation\n", "derived_from: [\"[[a]]\"]\n", "\n" + playon + "\n"} {
		if !strings.Contains(newCard, want) {
			t.Errorf("the new card lacks %q:\n%s", want, newCard)
		}
	}
	ideas, _ := filepath.Glob(filepath.Join(v, "personal", "ideas", "*.md"))
	if len(ideas) != 1 {
		t.Errorf("a card was created in personal/ideas/: %v", ideas)
	}
	for _, line := range strings.Split(read(t, v, "agent/memory/episodic/a.md"), "\n") {
		if strings.Contains(line, "verdict") {
			t.Errorf("the trace was written: %q", line)
		}
	}
}

func TestTheCapIsHonouredAndTheOverflowIsReportedNotDropped(t *testing.T) {
	var entries []string
	for i := 0; i < 25; i++ {
		entries = append(entries, entry(fmt.Sprintf("We should also try variant %d of the queue report later.", i),
			fmt.Sprintf("m%02d", i), ""))
	}
	v := vault(t, map[string]string{"a.md": trace("s1", entries...)})
	f := &fake{}
	st := &State{}
	plan := decide(t, v, f, st, 20)
	if f.calls != 20 || plan.Deferred != 5 || plan.Count(Unjudged) != 5 {
		t.Fatalf("%d calls, %d deferred, %d unjudged; want 20, 5, 5", f.calls, plan.Deferred, plan.Count(Unjudged))
	}
	f2 := &fake{}
	next := decide(t, v, f2, st, 20)
	if f2.calls != 5 || next.Deferred != 0 || next.Cached != 20 {
		t.Errorf("the next night made %d calls with %d deferred and %d cached; want 5, 0, 20", f2.calls, next.Deferred, next.Cached)
	}
}

func TestARemovedRemarkCostsNoCall(t *testing.T) {
	gone := "1-agree-2-private-on-github|9b93bd74c0b2d27d"
	home := "streaming-but-not-4k-bluray-yet|aaaaaaaaaaaaaaaa"
	v := vault(t, map[string]string{"a.md": trace("s1",
		entry("1. agree, 2. private on github ok, and later we could move the jellyfin template.", "m1", gone),
		entry("Stream the catalogue, but revisit it later.", "m2", home))})
	f := &fake{}
	st := &State{}
	opt := Options{Vault: v, MemoryRoot: "agent", Now: now, Call: f.call, Ledger: map[string]bool{gone: true, home: true}}
	plan, err := Decide(opt, st)
	if err != nil {
		t.Fatal(err)
	}
	if plan.Removed != 1 || f.calls != 1 {
		t.Fatalf("%d removed, %d calls; want 1 removed (its card is gone) and 1 call (its card is home)", plan.Removed, f.calls)
	}
	for _, p := range f.prompts {
		if strings.Contains(p, "jellyfin") {
			t.Error("the removed remark was sent to the judge")
		}
	}
}

func TestAnAppendNeverRewritesAnExistingSection(t *testing.T) {
	card := streamingCard + "\n## Added by capture\n\n**2026-09-28 · conversation**\n\nThe Apple TV handles Dolby Vision.\n" +
		"\n## Added by dreaming\n\n**2026-10-01 · [[old]]**\n\nAn earlier repeat.\n\n## Notes\n\nThe operator's own notes.\n"
	r := Remark{Trace: "memory/episodic/b.md", Remark: "Revisit 4K once streaming bitrates catch up.", Message: "m9"}
	got := WithAddition(card, r, "2026-10-17")
	head := card[:strings.Index(card, "\n## Notes\n")]
	if !strings.HasPrefix(got, head) {
		t.Fatalf("text before the addition changed:\n%s", got)
	}
	if !strings.HasSuffix(got, "\n## Notes\n\nThe operator's own notes.\n") {
		t.Errorf("the section after it changed:\n%s", got)
	}
	if strings.Count(got, AddedByDreaming) != 1 || !strings.Contains(got, "An earlier repeat.\n\n**2026-10-17 · [[b]] · message m9**\n\nRevisit 4K") {
		t.Errorf("the addition is not at the end of the night's section:\n%s", got)
	}
	if WithAddition(got, r, "2026-10-18") != "" {
		t.Error("the same words were added twice")
	}
}

func TestANewCardNeverLandsInPersonalIdeas(t *testing.T) {
	v := vault(t, map[string]string{"a.md": trace("s1", entry(playon, "m1", ""))})
	// The judge proposes a title whose slug an idea card already holds, and
	// names a card for a repeat that is not on the list.
	f := &fake{replies: map[string]string{"PlayOn": `{"verdict": "new", "title": "Streaming but not 4K Bluray yet"}`}}
	plan := decide(t, v, f, &State{}, 0)
	if len(plan.Intents) != 1 || !strings.HasPrefix(plan.Intents[0].Rel, "inbox/") ||
		plan.Intents[0].Rel == "inbox/streaming-but-not-4k-bluray-yet.md" {
		t.Fatalf("intents %+v", plan.Intents)
	}
	v2 := vault(t, map[string]string{"a.md": trace("s1", entry(playon, "m1", ""))})
	f2 := &fake{replies: map[string]string{"PlayOn": `{"verdict": "repeat", "slug": "playon-automation"}`}}
	plan2 := decide(t, v2, f2, &State{}, 0)
	if plan2.Count(New) != 1 || len(plan2.Intents) != 1 || !strings.HasPrefix(plan2.Intents[0].Rel, "inbox/") {
		t.Errorf("a repeat of a card not on the list: %+v %+v", plan2.Rows, plan2.Intents)
	}
	for _, in := range append(plan.Intents, plan2.Intents...) {
		if strings.Contains(in.Rel, "personal/") {
			t.Errorf("an intent reaches personal/: %s", in.Rel)
		}
	}
}

func TestAReportingNightsVerdictsAreWrittenWhenTheWritesAreOn(t *testing.T) {
	v := vault(t, map[string]string{"a.md": trace("s1", entry(playon, "m1", ""))})
	f := &fake{replies: map[string]string{"PlayOn": `{"verdict": "new", "title": "Automate the PlayOn recordings"}`}}
	st := &State{}
	report := decide(t, v, f, st, 0)
	if len(report.Intents) != 1 {
		t.Fatalf("the reporting night planned %d writes", len(report.Intents))
	}
	// Reporting: the plan is not applied. The next night applies the cached
	// verdict without asking again.
	f2 := &fake{}
	night := decide(t, v, f2, st, 0)
	if f2.calls != 0 || len(night.Intents) != 1 {
		t.Fatalf("%d calls, %d writes; want 0 and 1", f2.calls, len(night.Intents))
	}
	if written := MarkApplied(&night, st, apply(t, v, night), now); len(written) != 1 ||
		written[0] != "agent/inbox/automate-the-playon-recordings.md" {
		t.Errorf("MarkApplied returned %v, want the one inbox card", written)
	}
	third := decide(t, v, &fake{}, st, 0)
	if len(third.Intents) != 0 || !third.Rows[0].Applied {
		t.Errorf("an applied verdict was planned again: %+v", third.Rows)
	}
}

// A repeat whose words the card already holds is settled, and nothing is
// written for it: not an intent, not a write MarkApplied counts, and not a
// write a later night repeats. The supervised report-only run of 2026-10-16
// found two such repeats reported as written.
func TestARepeatTheCardAlreadyHoldsIsHomeNotWritten(t *testing.T) {
	held := "Stream the everyday catalogue, keep buying 4K discs."
	v := vault(t, map[string]string{"a.md": trace("s1", entry(held, "m1", ""))})
	f := &fake{replies: map[string]string{"Stream": `{"verdict": "repeat", "slug": "streaming-but-not-4k-bluray-yet"}`}}
	st := &State{}
	plan := decide(t, v, f, st, 0)
	if len(plan.Intents) != 0 || !plan.Rows[0].Home || plan.Rows[0].Applied {
		t.Fatalf("intents %d, row %+v; want no intent, home, not applied", len(plan.Intents), plan.Rows[0])
	}
	if written := MarkApplied(&plan, st, nil, now); len(written) != 0 {
		t.Errorf("written %v", written)
	}
	next := decide(t, v, &fake{}, st, 0)
	if len(next.Intents) != 0 || !next.Rows[0].Home || next.Rows[0].Applied {
		t.Errorf("the next night: %+v", next.Rows[0])
	}
}

func TestAFirstRequestRemarkIsMarkedInThePrompt(t *testing.T) {
	r := Remark{Remark: "idea: a blog post per shipped plan.", FirstRequest: true, Context: "operator: idea: …"}
	p := Prompt(r, []Card{{Slug: "streaming-but-not-4k-bluray-yet", Title: "Streaming", Area: "home-tech", Inbox: true}})
	for _, want := range []string{"first request", "streaming-but-not-4k-bluray-yet — Streaming [home-tech] (waiting for review)",
		"operator: idea: …"} {
		if !strings.Contains(p, want) {
			t.Errorf("the prompt lacks %q:\n%s", want, p)
		}
	}
}

func TestAVerdictThatIsNoneOfTheThreeIsAnError(t *testing.T) {
	if _, err := ParseVerdict(`{"verdict": "maybe"}`); err == nil {
		t.Error("an unknown verdict parsed")
	}
	if v, err := ParseVerdict("Here you go: {\"verdict\": \"New\", \"title\": \" X \"}"); err != nil || v.Verdict != New || v.Title != "X" {
		t.Errorf("%+v %v", v, err)
	}
}

// The release review of 2026-10-22 (task 192 step 9) found the cases below,
// each failing before its fix.

// A remembered repeat whose card was renamed before the writes went on is
// judged again against today's list, and lands on the card under its new
// name; it never errors night after night, unwritten.
func TestARememberedRepeatWhoseCardIsGoneIsJudgedAgain(t *testing.T) {
	v := vault(t, map[string]string{"a.md": trace("s1", entry("Stream it, but revisit Dolby Vision later.", "m1", ""))})
	st := &State{}
	decide(t, v, &fake{replies: map[string]string{"Dolby": `{"verdict": "repeat", "slug": "streaming-but-not-4k-bluray-yet"}`}}, st, 0)
	if err := os.Rename(filepath.Join(v, "personal/ideas/streaming-but-not-4k-bluray-yet.md"),
		filepath.Join(v, "personal/ideas/streaming-4k.md")); err != nil {
		t.Fatal(err)
	}
	f := &fake{replies: map[string]string{"Dolby": `{"verdict": "repeat", "slug": "streaming-4k"}`}}
	plan := decide(t, v, f, st, 0)
	if len(plan.Errors) != 0 || f.calls != 1 || len(plan.Intents) != 1 ||
		plan.Intents[0].Rel != "../personal/ideas/streaming-4k.md" {
		t.Fatalf("errors %v, calls %d, intents %+v; want one call and the repeat on the renamed card", plan.Errors, f.calls, plan.Intents)
	}
	if again := decide(t, v, &fake{}, st, 0); len(again.Errors) != 0 || len(again.Intents) != 1 {
		t.Errorf("the next night: errors %v, %d intents", again.Errors, len(again.Intents))
	}
}

// An inbox card the judge made and the operator deleted at review is not
// carded again when the same words come up in another session: no call, no
// card.
func TestAnInboxCardTheOperatorDeletedIsNotCardedAgain(t *testing.T) {
	v := vault(t, map[string]string{"a.md": trace("s1", entry(playon, "m1", ""))})
	newIdea := `{"verdict": "new", "title": "Automate the PlayOn recordings"}`
	st := &State{}
	night1 := decide(t, v, &fake{replies: map[string]string{"PlayOn": newIdea}}, st, 0)
	MarkApplied(&night1, st, apply(t, v, night1), now)
	if err := os.Remove(filepath.Join(v, "agent/inbox/automate-the-playon-recordings.md")); err != nil {
		t.Fatal(err)
	}
	write(t, v, "agent/memory/episodic/b.md", trace("s2", entry(playon, "m7", "")))
	f := &fake{replies: map[string]string{"PlayOn": newIdea}}
	night2 := decide(t, v, f, st, 0)
	if f.calls != 0 || len(night2.Intents) != 0 || night2.Removed != 1 {
		t.Errorf("calls %d, intents %+v, removed %d; want the deleted idea dropped without a call", f.calls, night2.Intents, night2.Removed)
	}
}

// A repeat its card came to hold after it was judged is home, even beside a
// second repeat of the same card that is written: the held one is not
// reported as written.
func TestAHeldRepeatBesideAWrittenOneIsHome(t *testing.T) {
	held := "Keep a shelf of the 4K discs worth the upgrade."
	fresh := "Stream it, but revisit Dolby Vision later."
	v := vault(t, map[string]string{"a.md": trace("s1", entry(held, "m1", ""))})
	rep := `{"verdict": "repeat", "slug": "streaming-but-not-4k-bluray-yet"}`
	st := &State{}
	decide(t, v, &fake{replies: map[string]string{"shelf": rep}}, st, 0) // a reporting night
	write(t, v, "personal/ideas/streaming-but-not-4k-bluray-yet.md", streamingCard+"\n"+held+"\n")
	write(t, v, "agent/memory/episodic/a.md", trace("s1", entry(held, "m1", ""), entry(fresh, "m2", "")))
	plan := decide(t, v, &fake{replies: map[string]string{"Dolby": rep}}, st, 0)
	written := MarkApplied(&plan, st, apply(t, v, plan), now)
	for _, r := range plan.Rows {
		if r.Remark == held && (r.Applied || !r.Home) {
			t.Errorf("the held repeat reads as written: applied=%v home=%v", r.Applied, r.Home)
		}
		if r.Remark == fresh && !r.Applied {
			t.Errorf("the fresh repeat was not written")
		}
	}
	if len(written) != 1 {
		t.Errorf("written %v", written)
	}
}

// A verdict parses whatever is around it: a note after it that holds a
// brace, a `[[link]]` before it, or an array around it (the second review of
// the fix found the last two broken by the first fix).
func TestAVerdictParsesWhateverIsAroundIt(t *testing.T) {
	for _, out := range []string{
		"{\"verdict\": \"steering\", \"reason\": \"directs the session\"}\n\n" +
			"(If it comes up again it could become an idea, e.g. {automate-playon}.)",
		"Repeat of [[streaming-but-not-4k-bluray-yet]]:\n{\"verdict\": \"steering\", \"reason\": \"x\"}",
		"[{\"verdict\": \"steering\", \"reason\": \"directs the session\"}]",
		"Thinking {aloud}. {\"verdict\": \"steering\"}",
	} {
		if v, err := ParseVerdict(out); err != nil || v.Verdict != Steering {
			t.Errorf("ParseVerdict(%q) = %+v, %v", out, v, err)
		}
	}
}

// A card the operator kept under another name still holds the words, so its
// repeats are home, not dropped as removed — and they cost no call.
func TestAKeptCardUnderAnotherNameIsNotRemoved(t *testing.T) {
	fk := "we-should-also-look-into-automating|0123456789abcdef"
	v := vault(t, map[string]string{"a.md": trace("s1", entry(playon, "m1", fk))})
	write(t, v, "personal/ideas/playon-automation.md", "---\ntitle: \"PlayOn automation\"\ntype: idea\n---\n\n"+playon+"\n")
	f := &fake{}
	plan, err := Decide(Options{Vault: v, MemoryRoot: "agent", Now: now, Call: f.call, Ledger: map[string]bool{fk: true}}, &State{})
	if err != nil {
		t.Fatal(err)
	}
	r := plan.Rows[0]
	if plan.Removed != 0 || f.calls != 0 || r.Verdict != Repeat || r.Slug != "playon-automation" || !r.Home {
		t.Errorf("removed %d, calls %d, row %+v; want a repeat at home on the renamed card", plan.Removed, f.calls, r)
	}
}

// An idea the operator dismissed is never carded again: a remark its card
// holds word for word is settled without a call, and the judge is shown the
// dismissed card, so a repeat in other words is named and nothing is written.
func TestADismissedIdeaIsNeverCardedAgain(t *testing.T) {
	words := "Build a tiny dashboard for the PlayOn queue some day."
	other := "One day, a little page that shows what PlayOn has queued."
	v := vault(t, map[string]string{"a.md": trace("s1", entry(words, "m1", ""), entry(other, "m2", ""))})
	write(t, v, "personal/ideas/playon-dashboard.md", "---\ntitle: \"PlayOn dashboard\"\ntype: idea\n"+
		"dismissed: 2026-10-01\n---\n\n"+words+"\n")
	f := &fake{replies: map[string]string{"queued": `{"verdict": "repeat", "slug": "playon-dashboard"}`}}
	plan := decide(t, v, f, &State{}, 0)
	if f.calls != 1 || !strings.Contains(f.prompts[0], "playon-dashboard") || !strings.Contains(f.prompts[0], "dismissed") {
		t.Fatalf("calls %d; want one call, showing the dismissed card", f.calls)
	}
	if len(plan.Intents) != 0 {
		t.Errorf("a dismissed idea was written: %s", plan.Intents[0].Rel)
	}
	for _, r := range plan.Rows {
		if !r.Dismissed || r.Rel != "personal/ideas/playon-dashboard.md" {
			t.Errorf("row %+v; want a repeat of the dismissed card", r)
		}
	}
}

// One new idea said in the same words in two sessions the same night is one
// inbox card, derived from both traces.
func TestOneNewIdeaInTwoSessionsTheSameNightIsOneCard(t *testing.T) {
	v := vault(t, map[string]string{
		"a.md": trace("s1", entry(playon, "m1", "")),
		"b.md": trace("s2", entry(playon, "m9", "")),
	})
	f := &fake{replies: map[string]string{"PlayOn": `{"verdict": "new", "title": "Automate the PlayOn recordings"}`}}
	st := &State{}
	plan := decide(t, v, f, st, 0)
	if len(plan.Intents) != 1 {
		t.Fatalf("%d intents; want one card", len(plan.Intents))
	}
	if card := string(plan.Intents[0].After); !strings.Contains(card, `derived_from: ["[[a]]", "[[b]]"]`) {
		t.Errorf("the card does not name both traces:\n%s", card)
	}
	if written := MarkApplied(&plan, st, apply(t, v, plan), now); len(written) != 1 {
		t.Errorf("written %v", written)
	}
	for _, r := range plan.Rows {
		if !r.Applied || r.Rel != "agent/inbox/automate-the-playon-recordings.md" {
			t.Errorf("row %+v", r)
		}
	}
}

// A card the retired lane filed holds the window around its trigger, not the
// whole sentence. Kept under another name, it still holds a run of the
// remark's words, so the remark goes to the judge, not dropped as removed.
func TestAKeptLaneCardHoldingOnlyItsWindowIsNotRemoved(t *testing.T) {
	remark := "Once the Netflix films are done and the Hulu queue is down to its last four episodes this week, " +
		"we should also look into automating the PlayOn recordings."
	body := "...Netflix films are done and the Hulu queue is down to its last four episodes this week, " +
		"we should also look into automating the PlayOn recordings."
	fk := "netflix-films-are-done-and-the|f8f6603e476a4185"
	v := vault(t, map[string]string{"a.md": trace("s1", entry(remark, "m1", fk))})
	write(t, v, "personal/ideas/playon-automation.md", "---\ntitle: \"PlayOn automation\"\ntype: idea\n---\n\n"+body+"\n")
	f := &fake{replies: map[string]string{"PlayOn": `{"verdict": "repeat", "slug": "playon-automation"}`}}
	plan, err := Decide(Options{Vault: v, MemoryRoot: "agent", Now: now, Call: f.call, Ledger: map[string]bool{fk: true}}, &State{})
	if err != nil {
		t.Fatal(err)
	}
	if plan.Removed != 0 || f.calls != 1 || plan.Rows[0].Slug != "playon-automation" {
		t.Errorf("removed %d, calls %d, row %+v; want it judged a repeat of the kept card", plan.Removed, f.calls, plan.Rows[0])
	}
}

// A deleted inbox card stays removed, though an unrelated idea card shares a
// stock phrase with its words (the third review of the fix).
func TestADeletedCardIsNotRevivedByAStockPhraseInAnotherCard(t *testing.T) {
	remark := "We should also look into the way we name the inbox cards, it is tedious by hand."
	v := vault(t, map[string]string{"a.md": trace("s1", entry(remark, "m1", ""))})
	newIdea := `{"verdict": "new", "title": "Name the inbox cards"}`
	st := &State{}
	night1 := decide(t, v, &fake{replies: map[string]string{"inbox cards": newIdea}}, st, 0)
	MarkApplied(&night1, st, apply(t, v, night1), now)
	if err := os.Remove(filepath.Join(v, "agent/inbox/name-the-inbox-cards.md")); err != nil {
		t.Fatal(err)
	}
	write(t, v, "personal/ideas/plex-backups.md", "---\ntitle: \"Plex backups\"\ntype: idea\n---\n\n"+
		"We should also look into the way we back up the Plex library.\n")
	write(t, v, "agent/memory/episodic/b.md", trace("s2", entry(remark, "m7", "")))
	f := &fake{replies: map[string]string{"inbox cards": newIdea}}
	night2 := decide(t, v, f, st, 0)
	if f.calls != 0 || len(night2.Intents) != 0 || night2.Removed != 1 {
		t.Errorf("calls %d, intents %d, removed %d; want the deleted idea dropped without a call",
			f.calls, len(night2.Intents), night2.Removed)
	}
}

// A reply whose own verdict is none of the three is an error, whatever
// objects it nests or strings it holds.
func TestANestedOptionIsNotTheVerdict(t *testing.T) {
	for _, out := range []string{
		`{"verdict": "unsure", "options": [{"verdict": "new", "title": "X"}, {"verdict": "steering"}]}`,
		`{"verdict": "steering", "reason": "it is not {"verdict": "new"} but directs the session"}`,
	} {
		if v, err := ParseVerdict(out); err == nil {
			t.Errorf("ParseVerdict(%q) = %+v; want an error, not a verdict", out, v)
		}
	}
	if v, err := ParseVerdict(`Hmm { let me think. {"verdict": "steering"}`); err != nil || v.Verdict != Steering {
		t.Errorf("a brace that never closes hid the verdict: %+v %v", v, err)
	}
}

// The judge's card, filed by inbox_review.file_one_idea, keeps its body; its
// name is no longer than a filed card's may be, so filing keeps it. The same
// idea said again in other case and punctuation is a repeat of the filed card,
// not removed (the fourth review of the fix).
func TestAFiledJudgeCardIsNotRemoved(t *testing.T) {
	remark := "Later we could reconcile the overnight PlayOn recordings against the streaming catalogue, automatically."
	v := vault(t, map[string]string{"a.md": trace("s1", entry(remark, "m1", ""))})
	newIdea := `{"verdict": "new", "title": "Automatically reconcile overnight PlayOn recordings against streaming subscription catalogues"}`
	st := &State{}
	night1 := decide(t, v, &fake{replies: map[string]string{"PlayOn": newIdea}}, st, 0)
	MarkApplied(&night1, st, apply(t, v, night1), now)
	ents, _ := os.ReadDir(filepath.Join(v, "agent/inbox"))
	if len(ents) != 1 || len(strings.TrimSuffix(ents[0].Name(), ".md")) > 72 {
		t.Fatalf("inbox %v; want one card named in at most 72 characters", ents)
	}
	body := read(t, v, "agent/inbox/"+ents[0].Name())
	// Filed under another name, the body kept, as file_one_idea files it.
	write(t, v, "personal/ideas/playon-reconcile.md", body)
	if err := os.Remove(filepath.Join(v, "agent/inbox", ents[0].Name())); err != nil {
		t.Fatal(err)
	}
	again := "later we could reconcile the overnight PlayOn recordings against the streaming catalogue automatically"
	write(t, v, "agent/memory/episodic/b.md", trace("s2", entry(again, "m7", "")))
	f := &fake{replies: map[string]string{"PlayOn": `{"verdict": "repeat", "slug": "playon-reconcile"}`}}
	night2 := decide(t, v, f, st, 0)
	if night2.Removed != 0 || f.calls != 1 {
		t.Errorf("removed %d, calls %d; the operator filed this idea, so it is judged, not removed", night2.Removed, f.calls)
	}
}

// A brace in the judge's prose, closed after its verdict, does not hide the
// verdict (the fourth review of the fix).
func TestAProseBraceAroundTheVerdictDoesNotHideIt(t *testing.T) {
	out := "The remark quotes `if cached {` from the loader. {\"verdict\": \"steering\", \"reason\": \"it directs the session\"} (the `}` closing that block is not in the remark)"
	if v, err := ParseVerdict(out); err != nil || v.Verdict != Steering {
		t.Errorf("ParseVerdict = %+v, %v; want steering", v, err)
	}
}

// The fifth review of the fix found the cases below.

// A deleted card's name is not given to another idea, so the deleted words
// stay removed.
func TestADeletedCardsNameIsNotReused(t *testing.T) {
	w1 := "We should also look into automating the PlayOn recordings, as that's tedious by hand."
	w2 := "Later we could have PlayOn record the Hulu queue overnight without me starting it."
	title := `{"verdict": "new", "title": "Automate the PlayOn recordings"}`
	v := vault(t, map[string]string{"a.md": trace("s1", entry(w1, "m1", ""))})
	st := &State{}
	n1 := decide(t, v, &fake{replies: map[string]string{"PlayOn": title}}, st, 0)
	MarkApplied(&n1, st, apply(t, v, n1), now)
	if err := os.Remove(filepath.Join(v, "agent/inbox/automate-the-playon-recordings.md")); err != nil {
		t.Fatal(err)
	}
	write(t, v, "agent/memory/episodic/b.md", trace("s2", entry(w2, "m2", "")))
	n2 := decide(t, v, &fake{replies: map[string]string{"Hulu": title}}, st, 0)
	MarkApplied(&n2, st, apply(t, v, n2), now)
	if n2.Rows[len(n2.Rows)-1].Rel == "agent/inbox/automate-the-playon-recordings.md" {
		t.Fatalf("the deleted card's name was given to another idea")
	}
	write(t, v, "agent/memory/episodic/c.md", trace("s3", entry(w1, "m3", "")))
	f := &fake{replies: map[string]string{"tedious": `{"verdict": "new", "title": "PlayOn automation"}`}}
	n3 := decide(t, v, f, st, 0)
	if n3.Removed != 1 || f.calls != 0 || len(n3.Intents) != 0 {
		t.Errorf("removed %d, calls %d, %d intents; want the deleted idea dropped without a call", n3.Removed, f.calls, len(n3.Intents))
	}
}

// Each remark is judged on its own conversation: the same words steering one
// session (restating its brief) can be another's own idea, so a verdict is
// never lent from one session to another (the sixth review of the fix).
func TestEachRemarkIsJudgedOnItsOwnConversation(t *testing.T) {
	v := vault(t, map[string]string{
		"a.md": trace("s1", entry(playon, "m1", "")),
		"b.md": trace("s2", entry(playon, "m9", "")),
	})
	n := 0
	call := func(string) (string, error) {
		n++
		if n == 1 {
			return `{"verdict": "steering", "reason": "it directs the session"}`, nil
		}
		return `{"verdict": "new", "title": "Automate the PlayOn recordings"}`, nil
	}
	plan, err := Decide(Options{Vault: v, MemoryRoot: "agent", Now: now, Call: call, Ledger: map[string]bool{}}, &State{})
	if err != nil {
		t.Fatal(err)
	}
	if n != 2 || len(plan.Intents) != 1 || plan.Count(Steering) != 1 || plan.Count(New) != 1 {
		t.Errorf("%d calls, %d intents, %d steering, %d new; want each judged on its own", n, len(plan.Intents),
			plan.Count(Steering), plan.Count(New))
	}
}

// A remark of too few words to know it by ("Idea:") is held by no card, so a
// second such remark carrying a different idea is judged.
func TestATooShortRemarkIsHeldByNoCard(t *testing.T) {
	short := func(message, context string) string {
		return fmt.Sprintf(`{"remark": "Idea:", "rule": "idea declaration", "first_request": false, `+
			`"transcript": "", "message": %q, "context": %q, "filed_key": ""}`, message, context)
	}
	v := vault(t, map[string]string{"a.md": trace("s1", short("m1", "operator: Idea: a dashboard for the PlayOn queue"))})
	st := &State{}
	n1 := decide(t, v, &fake{replies: map[string]string{"Idea": `{"verdict": "new", "title": "PlayOn queue dashboard"}`}}, st, 0)
	MarkApplied(&n1, st, apply(t, v, n1), now)
	write(t, v, "agent/memory/episodic/b.md", trace("s2", short("m2", "operator: Idea: rip the Blu-ray box sets to the NAS")))
	f := &fake{replies: map[string]string{"Idea": `{"verdict": "new", "title": "Rip the box sets"}`}}
	decide(t, v, f, st, 0)
	if f.calls != 1 {
		t.Errorf("calls %d; a different idea was settled without being judged", f.calls)
	}
}

// A remembered new verdict whose card landed on a run whose record was lost is
// settled at that card, not carded a second time, and remembered by it.
func TestARememberedNewWhoseCardLandedIsNotCardedTwice(t *testing.T) {
	v := vault(t, map[string]string{"a.md": trace("s1", entry(playon, "m1", ""))})
	st := &State{}
	n1 := decide(t, v, &fake{replies: map[string]string{"PlayOn": `{"verdict": "new", "title": "Automate the PlayOn recordings"}`}}, st, 0)
	apply(t, v, n1) // the card lands; MarkApplied never runs
	n2 := decide(t, v, &fake{}, st, 0)
	if len(n2.Intents) != 0 || !n2.Rows[0].Home {
		t.Fatalf("intents %d, row %+v; want the remark settled at the card that landed", len(n2.Intents), n2.Rows[0])
	}
	if st.Filed[fold(playon)] != "agent/inbox/automate-the-playon-recordings.md" {
		t.Errorf("the card is not remembered by its words: %v", st.Filed)
	}
}

// The seventh review of the fix found the cases below.

// Two ideas in one message, as the miner records "Idea: dark mode. Idea: a
// cache layer for the embeddings in the daemon.": a card made for one never
// holds the other, so the one the cap or a failed call left is judged the
// next night.
const twoIdeasShort = `{"remark": "Idea: dark mode.", "rule": "idea declaration", "first_request": false, "transcript": "", "message": "m1", "context": "assistant: Done, the test is fixed.\noperator: Idea: dark mode. Idea: a cache layer for the embeddings in the daemon.", "filed_key": ""}`
const twoIdeasSecond = `{"remark": "Idea: a cache layer for the embeddings in the daemon.", "rule": "idea declaration", "first_request": false, "transcript": "", "message": "m1", "context": "assistant: Done, the test is fixed.\noperator: Idea: dark mode. Idea: a cache layer for the embeddings in the daemon.", "filed_key": ""}`

func TestTheIdeaTheCapLeftIsJudgedNextNight(t *testing.T) {
	v := vault(t, map[string]string{"a.md": trace("s1", twoIdeasShort, twoIdeasSecond)})
	st := &State{}
	n1 := decide(t, v, &fake{replies: map[string]string{"dark mode": `{"verdict": "new", "title": "Dark mode"}`}}, st, 1)
	if n1.Deferred != 1 {
		t.Fatalf("night 1: %d deferred; want the cap to leave the second idea", n1.Deferred)
	}
	MarkApplied(&n1, st, apply(t, v, n1), now)
	f2 := &fake{replies: map[string]string{"cache layer": `{"verdict": "new", "title": "Cache the embeddings"}`}}
	n2 := decide(t, v, f2, st, 0)
	if f2.calls != 1 || len(n2.Intents) != 1 {
		t.Errorf("night 2: %d calls, %d intents; want the idea the cap left judged and carded", f2.calls, len(n2.Intents))
	}
}

func TestAnIdeaWhoseCallFailedIsJudgedNextNight(t *testing.T) {
	v := vault(t, map[string]string{"a.md": trace("s1", twoIdeasShort, twoIdeasSecond)})
	st := &State{}
	call := func(prompt string) (string, error) {
		if strings.Contains(prompt, "## The remark\n\nIdea: dark mode.") {
			return `{"verdict": "new", "title": "Dark mode"}`, nil
		}
		return "", errors.New("the judge timed out")
	}
	n1, err := Decide(Options{Vault: v, MemoryRoot: "agent", Now: now, Ledger: map[string]bool{}, Call: call}, st)
	if err != nil || len(n1.Errors) != 1 {
		t.Fatalf("night 1: err %v, errors %v", err, n1.Errors)
	}
	MarkApplied(&n1, st, apply(t, v, n1), now)
	f2 := &fake{replies: map[string]string{"cache layer": `{"verdict": "new", "title": "Cache the embeddings"}`}}
	decide(t, v, f2, st, 0)
	if f2.calls != 1 {
		t.Errorf("night 2: %d calls; want the idea whose call failed judged", f2.calls)
	}
}

// A short remark's card that landed on a run whose record was lost is found
// by its trace and its words, and not carded a second time.
func TestAShortRemarksLandedCardIsNotCardedTwice(t *testing.T) {
	v := vault(t, map[string]string{"a.md": trace("s1", twoIdeasShort)})
	st := &State{}
	n1 := decide(t, v, &fake{replies: map[string]string{"dark mode": `{"verdict": "new", "title": "Dark mode"}`}}, st, 0)
	apply(t, v, n1) // the card lands; MarkApplied never runs
	n2 := decide(t, v, &fake{}, st, 0)
	if len(n2.Intents) != 0 || !n2.Rows[0].Home {
		t.Errorf("night 2: %d intents, row %+v; want the remark settled at the card that landed", len(n2.Intents), n2.Rows[0])
	}
}

// The eighth review of the fix: a short remark's write is never settled at a
// card made for another remark of the same session.

func siblingEntry(remark, message, context string) string {
	return fmt.Sprintf(`{"remark": %q, "rule": "idea declaration", "first_request": false, "transcript": "", `+
		`"message": %q, "context": %q, "filed_key": ""}`, remark, message, context)
}

var (
	siblingRemarkB = siblingEntry("Idea: a CLI flag to skip the embedder in tests.", "m1",
		"operator: Idea: a CLI flag to skip the embedder in tests.")
	siblingRemarkA = siblingEntry("Idea: a CLI", "m2",
		"assistant: The queue is drained.\noperator: Idea: a CLI\nto pause and resume the PlayOn recording queue")
)

// A, judged new on a reporting night, is never carded once the writes are on:
// B's card (same trace, holds "Idea: a CLI") settles A's pending write.
func TestAShortNewFromAReportingNightIsCardedWhenTheWritesTurnOn(t *testing.T) {
	v := vault(t, map[string]string{"a.md": trace("s1", siblingRemarkB)})
	st := &State{}
	// Night 1, writes on: B is judged new, carded, and lands.
	n1 := decide(t, v, &fake{replies: map[string]string{"embedder": `{"verdict": "new", "title": "Skip the embedder in tests"}`}}, st, 0)
	MarkApplied(&n1, st, apply(t, v, n1), now)
	// The session goes on; its trace now holds A too.
	write(t, v, "agent/memory/episodic/a.md", trace("s1", siblingRemarkB, siblingRemarkA))
	// Night 2, a reporting night: A is judged new, nothing is written.
	n2 := decide(t, v, &fake{replies: map[string]string{"a CLI": `{"verdict": "new", "title": "A CLI for the PlayOn queue"}`}}, st, 0)
	if n2.Calls != 1 || n2.Count(New) != 2 {
		t.Fatalf("night 2: %d calls, %d new", n2.Calls, n2.Count(New))
	}
	// Night 3, writes on: A's remembered verdict should be written.
	n3 := decide(t, v, &fake{}, st, 0)
	if len(n3.Intents) != 1 {
		var rows []string
		for _, r := range n3.Rows {
			rows = append(rows, fmt.Sprintf("%q home=%v rel=%s", r.Remark, r.Home, r.Rel))
		}
		t.Errorf("night 3: %d intents; want A carded. rows: %s; Filed: %v", len(n3.Intents), strings.Join(rows, " | "), st.Filed)
	}
}

// The same, through a write that did not land: both are judged new on one
// night, B's card lands, A's creation is skipped. The next night A is settled
// at B's card and never carded.
func TestAShortNewWhoseWriteDidNotLandIsPlannedAgain(t *testing.T) {
	v := vault(t, map[string]string{"a.md": trace("s1", siblingRemarkB, siblingRemarkA)})
	st := &State{}
	n1 := decide(t, v, &fake{replies: map[string]string{
		"embedder": `{"verdict": "new", "title": "Skip the embedder in tests"}`,
		"a CLI":    `{"verdict": "new", "title": "A CLI for the PlayOn queue"}`,
	}}, st, 0)
	if len(n1.Intents) != 2 {
		t.Fatalf("night 1: %d intents", len(n1.Intents))
	}
	// Only B's creation lands (the run stopped, or A's write was skipped).
	landed := apply(t, v, Plan{Intents: n1.Intents[:1]})
	MarkApplied(&n1, st, landed, now)
	n2 := decide(t, v, &fake{}, st, 0)
	if len(n2.Intents) != 1 {
		t.Errorf("night 2: %d intents; want A's card retried", len(n2.Intents))
	}
}

// A cached repeat of an operator card, whose addition did not land, is settled
// at an inbox card it never targeted, and the addition is never made.
func TestAShortRepeatIsNotSettledAtASiblingsCard(t *testing.T) {
	v := vault(t, map[string]string{"a.md": trace("s1", siblingRemarkB, siblingRemarkA)})
	write(t, v, "personal/ideas/playon-queue-cli.md", "---\ntitle: \"PlayOn queue CLI\"\ntype: idea\narea: home-tech\n"+
		"status: active\nslug: playon-queue-cli\n---\n\nA command line to pause and resume the PlayOn recording queue.\n")
	st := &State{}
	n1 := decide(t, v, &fake{replies: map[string]string{
		"embedder": `{"verdict": "new", "title": "Skip the embedder in tests"}`,
		"a CLI":    `{"verdict": "repeat", "slug": "playon-queue-cli"}`,
	}}, st, 0)
	if len(n1.Intents) != 2 {
		t.Fatalf("night 1: %d intents", len(n1.Intents))
	}
	// The operator edits the PlayOn card during the run: the addition is
	// skipped as a conflict; B's creation lands.
	write(t, v, "personal/ideas/playon-queue-cli.md", "---\ntitle: \"PlayOn queue CLI\"\ntype: idea\narea: home-tech\n"+
		"status: active\nslug: playon-queue-cli\n---\n\nA command line to pause and resume the PlayOn recording queue.\n\nAlso: list it.\n")
	var first []string
	for _, in := range n1.Intents {
		if in.Before == nil {
			first = apply(t, v, Plan{Intents: n1.Intents[:1]})
		}
	}
	MarkApplied(&n1, st, first, now)
	n2 := decide(t, v, &fake{}, st, 0)
	if len(n2.Intents) != 1 || !strings.Contains(n2.Intents[0].Rel, "playon-queue-cli") {
		var rows []string
		for _, r := range n2.Rows {
			rows = append(rows, fmt.Sprintf("%q verdict=%s slug=%s home=%v rel=%s", r.Remark, r.Verdict, r.Slug, r.Home, r.Rel))
		}
		t.Errorf("night 2: %d intents; want the addition to playon-queue-cli retried. rows: %s", len(n2.Intents), strings.Join(rows, " | "))
	}
}
