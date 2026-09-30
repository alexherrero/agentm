package people

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
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
