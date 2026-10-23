package ideajudge

import (
	"fmt"
	"os"
	"path/filepath"
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
	for k, v := range f.replies {
		if strings.Contains(remark, k) {
			return v, nil
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

// A verdict followed by a note that holds a brace still parses.
func TestAVerdictFollowedByANoteWithABraceParses(t *testing.T) {
	out := "{\"verdict\": \"steering\", \"reason\": \"directs the session\"}\n\n" +
		"(If it comes up again it could become an idea, e.g. {automate-playon}.)"
	if v, err := ParseVerdict(out); err != nil || v.Verdict != Steering {
		t.Errorf("%+v %v", v, err)
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
// question and one inbox card, derived from both traces.
func TestOneNewIdeaInTwoSessionsTheSameNightIsOneCard(t *testing.T) {
	v := vault(t, map[string]string{
		"a.md": trace("s1", entry(playon, "m1", "")),
		"b.md": trace("s2", entry(playon, "m9", "")),
	})
	f := &fake{replies: map[string]string{"PlayOn": `{"verdict": "new", "title": "Automate the PlayOn recordings"}`}}
	st := &State{}
	plan := decide(t, v, f, st, 0)
	if f.calls != 1 || len(plan.Intents) != 1 {
		t.Fatalf("%d calls, %d intents; want one of each", f.calls, len(plan.Intents))
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
