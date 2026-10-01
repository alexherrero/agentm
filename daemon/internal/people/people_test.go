package people

import (
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

const tableFile = "---\ntitle: People\nkind: standard\n---\n\n# People\n\n```people\n" +
	"you: [Pat Owner, Pat]\n" +
	"aliases:\n  Jane Doe: [Jane, J. Doe]\n  Ravi Shah: [Ravi]\n" +
	"deny: [Claude, Mount Rainier]\n```\n"

func table(t *testing.T) Table {
	t.Helper()
	tb, err := Parse(tableFile)
	if err != nil {
		t.Fatal(err)
	}
	return tb
}

func TestAMissingTableIsEmptyAndABrokenOneIsAnError(t *testing.T) {
	if tb, err := Load(t.TempDir()); err != nil || len(tb.You)+len(tb.Aliases)+len(tb.Deny) != 0 {
		t.Errorf("a missing table read as %+v, %v", tb, err)
	}
	vault := t.TempDir()
	path := filepath.Join(vault, filepath.FromSlash(RelPath))
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("```people\nyou: [unclosed\n```\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(vault); err == nil {
		t.Error("a table that is not YAML read as a table")
	}
	if err := os.WriteFile(path, []byte(tableFile), 0o644); err != nil {
		t.Fatal(err)
	}
	if tb, err := Load(vault); err != nil || !reflect.DeepEqual(tb.Aliases["Jane Doe"], []string{"Jane", "J. Doe"}) {
		t.Errorf("Load = %+v, %v", tb, err)
	}
}

func TestTheTableFilesASpellingUnderItsPerson(t *testing.T) {
	tb := table(t)
	for in, want := range map[string]string{
		"jane": "Jane Doe", "J. Doe": "Jane Doe", "Jane Doe": "Jane Doe",
		"Ravi": "Ravi Shah", "  Mara   Lin ": "Mara Lin",
		"Pat": "", "pat owner": "", "Claude": "", "mount rainier": "", "": "", "12": "",
	} {
		got, ok := tb.Canonical(in)
		if got != want || ok != (want != "") {
			t.Errorf("Canonical(%q) = %q, %v; want %q", in, got, ok, want)
		}
	}
}

func TestANameIsMatchedAsAWholeWordAndLongestFirst(t *testing.T) {
	tb := table(t)
	if got := tb.Spellings("Jane Doe"); !reflect.DeepEqual(got, []string{"Jane Doe", "J. Doe", "Jane"}) {
		t.Errorf("Spellings = %v", got)
	}
	for text, want := range map[string]bool{
		"Met with Jane about the plan.": true,
		"jane doe signed off.":          true,
		"Janet wrote the brief.":        false,
		"See janedoe.dev for it.":       false,
		"Reviewed by J. Doe, twice.":    true,
	} {
		if got := Mentions(text, tb.Spellings("Jane Doe")); got != want {
			t.Errorf("Mentions(%q) = %v, want %v", text, got, want)
		}
	}
}

// A pass's names are kept only where the note's own text names them: the
// colleague stays, the owner and a denied name go, and an inferred name the
// text never states goes too.
func TestGroundKeepsOnlyThePeopleTheNoteNames(t *testing.T) {
	tb := table(t)
	text := "Pat met Jane and Mara Lin at Mount Rainier; Claude drafted the notes."
	got := tb.Ground(text, []string{"Jane Doe", "Mara Lin", "Pat Owner", "Claude", "Mount Rainier", "Geoffrey Hinton", "mara lin"})
	if want := []string{"Jane Doe", "Mara Lin"}; !reflect.DeepEqual(got, want) {
		t.Errorf("Ground = %v, want %v", got, want)
	}
	if got := (Table{}).Ground("Nobody here.", []string{"Jane Doe"}); len(got) != 0 {
		t.Errorf("an ungrounded name survived an empty table: %v", got)
	}
}

// A one-word name matches only as written: "Ben" is a person, "ben" in a
// sentence is not, and a two-word name matches in any case. A one-word name
// that is also a common word ("Will") names no one on its own, since a sentence
// capitalizes it as readily as a person's name (the release review).
func TestAOneWordNameMatchesOnlyAsWritten(t *testing.T) {
	for text, want := range map[string]bool{
		"Ben reviewed it.":           true,
		"the ben of the argument":    false,
		"Will you ship it? Will did": false,
		"Will Tran shipped it":       true,
		"we will ship it":            false,
	} {
		spellings := []string{"Ben Okafor", "Ben"}
		if strings.Contains(text, "ill") {
			spellings = []string{"Will Tran", "Will"}
		}
		if got := Mentions(text, spellings); got != want {
			t.Errorf("Mentions(%q, %v) = %v, want %v", text, spellings, got, want)
		}
	}
	if !Mentions("BEN OKAFOR signed.", []string{"Ben Okafor"}) {
		t.Error("a two-word name did not match in another case")
	}
}

// From the release review (2026-09-30): a name before typographic punctuation
// is a whole word, and a one-word spelling that is a month or a common word
// names no one on its own.
func TestTypographicPunctuationEndsAWordAndAMonthIsNoName(t *testing.T) {
	for _, text := range []string{
		"Ben Okafor’s review style is bottom up.",
		"Decided with Ben Okafor—he agreed.",
		"Ask Ben Okafor…",
	} {
		if !Mentions(text, []string{"Ben Okafor"}) {
			t.Errorf("Mentions(%q, Ben Okafor) = false", text)
		}
	}
	if got := (Table{}).Ground("Ben Okafor’s review style.", []string{"Ben Okafor"}); len(got) != 1 {
		t.Errorf("Ground dropped a name the note states: %v", got)
	}
	for _, text := range []string{"Planning for May: ship the ranker.", "Will you ship it?", "Monday retro."} {
		if Mentions(text, []string{"May", "Will", "Monday"}) {
			t.Errorf("a common word named someone in %q", text)
		}
	}
	if !Mentions("May Chen signed off.", []string{"May Chen", "May"}) {
		t.Error("a full name carrying a common word was not found")
	}
}

// Matching a vault's worth of notes is bounded: 300 names over 4,000 notes of
// about 6 KB, each note prepared once, well inside a night's budget.
func TestMatchingManyNamesOverManyNotesStaysCheap(t *testing.T) {
	if testing.Short() {
		t.Skip("timing")
	}
	para := strings.Repeat("The ranker shipped after the review with the team and the plan moved on. ", 80)
	texts := make([]Text, 4000)
	for i := range texts {
		texts[i] = NewText(fmt.Sprintf("note %d\n%s", i, para))
	}
	start := time.Now()
	for i := 0; i < 300; i++ {
		spellings := []string{fmt.Sprintf("Person%03d Surname", i)}
		for _, tx := range texts {
			tx.Mentions(spellings)
		}
	}
	if d := time.Since(start); d > 3*time.Second {
		t.Errorf("300 names x 4,000 notes took %v", d)
	}
}
