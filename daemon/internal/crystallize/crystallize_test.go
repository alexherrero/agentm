package crystallize

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

// The bar is arithmetic and the lesson is judgment. These hold the arithmetic:
// three sources, two sessions, seven days — and hold that nothing below the bar
// ever reaches a model, because a phase that asks anyway is a phase whose cost
// is the corpus rather than the recurrences in it.

type fixture struct {
	dir     string
	vault   string
	root    string
	prompts []string
	answer  func(prompt string) (string, error)
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	dir := t.TempDir()
	f := &fixture{dir: dir, vault: dir, root: filepath.Join(dir, "agent")}
	if err := os.MkdirAll(f.root, 0o755); err != nil {
		t.Fatal(err)
	}
	return f
}

func (f *fixture) call(prompt string) (string, error) {
	f.prompts = append(f.prompts, prompt)
	if f.answer != nil {
		return f.answer(prompt)
	}
	// Every source the cluster showed: the lesson is true of all of them. A
	// number past the cluster's size is ignored, so the one answer serves
	// every fixture.
	return `{"subject":"worktree-guard","title":"A worktree guard refuses what it cannot verify",
	          "lesson":"The guard reads the command, not the intent.","why":"Three tasks hit it.",
	          "sources":[1,2,3,4,5,6,7,8,9,10]}`, nil
}

func (f *fixture) run(t *testing.T, opt Options) Report {
	t.Helper()
	opt.Root, opt.Vault = f.root, f.vault
	if opt.Now.IsZero() {
		opt.Now = time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)
	}
	rep, err := Run(opt, f.call)
	if err != nil {
		t.Fatal(err)
	}
	return rep
}

