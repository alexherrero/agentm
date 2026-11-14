package people

import (
	"strings"
	"testing"
)

// Task 202: a page is built only for a person the table lists by full name,
// and a spelling counts only when it names exactly one of them.
func TestOnlyListedPeopleAreNamed(t *testing.T) {
	tb, err := Parse("```people\nyou: [Pat Owner]\naliases:\n" +
		"  Kevin Smith: [Kevin, Kev]\n  Kevin Jones: [Kevin]\n  Jane Doe: [Jane]\n  Claude: []\n" +
		"deny: [Claude, Samus]\n```\n")
	if err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(tb.People(), ", "); got != "Jane Doe, Kevin Jones, Kevin Smith" {
		t.Errorf("people = %s; a denied name is not a person even when listed", got)
	}
	for _, c := range []struct {
		name, want string
		ok         bool
	}{
		{"Kevin Smith", "Kevin Smith", true},
		{"kevin smith", "Kevin Smith", true},
		{"Kev", "Kevin Smith", true},
		{"Kevin", "", false}, // two listed Kevins: it names neither
		{"Jane", "Jane Doe", true},
		{"Samus", "", false}, // denied
		{"Pat Owner", "", false},
		{"Ravi", "", false}, // never listed
	} {
		got, ok := tb.Listed(c.name)
		if got != c.want || ok != c.ok {
			t.Errorf("Listed(%q) = %q, %v; want %q, %v", c.name, got, ok, c.want, c.ok)
		}
	}
	if got := strings.Join(tb.ListedSpellings("Kevin Smith"), ", "); got != "Kevin Smith, Kev" {
		t.Errorf("Kevin Smith's spellings = %s; the shared \"Kevin\" is not one", got)
	}
	if got := strings.Join(tb.ListedSpellings("Jane Doe"), ", "); got != "Jane Doe, Jane" {
		t.Errorf("Jane Doe's spellings = %s", got)
	}
	if len(Table{}.People()) != 0 {
		t.Errorf("an empty table lists %v", Table{}.People())
	}
}
