package index

import (
	"fmt"
	"testing"
)

// Task 179, the operator's ruling of 2026-09-30: a query that names an entity,
// or asks about one in so many words, gets the entity's page first; any other
// query ranks as it did.

func TestAQueryNamesWhatFollowsItsQuestionFrame(t *testing.T) {
	for q, want := range map[string]string{
		"alexherrero/crickets":            "alexherrero/crickets",
		"  Crickets ":                     "crickets",
		"What do I know about crickets?":  "crickets",
		"who is Jane Doe":                 "jane doe",
		"what shipped in agentm v10.0.0?": "agentm v10.0.0",
		"tell me about the crickets":      "crickets",
		"crickets install bug":            "crickets install bug",
		"":                                "",
	} {
		if got := QueryName(q); got != want {
			t.Errorf("QueryName(%q) = %q, want %q", q, got, want)
		}
	}
}

func entityPage(title, kind, typ, id string, aliases, body string) string {
	return fmt.Sprintf("---\ntitle: %q\nkind: %s\ncreated: 2026-09-30\nupdated: 2026-09-30\n"+
		"entity_type: %s\nentity_id: %q\nslug: x\naliases: %s\n---\n\n# %s\n\n%s\n", title, kind, typ, id, aliases, title, body)
}

// namedVault holds a repository page whose long list of notes loses to short
// issue pages carrying the same name, the shape the live index had.
func namedVault(t *testing.T) *Index {
	t.Helper()
	x, vault := newVaultIndex(t)
	long := ""
	for i := 0; i < 80; i++ {
		long += fmt.Sprintf("- 2026-09-%02d · [[projects/agentm/tasks/%03d-some-task/plan|%03d some task plan]]\n", i%28+1, i, i)
	}
	writeVaultNote(t, vault, "agent/memory/entities/repos/alexherrero-crickets.md",
		entityPage("alexherrero/crickets", "entity-profile", "repo", "repo:alexherrero/crickets", "[crickets]", long))
	for _, n := range []int{35, 36, 40, 42} {
		writeVaultNote(t, vault, fmt.Sprintf("agent/memory/entities/issues/alexherrero-crickets-%d.md", n),
			entityPage(fmt.Sprintf("alexherrero/crickets#%d", n), "entity-profile", "issue",
				fmt.Sprintf("issue:alexherrero/crickets#%d", n), fmt.Sprintf("[\"crickets#%d\"]", n),
				"- 2026-09-28 · [[a|alexherrero/crickets fix]]"))
	}
	writeVaultNote(t, vault, "agent/memory/entities/people/jane-doe.md",
		entityPage("Jane Doe", "entity-profile", "person", "person:jane-doe", "[Jane]", "## Shared work"))
	// A hand-written note in the folder is no entity page.
	writeVaultNote(t, vault, "agent/memory/entities/people/notes-on-mara.md",
		"---\ntitle: Mara Lin\ntype: reference\n---\n\nMara Lin, a note by hand.\n")
	writeVaultNote(t, vault, "agent/memory/semantic/crickets-install.md",
		"---\ntitle: crickets install bug\n---\n\nThe crickets install bug and its fix.\n")
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}
	return x
}

func firstPath(t *testing.T, x *Index, q string, mode string) (string, []string) {
	t.Helper()
	out, err := x.Search(Query{Text: q, K: 5, Mode: mode})
	if err != nil {
		t.Fatal(err)
	}
	var paths []string
	for _, r := range out.Results {
		paths = append(paths, r.Path)
	}
	if len(paths) == 0 {
		return "", nil
	}
	return paths[0], paths
}

func TestAQueryNamingAnEntityGetsItsPageFirst(t *testing.T) {
	x := namedVault(t)
	for q, want := range map[string]string{
		"alexherrero/crickets":           "agent/memory/entities/repos/alexherrero-crickets.md",
		"crickets":                       "agent/memory/entities/repos/alexherrero-crickets.md",
		"What do I know about crickets?": "agent/memory/entities/repos/alexherrero-crickets.md",
		"crickets#40":                    "agent/memory/entities/issues/alexherrero-crickets-40.md",
		"who is Jane Doe":                "agent/memory/entities/people/jane-doe.md",
		"who is Jane":                    "agent/memory/entities/people/jane-doe.md",
	} {
		for _, mode := range []string{ModeAnd, ModeFusion} {
			if got, all := firstPath(t, x, q, mode); got != want {
				t.Errorf("%s %q: first %q, want %q (all %v)", mode, q, got, want, all)
			}
		}
	}
	// The head of an added page is filled like any served row's.
	out, err := x.Search(Query{Text: "who is Jane Doe", K: 5})
	if err != nil {
		t.Fatal(err)
	}
	if len(out.Results) == 0 || out.Results[0].Title != "Jane Doe" || out.Results[0].Kind != "entity-profile" {
		t.Errorf("the added page's head: %+v", out.Results)
	}
}

// Every other query ranks as it did: a query that only contains a name, a name
// a hand-written note carries, and a name two pages share name no page.
func TestAQueryThatNamesNoEntityPageIsUntouched(t *testing.T) {
	x := namedVault(t)
	if got, _ := firstPath(t, x, "crickets install bug", ModeAnd); got != "agent/memory/semantic/crickets-install.md" {
		t.Errorf("a query containing a name was redirected to %q", got)
	}
	if got, _ := firstPath(t, x, "who is Mara Lin", ModeAnd); got == "agent/memory/entities/people/notes-on-mara.md" {
		t.Error("a hand-written note in entities/ was taken for an entity page")
	}
	writeVaultNote(t, x.vault, "agent/memory/entities/repos/other-crickets.md",
		entityPage("other/crickets", "entity-profile", "repo", "repo:other/crickets", "[crickets]", "- a"))
	if err := x.IndexFile("agent/memory/entities/repos/other-crickets.md"); err != nil {
		t.Fatal(err)
	}
	out := x.PutNamedEntityFirst("crickets", nil, 5)
	if len(out) != 0 {
		t.Errorf("a name two pages share put %v first", out)
	}
	// The new page's own full name works at once: the cache fell with the write.
	if out := x.PutNamedEntityFirst("other/crickets", nil, 5); len(out) != 1 ||
		out[0].Path != "agent/memory/entities/repos/other-crickets.md" {
		t.Errorf("a page written after the first lookup is not found by name: %v", out)
	}
}

// A date-bounded search is a question about a time: the exact-name rule does
// not serve a page outside the bound into it (the release review).
func TestABoundedSearchIsNotGivenANamedPage(t *testing.T) {
	x := namedVault(t)
	out, err := x.Search(Query{Text: "alexherrero/crickets", K: 5, After: "2099-01-01"})
	if err != nil {
		t.Fatal(err)
	}
	if len(out.Results) != 0 {
		t.Errorf("After=2099-01-01 returned %s", out.Results[0].Path)
	}
}