// outcome writes a closed task's tracker with an Outcome that names `term`.
func (f *fixture) outcome(t *testing.T, project, task, closed, term string) string {
	t.Helper()
	p := filepath.Join(f.vault, "projects", project, "tasks", task, "tracker.md")
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	body := "---\nkind: tracker\ntitle: " + task + "\nstatus: done\nclosed: " + closed +
		"\n---\n\n## Objective\n\nthe work\n\n## Outcome\n\nThe run tripped over `" +
		term + "` again, and the fix was the same one.\n"
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

// card writes a memory card naming `term`.
func (f *fixture) card(t *testing.T, class, slug, created, session, term string) string {
	t.Helper()
	p := filepath.Join(f.root, "memory", class, slug+".md")
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	body := "---\ntitle: " + slug + "\nstatus: active\nlifecycle: active\n" +
		"created: " + created + "\nupdated: " + created + "\n" +
		"source_session: " + session + "\ntags: [" + term + "]\nproject: agentm\n" +
		"slug: " + slug + "\n---\n\nWhat happened with `" + term + "`.\n"
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestThreeRecurringOutcomesWriteOneLesson(t *testing.T) {
	f := newFixture(t)
	f.outcome(t, "agentm", "101-one", "2026-08-01", "git worktree")
	f.outcome(t, "agentm", "102-two", "2026-08-20", "git worktree")
	f.outcome(t, "agentm", "103-three", "2026-09-10", "git worktree")

	rep := f.run(t, Options{})

	if len(rep.Lessons) != 1 {
		t.Fatalf("wrote %d lesson(s), want one: %+v (skipped %+v, errors %v)",
			len(rep.Lessons), rep.Lessons, rep.Skipped, rep.Errors)
	}
	if len(f.prompts) != 1 {
		t.Errorf("made %d call(s), want one — the bar decides who gets asked", len(f.prompts))
	}
	l := rep.Lessons[0]
	if len(l.Sources) != 3 {
		t.Errorf("consolidated_from names %d source(s), want the three that taught it: %v",
			len(l.Sources), l.Sources)
	}
	// The record's path is slash-separated on every platform: it is what the
	// morning note renders as a link and what a later run joins against a
	// root. Asserted here rather than left to the one runner that has a
	// different separator, where it failed first.
	if strings.ContainsRune(l.Rel, '\\') {
		t.Errorf("the record's path is not slash-separated: %q", l.Rel)
	}
	raw, err := os.ReadFile(filepath.Join(f.vault, filepath.FromSlash(l.Rel)))
	if err != nil {
		t.Fatal(err)
	}
	text := string(raw)
	// A task's tracker links by path with the directory as the alias: every
	// project carries a file called `tracker.md`, so `[[tracker]]` would name
	// seventy of them and open whichever Obsidian ranked first.
	for _, want := range []string{"consolidated_from:",
		"[[projects/agentm/tasks/101-one/tracker|101-one]]",
		"[[projects/agentm/tasks/103-three/tracker|103-three]]",
		"lifecycle: pinned", "project: agentm", "\nkind: crystallized\n"} {
		if !strings.Contains(text, want) {
			t.Errorf("the lesson is missing %q:\n%s", want, text)
		}
	}
	// The contract's record kind and nothing beside it: a note carries `type`
	// or `kind`, never both, and `insight` is a retired type. A record never
	// carries the card's judgment fields, and `source:` holds only a transport,
	// so the phase names itself in `source_id:` and its reason goes in the body.
	for _, never := range []string{"\ntype:", "\nwhy:", "\nfiling_confidence:", "\ntrust:", "\nsource:"} {
		if strings.Contains(text, never) {
			t.Errorf("the lesson carries %q, which a crystallized record never does:\n%s", never[1:], text)
		}
	}
	for _, want := range []string{"\nsource_id: crystallize\n", "*Why it is a lesson:* Three tasks hit it."} {
		if !strings.Contains(text, want) {
			t.Errorf("the lesson is missing %q:\n%s", want, text)
		}
	}
	// A lesson never decays and nothing ages it out, so it must not be written
	// into a class the lifecycle job walks looking for something to sink. The
	// record names it from the vault root, the base its sources' links use.
	if !strings.HasPrefix(l.Rel, "agent/memory/crystallized/") {
		t.Errorf("the lesson landed at %s, want agent/memory/crystallized/", l.Rel)
	}
}

func TestTwoRecurringOutcomesWriteNothing(t *testing.T) {
	f := newFixture(t)
	f.outcome(t, "agentm", "101-one", "2026-08-01", "git worktree")
	f.outcome(t, "agentm", "102-two", "2026-09-10", "git worktree")

	rep := f.run(t, Options{})

	if len(rep.Lessons) != 0 {
		t.Fatalf("wrote %+v from two sources; the bar is %d", rep.Lessons, MinSources)
	}
	if len(f.prompts) != 0 {
		t.Errorf("made %d call(s) for a recurrence below the bar; nothing under the "+
			"bar should cost anything", len(f.prompts))
	}
	// And the near miss says which leg it missed, which is what the cadence
	// re-audit reads when the phase has written nothing for a month.
	var found string
	for _, m := range rep.NearMisses {
		if m.Subject == "git-worktree" {
			found = m.Reason
		}
	}
	if !strings.Contains(found, "2 source(s)") {
		t.Errorf("the near miss reads %q, want it to name the source count", found)
	}
}

func TestThreeInOneSittingWriteNothing(t *testing.T) {
	f := newFixture(t)
	// Three cards, three weeks apart, all captured in one session.
	f.card(t, "semantic", "a", "2026-08-01", "sess-1", "worktree-guard")
	f.card(t, "semantic", "b", "2026-08-20", "sess-1", "worktree-guard")
	f.card(t, "semantic", "c", "2026-09-10", "sess-1", "worktree-guard")

	rep := f.run(t, Options{})

	if len(rep.Lessons) != 0 || len(f.prompts) != 0 {
		t.Fatalf("three mentions in one sitting is one thought said three times; "+
			"wrote %+v after %d call(s)", rep.Lessons, len(f.prompts))
	}
	var found string
	for _, m := range rep.NearMisses {
		if m.Subject == "worktree-guard" {
			found = m.Reason
		}
	}
	if !strings.Contains(found, "session(s)") {
		t.Errorf("the near miss reads %q, want it to name the session count", found)
	}
}

func TestThreeInsideAWeekWriteNothing(t *testing.T) {
	f := newFixture(t)
	f.outcome(t, "agentm", "101-one", "2026-09-10", "git worktree")
	f.outcome(t, "agentm", "102-two", "2026-09-12", "git worktree")
	f.outcome(t, "agentm", "103-three", "2026-09-15", "git worktree")

	rep := f.run(t, Options{})

	if len(rep.Lessons) != 0 || len(f.prompts) != 0 {
		t.Fatalf("a lesson is what survived a week, not an afternoon's preoccupation; "+
			"wrote %+v after %d call(s)", rep.Lessons, len(f.prompts))
	}
	var found string
	for _, m := range rep.NearMisses {
		if m.Subject == "git-worktree" {
			found = m.Reason
		}
	}
	if !strings.Contains(found, "day(s)") {
		t.Errorf("the near miss reads %q, want it to name the span", found)
	}
}

func TestTheSourcesACardCanCarryAreStamped(t *testing.T) {
	f := newFixture(t)
	tracker := f.outcome(t, "agentm", "101-one", "2026-08-01", "git worktree")
	before, err := os.ReadFile(tracker)
	if err != nil {
		t.Fatal(err)
	}
	cardA := f.card(t, "semantic", "a", "2026-08-20", "sess-1", "git-worktree")
	cardB := f.card(t, "procedural", "b", "2026-09-10", "sess-2", "git-worktree")

	rep := f.run(t, Options{})

	if len(rep.Lessons) != 1 {
		t.Fatalf("wrote %d lesson(s), want one: %+v", len(rep.Lessons), rep.Lessons)
	}
	stem := strings.TrimSuffix(path.Base(rep.Lessons[0].Rel), ".md")
	for _, p := range []string{cardA, cardB} {
		raw, err := os.ReadFile(p)
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(string(raw), "consolidated_into: \"[["+stem+"]]\"") {
			t.Errorf("%s was not stamped with the lesson it taught:\n%s",
				filepath.Base(p), raw)
		}
	}
	// The tracker is untouched. A tracker is the operator's living head and its
	// frontmatter has a locked schema; stamping it would add a field nothing
	// reads and, worse, dampen the project's own head in every ranking because
	// a lesson quoted its Outcome.
	after, err := os.ReadFile(tracker)
	if err != nil {
		t.Fatal(err)
	}
	if string(after) != string(before) {
		t.Errorf("the tracker was rewritten:\n%s", after)
	}
	if len(rep.Lessons[0].Stamped) != 2 {
		t.Errorf("stamped %v, want the two cards", rep.Lessons[0].Stamped)
	}
}

func TestAStampedSourceIsNotReadAgain(t *testing.T) {
	f := newFixture(t)
	f.card(t, "semantic", "a", "2026-08-01", "sess-1", "git-worktree")
	f.card(t, "semantic", "b", "2026-08-20", "sess-2", "git-worktree")
	f.card(t, "semantic", "c", "2026-09-10", "sess-3", "git-worktree")

	first := f.run(t, Options{})
	if len(first.Lessons) != 1 {
		t.Fatalf("wrote %d lesson(s) on the first run, want one", len(first.Lessons))
	}
	calls := len(f.prompts)

	second := f.run(t, Options{})
	// Two mechanisms, each doing its own half: a stamped card is no longer read
	// as a source at all, and a subject that already has a lesson is not asked
	// for a second one. Asserted apart, or a break in either would hide behind
	// the other.
	if second.Sources >= first.Sources {
		t.Errorf("the second run read %d source(s) against the first run's %d; a "+
			"card that has taught its lesson is not raw material again",
			second.Sources, first.Sources)
	}
	if len(second.Lessons) != 0 {
		t.Errorf("the second run wrote %+v; one recurrence teaches one lesson",
			second.Lessons)
	}
	if len(f.prompts) != calls {
		t.Errorf("the second run made %d more call(s); a lesson already written "+
			"should cost nothing to not write again", len(f.prompts)-calls)
	}
}

// Outcomes are never stamped, so nothing drops them out of the source set: the
// same three closed tasks clear the bar again next week, and the week after
// that. Only the one-lesson-per-subject rule stops the phase paying for the
// same lesson every Sunday for as long as the project exists.
func TestASubjectAlreadyLearnedIsNotLearnedAgain(t *testing.T) {
	f := newFixture(t)
	f.outcome(t, "agentm", "101-one", "2026-08-01", "git worktree")
	f.outcome(t, "agentm", "102-two", "2026-08-20", "git worktree")
	f.outcome(t, "agentm", "103-three", "2026-09-10", "git worktree")

	if first := f.run(t, Options{}); len(first.Lessons) != 1 {
		t.Fatalf("wrote %d lesson(s) on the first run, want one", len(first.Lessons))
	}
	calls := len(f.prompts)

	second := f.run(t, Options{})
	if second.Clusters != 1 {
		t.Fatalf("the recurrence stopped clearing the bar (%d cluster(s)); this "+
			"test is about what stops the *lesson*, not the cluster", second.Clusters)
	}
	if len(second.Lessons) != 0 || len(f.prompts) != calls {
		t.Errorf("the second run wrote %+v after %d more call(s); one recurrence "+
			"teaches one lesson", second.Lessons, len(f.prompts)-calls)
	}
	if len(second.Skipped) != 1 || !strings.Contains(second.Skipped[0].Reason, "exists") {
		t.Errorf("skipped = %+v, want it to say a lesson already covers the subject",
			second.Skipped)
	}
}

func TestTheModelMayRefuseToCallItALesson(t *testing.T) {
	f := newFixture(t)
	f.answer = func(string) (string, error) {
		return `{"skip":"they share a tool name, not a lesson"}`, nil
	}
	f.outcome(t, "agentm", "101-one", "2026-08-01", "git worktree")
	f.outcome(t, "agentm", "102-two", "2026-08-20", "git worktree")
	f.outcome(t, "agentm", "103-three", "2026-09-10", "git worktree")

	rep := f.run(t, Options{})

	if len(rep.Lessons) != 0 {
		t.Fatalf("wrote %+v although the model declined", rep.Lessons)
	}
	if len(rep.Skipped) != 1 || !strings.Contains(rep.Skipped[0].Reason, "tool name") {
		t.Errorf("skipped = %+v, want the model's own reason carried through", rep.Skipped)
	}
	if _, err := os.Stat(filepath.Join(f.root, "memory", "crystallized")); !os.IsNotExist(err) {
		t.Error("a refusal created the class directory anyway")
	}
}

func TestADryRunFindsAndSpendsNothing(t *testing.T) {
	f := newFixture(t)
	f.outcome(t, "agentm", "101-one", "2026-08-01", "git worktree")
	f.outcome(t, "agentm", "102-two", "2026-08-20", "git worktree")
	f.outcome(t, "agentm", "103-three", "2026-09-10", "git worktree")

	rep := f.run(t, Options{DryRun: true})

	if rep.Clusters != 1 {
		t.Errorf("found %d recurrence(s) over the bar, want one", rep.Clusters)
	}
	if len(f.prompts) != 0 {
		t.Errorf("a dry run made %d call(s); the one thing a dry run must not do "+
			"is spend", len(f.prompts))
	}
	if len(rep.Lessons) != 0 {
		t.Errorf("a dry run wrote %+v", rep.Lessons)
	}
}

func TestATopicNarrowsTheRun(t *testing.T) {
	f := newFixture(t)
	f.outcome(t, "agentm", "101-one", "2026-08-01", "git worktree")
	f.outcome(t, "agentm", "102-two", "2026-08-20", "git worktree")
	f.outcome(t, "agentm", "103-three", "2026-09-10", "git worktree")
	f.card(t, "semantic", "x", "2026-08-01", "s1", "ledger-rows")
	f.card(t, "semantic", "y", "2026-08-20", "s2", "ledger-rows")
	f.card(t, "semantic", "z", "2026-09-10", "s3", "ledger-rows")

	rep := f.run(t, Options{Topic: "ledger"})

	if rep.Clusters != 1 || len(rep.Lessons) != 1 {
		t.Fatalf("a topic run found %d cluster(s) and wrote %d lesson(s), want one "+
			"of each", rep.Clusters, len(rep.Lessons))
	}
	if !strings.Contains(strings.Join(f.prompts, "\n"), "ledger-rows") {
		t.Error("the call was not about the topic asked for")
	}
}

func TestAnUnreadableAnswerIsAnErrorAndNotALesson(t *testing.T) {
	f := newFixture(t)
	f.answer = func(string) (string, error) { return "Sure! Here's what I found.", nil }
	f.outcome(t, "agentm", "101-one", "2026-08-01", "git worktree")
	f.outcome(t, "agentm", "102-two", "2026-08-20", "git worktree")
	f.outcome(t, "agentm", "103-three", "2026-09-10", "git worktree")

	rep := f.run(t, Options{})

	if len(rep.Lessons) != 0 || len(rep.Errors) != 1 {
		t.Fatalf("lessons %+v, errors %v — an answer that cannot be read is a call "+
			"that did something else, not a lesson with a missing field",
			rep.Lessons, rep.Errors)
	}
}

func TestAFencedAnswerIsStillRead(t *testing.T) {
	l, err := ParseLesson("```json\n{\"title\":\"t\",\"lesson\":\"l\",\"why\":\"w\"}\n```")
	if err != nil {
		t.Fatalf("a fenced object was refused: %v", err)
	}
	if l.Title != "t" {
		t.Errorf("read %+v", l)
	}
}

func TestStampPlacesTheKeyBeforeTheProjectFields(t *testing.T) {
	in := "---\ntitle: a\nrelated: [b]\nproject: agentm\nslug: a\n---\n\nbody\n"
	out := Stamp(in, "the-lesson")
	want := "---\ntitle: a\nrelated: [b]\nconsolidated_into: \"[[the-lesson]]\"\n" +
		"project: agentm\nslug: a\n---\n\nbody\n"
	if out != want {
		t.Errorf("stamped:\n%s\nwant:\n%s", out, want)
	}
	// Stamping twice replaces rather than repeats: a re-run must not leave two.
	if again := Stamp(out, "the-lesson"); again != want {
		t.Errorf("a second stamp changed the note:\n%s", again)
	}
}

func TestClustersPreferTheRecurrenceOverTheWordInsideIt(t *testing.T) {
	at := func(d int) time.Time {
		return time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC).AddDate(0, 0, d)
	}
	src := func(i int, terms ...string) Source {
		return Source{Rel: "a" + string(rune('0'+i)) + ".md", Kind: KindCard,
			Session: "s" + string(rune('0'+i)), At: at(i * 10), Terms: terms}
	}
	// Every source names both terms, so the two clusters hold the same set.
	// One lesson, not two.
	got := Clusters([]Source{
		src(1, "alpha", "beta"), src(2, "alpha", "beta"), src(3, "alpha", "beta"),
	})
	if len(got) != 1 {
		t.Fatalf("kept %d cluster(s) over one source set: %+v", len(got), got)
	}
	if got[0].Subject != "alpha" {
		t.Errorf("kept %q; equal sets break the tie by subject so a night's output "+
			"does not depend on map order", got[0].Subject)
	}
}

func TestTheReportIsTheRecordTheRunnerReads(t *testing.T) {
	f := newFixture(t)
	f.outcome(t, "agentm", "101-one", "2026-08-01", "git worktree")
	f.outcome(t, "agentm", "102-two", "2026-08-20", "git worktree")
	f.outcome(t, "agentm", "103-three", "2026-09-10", "git worktree")

	rep := f.run(t, Options{})
	blob, err := json.Marshal(rep)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{`"lessons"`, `"consolidated_from"`, `"sources"`} {
		if !strings.Contains(string(blob), want) {
			t.Errorf("the report does not carry %s:\n%s", want, blob)
		}
	}
}

// Task 177: a closed task's directory moves to `completed/tasks/` two weeks
// after it closes, and its Outcome still teaches.
func TestAMovedTasksOutcomeIsStillASource(t *testing.T) {
	f := newFixture(t)
	f.outcome(t, "agentm", "101-one", "2026-08-01", "git worktree")
	f.outcome(t, "agentm", "102-two", "2026-08-20", "git worktree")
	moved := f.outcome(t, "agentm", "103-three", "2026-09-10", "git worktree")
	to := filepath.Join(f.vault, "projects", "agentm", "completed", "tasks", "103-three")
	if err := os.MkdirAll(filepath.Dir(to), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(filepath.Dir(moved), to); err != nil {
		t.Fatal(err)
	}

	rep := f.run(t, Options{})

	if len(rep.Lessons) != 1 || len(rep.Lessons[0].Sources) != 3 {
		t.Fatalf("wrote %+v, want one lesson from all three Outcomes", rep.Lessons)
	}
	if !strings.Contains(strings.Join(rep.Lessons[0].Sources, " "),
		"projects/agentm/completed/tasks/103-three/tracker|103-three") {
		t.Errorf("the moved Outcome is not linked where it sits: %v", rep.Lessons[0].Sources)
	}
}

// The folder's own `_index.md` is not a lesson. Read as one, it blocked every
// subject its name or tags spell.
func TestTheFoldersIndexIsNotALesson(t *testing.T) {
	f := newFixture(t)
	idx := filepath.Join(f.root, Dir, "_index.md")
	if err := os.MkdirAll(filepath.Dir(idx), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(idx, []byte("---\nkind: dir-index\ntags: [git-worktree]\n---\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	f.outcome(t, "agentm", "101-one", "2026-08-01", "git worktree")
	f.outcome(t, "agentm", "102-two", "2026-08-20", "git worktree")
	f.outcome(t, "agentm", "103-three", "2026-09-10", "git worktree")

	rep := f.run(t, Options{})

	if len(rep.Lessons) != 1 {
		t.Fatalf("wrote %d lesson(s), skipped %+v; the folder's index blocked the subject",
			len(rep.Lessons), rep.Skipped)
	}
}

// The shape is the vault's gates' to decide, so a written lesson is run
// through them: the frontmatter gate and the card-shape gate, which a lesson
// in the card's fields failed on its first live run (task 177, step 9).
func TestAWrittenLessonPassesTheVaultsGates(t *testing.T) {
	py, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 is not on PATH")
	}
	// The frontmatter gate parses YAML and says it cannot run without PyYAML;
	// a daemon job with a bare python3 has no gate to run, and the Python
	// suite runs this one where it can.
	if err := exec.Command(py, "-c", "import yaml").Run(); err != nil {
		t.Skip("PyYAML is not installed, so the vault gates cannot run here")
	}
	_, here, _, _ := runtime.Caller(0)
	scripts := filepath.Join(filepath.Dir(here), "..", "..", "..", "scripts")
	f := newFixture(t)
	if err := os.MkdirAll(filepath.Join(f.vault, ".obsidian"), 0o755); err != nil {
		t.Fatal(err)
	}
	// Three cards, one with a machine field and no `project`, `task` or `slug`:
	// the stamp it gains must still land in the card's read block.
	for i, d := range []string{"2026-08-01", "2026-08-20", "2026-09-10"} {
		p := filepath.Join(f.root, "memory", "semantic", fmt.Sprintf("c%d.md", i))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		body := "---\ntitle: c\ntype: fix\nstatus: active\nlifecycle: active\nfiling_confidence: high\nsource: conversation\n" +
			"trust: trusted\ncreated: " + d + "\nupdated: " + d + "\ntags: [git-worktree]\n" +
			"confidence: 0.9\n---\n\nWhat happened with `git worktree`, session " + fmt.Sprint(i) + ".\n"
		if i == 0 {
			body = strings.Replace(body, "type: fix\n", "type: fix\nsummary: s\n", 1)
		}
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	// The card-shape gate enforces once the backfill has run, as it has live.
	if err := os.WriteFile(filepath.Join(f.root, "memory", ".card-backfill-complete"), nil, 0o644); err != nil {
		t.Fatal(err)
	}
	rep := f.run(t, Options{})
	if len(rep.Lessons) != 1 || len(rep.Lessons[0].Stamped) == 0 {
		t.Fatalf("wrote %+v; want one lesson that stamps its cards", rep.Lessons)
	}
	for _, gate := range [][]string{
		{filepath.Join(scripts, "check-vault-frontmatter.py"), "--vault", f.vault},
		{filepath.Join(scripts, "check-card-shape.py"), "--memory-root", f.root},
	} {
		cmd := exec.Command(py, gate...)
		cmd.Env = append(os.Environ(), "AGENTM_STORAGE_RULES=")
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Errorf("%s refuses the lesson: %v\n%s", filepath.Base(gate[0]), err, out)
		}
	}
}

// #749: a cluster gathers notes that share a word, and the lesson written from
// it is often true of only some of them. Only the sources the lesson names are
// recorded as what taught it and stamped consolidated_into; a source it does
// not name keeps its rank.
func TestCoveredKeepsOnlyTheSourcesTheLessonNames(t *testing.T) {
	c := Cluster{Subject: "inbox", Sources: []Source{{Rel: "a.md"}, {Rel: "b.md"}, {Rel: "c.md"}}}
	got, err := Covered(Lesson{Sources: []int{3, 1, 3, 0, 9}}, c)
	if err != nil {
		t.Fatal(err)
	}
	if len(got.Sources) != 2 || got.Sources[0].Rel != "c.md" || got.Sources[1].Rel != "a.md" {
		t.Errorf("covered %+v, want c.md then a.md once each", got.Sources)
	}
	if _, err := Covered(Lesson{}, c); err == nil {
		t.Error("a lesson that named no source was accepted")
	}
	if _, err := Covered(Lesson{Sources: []int{4, 0}}, c); err == nil {
		t.Error("a lesson that named only sources it was never shown was accepted")
	}
	arc := Cluster{Subject: "arc", Arc: "vault-perfection", Sources: c.Sources}
	if got, err := Covered(Lesson{}, arc); err != nil || len(got.Sources) != 3 {
		t.Errorf("an arc's synthesis rests on all its sources: %+v, %v", got.Sources, err)
	}
}

func TestOnlyTheCoveredCardsAreStampedAndNamed(t *testing.T) {
	f := newFixture(t)
	// Three of the four: a lesson must still clear the bar on the sources it
	// keeps (task 190), so it names three and leaves the fourth.
	f.answer = func(prompt string) (string, error) {
		return `{"subject":"worktree-guard","title":"A worktree guard refuses what it cannot verify",
		          "lesson":"The guard reads the command, not the intent.","why":"Three tasks hit it.",
		          "sources":[1,2,4]}`, nil
	}
	cards := []string{
		f.card(t, "semantic", "a", "2026-08-01", "sess-1", "worktree-guard"),
		f.card(t, "semantic", "b", "2026-08-20", "sess-2", "worktree-guard"),
		f.card(t, "semantic", "c", "2026-09-05", "sess-3", "worktree-guard"),
		f.card(t, "semantic", "d", "2026-09-10", "sess-4", "worktree-guard"),
	}
	rep := f.run(t, Options{})
	if len(rep.Lessons) != 1 {
		t.Fatalf("lessons %+v, errors %v", rep.Lessons, rep.Errors)
	}
	if w := rep.Lessons[0]; len(w.Sources) != 3 || len(w.Stamped) != 3 {
		t.Errorf("the lesson names %v and stamped %v, want the 3 it rests on", w.Sources, w.Stamped)
	}
	raw, err := os.ReadFile(filepath.Join(f.vault, filepath.FromSlash(rep.Lessons[0].Rel)))
	if err != nil {
		t.Fatal(err)
	}
	if n := strings.Count(string(raw), "\n  - \"[["); n != 3 {
		t.Errorf("consolidated_from lists %d sources, want 3:\n%s", n, raw)
	}
	stamped := 0
	for _, p := range cards {
		if b, _ := os.ReadFile(p); strings.Contains(string(b), "consolidated_into:") {
			stamped++
		}
	}
	if b, _ := os.ReadFile(cards[2]); stamped != 3 || strings.Contains(string(b), "consolidated_into:") {
		t.Errorf("%d cards stamped, want the 3 the lesson rests on and never c", stamped)
	}
}

// --- task 190 step 6: the bar on the lesson, and a lesson minted once --------

// trace writes a session trace whose `## Candidates` carry `lines`, each
// naming `term`.
func (f *fixture) trace(t *testing.T, name, created, session, term string, lines int) string {
	t.Helper()
	p := filepath.Join(f.root, "memory", "episodic", name+".md")
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	var b strings.Builder
	b.WriteString("---\ntitle: " + name + "\nkind: session-trace\ncreated: " + created +
		"\nsession: " + session + "\n---\n\n## Candidates\n\n")
	for i := 0; i < lines; i++ {
		fmt.Fprintf(&b, "- rule-%d — \"the merge stranded the worktree on `%s`, line %d\"\n", i, term, i)
	}
	if err := os.WriteFile(p, []byte(b.String()), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func lessonFiles(t *testing.T, f *fixture) []string {
	t.Helper()
	entries, _ := os.ReadDir(filepath.Join(f.root, filepath.FromSlash(Dir)))
	var out []string
	for _, e := range entries {
		out = append(out, e.Name())
	}
	return out
}

// The audit's gh-pr-merge-delete-branch-strands-worktree shape: the cluster
// cleared the bar, and the lesson the model wrote rests on fewer sources than
// the bar asks. Checked on the sources the lesson kept, it is a near miss.
func TestADraftNarrowedUnderTheBarIsNotWritten(t *testing.T) {
	f := newFixture(t)
	f.answer = func(prompt string) (string, error) {
		return `{"subject":"worktree-guard","title":"A worktree guard refuses what it cannot verify",
		          "lesson":"The guard reads the command, not the intent.","why":"Two tasks hit it.",
		          "sources":[1,2]}`, nil
	}
	cards := []string{
		f.card(t, "semantic", "a", "2026-08-01", "sess-1", "worktree-guard"),
		f.card(t, "semantic", "b", "2026-08-20", "sess-2", "worktree-guard"),
		f.card(t, "semantic", "c", "2026-09-10", "sess-3", "worktree-guard"),
	}
	rep := f.run(t, Options{})
	if len(rep.Lessons) != 0 || len(lessonFiles(t, f)) != 0 {
		t.Fatalf("a lesson resting on 2 of 3 sources was written: %+v", rep.Lessons)
	}
	found := false
	for _, n := range rep.NearMisses {
		if n.Subject == "worktree-guard" && n.Sources == 2 && strings.Contains(n.Reason, "the lesson rests on") {
			found = true
		}
	}
	if !found {
		t.Errorf("the narrowed draft is not reported as a near miss: %+v", rep.NearMisses)
	}
	for _, p := range cards {
		if b, _ := os.ReadFile(p); strings.Contains(string(b), "consolidated_into") {
			t.Errorf("%s was stamped by a lesson that was not written", p)
		}
	}
}

// Two candidate lines of one trace are one note. The audit's gh-pr-merge
// lesson rested on four lines from two traces a day apart.
func TestCandidateLinesFromOneTraceAreOneNote(t *testing.T) {
	f := newFixture(t)
	f.trace(t, "2026-08-01-session-a", "2026-08-01", "sess-a", "gh-pr-merge", 2)
	f.trace(t, "2026-08-20-session-b", "2026-08-20", "sess-b", "gh-pr-merge", 2)
	rep := f.run(t, Options{})
	if len(f.prompts) != 0 || rep.Clusters != 0 {
		t.Fatalf("four lines in two traces reached a model (%d call(s), %d cluster(s))", len(f.prompts), rep.Clusters)
	}
	found := false
	for _, n := range rep.NearMisses {
		if n.Subject == "gh-pr-merge" && strings.Contains(n.Reason, "2 note(s)") {
			found = true
		}
	}
	if !found {
		t.Errorf("the near miss does not say the lines were two notes: %+v", rep.NearMisses)
	}
}

// A tracker cannot take `consolidated_into`, so the closed tasks that taught a
// lesson cluster again every week. The ledger remembers them: a lesson the
// operator deletes is not minted again from the same sources.
func TestALedgeredTrackerClusterIsNotReproposedNextWeek(t *testing.T) {
	f := newFixture(t)
	f.outcome(t, "agentm", "101-one", "2026-08-01", "git worktree")
	f.outcome(t, "agentm", "102-two", "2026-08-20", "git worktree")
	f.outcome(t, "agentm", "103-three", "2026-09-10", "git worktree")
	state := t.TempDir()
	first := f.run(t, Options{StateDir: state})
	if len(first.Lessons) != 1 {
		t.Fatalf("lessons %+v, errors %v", first.Lessons, first.Errors)
	}
	// The operator reads it on the morning note and deletes it.
	if err := os.Remove(filepath.Join(f.vault, filepath.FromSlash(first.Lessons[0].Rel))); err != nil {
		t.Fatal(err)
	}
	calls := len(f.prompts)
	next := f.run(t, Options{StateDir: state, Now: time.Date(2026, 9, 25, 9, 0, 0, 0, time.UTC)})
	if len(next.Lessons) != 0 || len(f.prompts) != calls {
		t.Fatalf("the same three trackers were proposed again: %d call(s), lessons %+v", len(f.prompts)-calls, next.Lessons)
	}
	if len(next.Skipped) != 1 || !strings.Contains(next.Skipped[0].Reason, "taught a lesson already") {
		t.Errorf("skipped %+v, want the cluster named as consumed", next.Skipped)
	}
}

// A lesson written before the ledger existed names its trackers in
// `consolidated_from`, and they count as consumed too.
func TestALessonsOwnSourcesCountAsConsumed(t *testing.T) {
	f := newFixture(t)
	f.outcome(t, "agentm", "101-one", "2026-08-01", "lock file")
	f.outcome(t, "agentm", "102-two", "2026-08-20", "lock file")
	f.outcome(t, "agentm", "103-three", "2026-09-10", "lock file")
	lesson := "---\ntitle: Older lesson\nkind: crystallized\ntags: [stale-lock]\nconsolidated_from:\n" +
		"  - \"[[projects/agentm/tasks/101-one/tracker|101-one]]\"\n" +
		"  - \"[[projects/agentm/tasks/102-two/tracker|102-two]]\"\n" +
		"  - \"[[projects/agentm/tasks/103-three/tracker|103-three]]\"\n---\n\nAn older lesson.\n"
	p := filepath.Join(f.root, filepath.FromSlash(Dir), "agentm-stale-lock.md")
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(lesson), 0o644); err != nil {
		t.Fatal(err)
	}
	rep := f.run(t, Options{StateDir: t.TempDir()})
	if len(f.prompts) != 0 || len(rep.Lessons) != 0 {
		t.Errorf("trackers an existing lesson rests on were proposed again: %d call(s), %+v", len(f.prompts), rep.Lessons)
	}
}

// A draft whose sources are mostly an existing lesson's links that lesson
// rather than minting a second one: the new card names it in `related`.
func TestAnOverlappingDraftLinksTheLessonRatherThanMintingOne(t *testing.T) {
	f := newFixture(t)
	f.outcome(t, "agentm", "101-one", "2026-08-01", "git worktree")
	f.outcome(t, "agentm", "102-two", "2026-08-20", "git worktree")
	f.outcome(t, "agentm", "103-three", "2026-09-10", "git worktree")
	state := t.TempDir()
	first := f.run(t, Options{StateDir: state})
	if len(first.Lessons) != 1 {
		t.Fatalf("lessons %+v", first.Lessons)
	}
	stem := strings.TrimSuffix(path.Base(first.Lessons[0].Rel), ".md")
	// The next week a fresh card names it again, under a word the three
	// trackers share with it, so the cluster is new and mostly not.
	for _, task := range []string{"101-one", "102-two", "103-three"} {
		p := filepath.Join(f.vault, "projects", "agentm", "tasks", task, "tracker.md")
		b, _ := os.ReadFile(p)
		if err := os.WriteFile(p, []byte(strings.Replace(string(b), "again,", "again under `stale lock`,", 1)), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	card := f.card(t, "semantic", "d", "2026-09-20", "sess-4", "stale lock")
	before := lessonFiles(t, f)
	next := f.run(t, Options{StateDir: state, Now: time.Date(2026, 9, 25, 9, 0, 0, 0, time.UTC)})
	if len(next.Lessons) != 0 || len(lessonFiles(t, f)) != len(before) {
		t.Fatalf("a draft resting mostly on %s's sources minted a lesson: %+v", stem, next.Lessons)
	}
	if len(next.Linked) != 1 || next.Linked[0].Lesson != stem {
		t.Fatalf("linked %+v, want the draft linked to %s", next.Linked, stem)
	}
	b, _ := os.ReadFile(card)
	if !strings.Contains(string(b), "[["+stem+"]]") || strings.Contains(string(b), "consolidated_into") {
		t.Errorf("the new card does not name %s in related, or was stamped:\n%s", stem, b)
	}
}

// The cap bounds the lessons one run writes, by default and when set.
func TestTheCapBoundsTheLessonsOneRunWrites(t *testing.T) {
	for _, tc := range []struct {
		cap, want int
	}{{0, DefaultCap}, {2, 2}} {
		f := newFixture(t)
		n := 0
		f.answer = func(prompt string) (string, error) {
			n++
			return fmt.Sprintf(`{"subject":"lesson-%d","title":"Lesson %d","lesson":"It recurred.","why":"Three tasks.","sources":[1,2,3]}`, n, n), nil
		}
		for i := 0; i < DefaultCap+1; i++ {
			term := fmt.Sprintf("subject%c", 'a'+i)
			for j, closed := range []string{"2026-08-01", "2026-08-20", "2026-09-10"} {
				f.outcome(t, "agentm", fmt.Sprintf("%d%d-%s", i, j, term), closed, term)
			}
		}
		rep := f.run(t, Options{Cap: tc.cap})
		if len(rep.Lessons) != tc.want || len(lessonFiles(t, f)) != tc.want {
			t.Errorf("cap %d: wrote %d lesson(s) (%d file(s)), want %d", tc.cap, len(rep.Lessons), len(lessonFiles(t, f)), tc.want)
		}
	}
}

// Two drafts the model names alike never share a file: the second is an
// error, and the first lesson stays as written.
func TestASecondDraftUnderAnExistingNameDoesNotOverwriteIt(t *testing.T) {
	f := newFixture(t)
	for i, term := range []string{"alpha", "bravo"} {
		for j, closed := range []string{"2026-08-01", "2026-08-20", "2026-09-10"} {
			f.outcome(t, "agentm", fmt.Sprintf("%d%d-%s", i, j, term), closed, term)
		}
	}
	rep := f.run(t, Options{})
	if len(rep.Lessons) != 1 || len(lessonFiles(t, f)) != 1 {
		t.Fatalf("lessons %+v, files %v", rep.Lessons, lessonFiles(t, f))
	}
	if len(rep.Errors) != 1 || !strings.Contains(rep.Errors[0], "already") {
		t.Errorf("errors %v, want the second draft refused for its name", rep.Errors)
	}
}

// Found by the adversarial review of task 190 steps 3-7.
// 1. The ledger keys a tracker by its full path (Source.Link() for a repeated
// name), so the key changes when the task mover takes the task directory to
// `completed/tasks/` two weeks after it closes — and crystallize reads trackers
// from both homes (taskHomes). A lesson the operator deleted is remembered only
// in the ledger, under the old `tasks/` key; after the move the cluster no
// longer reads as consumed and the deleted lesson is minted again: the exact
// case the ledger exists to stop.
func TestALedgeredTrackerClusterStaysConsumedAfterTheTaskMoves(t *testing.T) {
	f := newFixture(t)
	tasks := []string{"101-one", "102-two", "103-three"}
	f.outcome(t, "agentm", tasks[0], "2026-08-01", "git worktree")
	f.outcome(t, "agentm", tasks[1], "2026-08-20", "git worktree")
	f.outcome(t, "agentm", tasks[2], "2026-09-10", "git worktree")
	state := t.TempDir()
	first := f.run(t, Options{StateDir: state})
	if len(first.Lessons) != 1 {
		t.Fatalf("lessons %+v, errors %v", first.Lessons, first.Errors)
	}
	// The operator deletes the lesson as wrong.
	if err := os.Remove(filepath.Join(f.vault, filepath.FromSlash(first.Lessons[0].Rel))); err != nil {
		t.Fatal(err)
	}
	// Two weeks after close, the night's task mover moves each directory whole.
	for _, task := range tasks {
		from := filepath.Join(f.vault, "projects", "agentm", "tasks", task)
		to := filepath.Join(f.vault, "projects", "agentm", "completed", "tasks", task)
		if err := os.MkdirAll(filepath.Dir(to), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.Rename(from, to); err != nil {
			t.Fatal(err)
		}
	}
	calls := len(f.prompts)
	next := f.run(t, Options{StateDir: state, Now: time.Date(2026, 10, 2, 9, 0, 0, 0, time.UTC)})
	if len(next.Lessons) != 0 || len(f.prompts) != calls {
		ledger, _ := os.ReadFile(filepath.Join(state, ConsumedName))
		t.Errorf("the lesson the operator deleted was proposed again once its trackers moved to "+
			"completed/tasks/: %d call(s), lessons %+v\nledger:\n%s", len(f.prompts)-calls, next.Lessons, ledger)
	}
}

// 2. The ledger keys a candidate line by its trace (Link() is the trace's
// stem), so once one line of a trace teaches a lesson every other line of that
// trace reads as consumed. sources.go says the opposite on purpose: "a trace
// is one session's record and several of its lines may teach different
// lessons". Three sessions that each noted two different things yield one
// lesson; the second, unrelated cluster is skipped as "every source has
// taught a lesson already" in the same run.
func TestTwoLessonsFromDifferentLinesOfTheSameTracesAreBothWritten(t *testing.T) {
	f := newFixture(t)
	for i, day := range []string{"2026-08-01", "2026-08-20", "2026-09-10"} {
		p := filepath.Join(f.root, "memory", "episodic", fmt.Sprintf("%s-session-%d.md", day, i))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		body := "---\ntitle: session " + day + "\nkind: session-trace\ncreated: " + day +
			"\nsession: sess-" + day + "\n---\n\n## Candidates\n\n" +
			"- merge-rule — \"the merge stranded the worktree on `alpha-merge`\"\n" +
			"- lock-rule — \"a stale `bravo-lock` file blocked the build\"\n"
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	f.answer = func(prompt string) (string, error) {
		subject := "alpha-merge"
		if strings.Contains(prompt, "bravo-lock") && !strings.Contains(prompt, "alpha-merge") {
			subject = "bravo-lock"
		}
		return fmt.Sprintf(`{"subject":%q,"title":"Lesson about %s","lesson":"It recurred.",`+
			`"why":"Three sessions.","sources":[1,2,3]}`, subject, subject), nil
	}
	rep := f.run(t, Options{StateDir: t.TempDir()})
	if rep.Clusters != 2 {
		t.Fatalf("fixture: %d cluster(s), want alpha-merge and bravo-lock (near misses %+v)", rep.Clusters, rep.NearMisses)
	}
	if len(rep.Lessons) != 2 {
		t.Errorf("two distinct recurrences in three sessions wrote %d lesson(s); skipped %+v, errors %v",
			len(rep.Lessons), rep.Skipped, rep.Errors)
	}
}

// 3. An arc's synthesis is owed to a specific name (`<project>-<arc>`), and
// its sources are every closed-task Outcome of the project. When those tasks
// already taught an ordinary recurrence lesson, `used.all` skips the arc
// cluster outright, so the synthesis the closing session marked is never
// written.
func TestAClosedArcIsSynthesisedEvenIfItsTasksTaughtALesson(t *testing.T) {
	f := newFixture(t)
	f.outcome(t, "agentm", "101-one", "2026-08-01", "git worktree")
	f.outcome(t, "agentm", "102-two", "2026-08-20", "git worktree")
	f.outcome(t, "agentm", "103-three", "2026-09-10", "git worktree")
	state := t.TempDir()
	first := f.run(t, Options{StateDir: state})
	if len(first.Lessons) != 1 {
		t.Fatalf("lessons %+v, errors %v", first.Lessons, first.Errors)
	}
	// The closing session marks the arc on the project's tracker.
	tracker := filepath.Join(f.vault, "projects", "agentm", "tracker.md")
	if err := os.WriteFile(tracker, []byte("---\nkind: tracker\ntitle: agentm\nstatus: active\n"+
		"arc_closed: first-arc\n---\n\n## Objective\n\nthe project\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	next := f.run(t, Options{StateDir: state, Now: time.Date(2026, 9, 25, 9, 0, 0, 0, time.UTC)})
	if _, err := os.Stat(filepath.Join(f.root, filepath.FromSlash(Dir), "agentm-first-arc.md")); err != nil {
		t.Errorf("the closed arc's synthesis was not written: lessons %+v, skipped %+v, linked %+v, errors %v",
			next.Lessons, next.Skipped, next.Linked, next.Errors)
	}
}
