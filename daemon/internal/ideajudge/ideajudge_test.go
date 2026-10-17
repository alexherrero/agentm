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
	MarkApplied(&night, st, apply(t, v, night), now)
	third := decide(t, v, &fake{}, st, 0)
	if len(third.Intents) != 0 || !third.Rows[0].Applied {
		t.Errorf("an applied verdict was planned again: %+v", third.Rows)
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
