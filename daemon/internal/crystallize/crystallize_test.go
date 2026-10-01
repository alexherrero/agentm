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
	if len(rep.Lessons) != 1 {
		t.Fatalf("lessons %+v, errors %v", rep.Lessons, rep.Errors)
	}
	if w := rep.Lessons[0]; len(w.Sources) != 2 || len(w.Stamped) != 2 {
		t.Errorf("the lesson names %v and stamped %v, want the 2 it rests on", w.Sources, w.Stamped)
	}
	raw, err := os.ReadFile(filepath.Join(f.vault, filepath.FromSlash(rep.Lessons[0].Rel)))
	if err != nil {
		t.Fatal(err)
	}
	if n := strings.Count(string(raw), "\n  - \"[["); n != 2 {
		t.Errorf("consolidated_from lists %d sources, want 2:\n%s", n, raw)
	}
	stamped := 0
	for _, p := range cards {
		if b, _ := os.ReadFile(p); strings.Contains(string(b), "consolidated_into:") {
			stamped++
		}
	}
	if stamped != 2 {
		t.Errorf("%d cards stamped, want the 2 the lesson rests on", stamped)
	}
}
