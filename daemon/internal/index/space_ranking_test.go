package index

import (
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// The end-to-end path the unit tests do not cover: a space named in the contract
// becomes a flag on the indexed row, and that flag demotes the note in a real
// ranking.
//
// Worth its own test because the first live run of this change was a no-op, and
// for two reasons neither unit test could have caught. The vault's own rules file
// wins resolution and did not carry the key — the arrangement working exactly as
// designed, and invisible from inside the daemon. And flags are computed at index
// time, so a contract change does nothing until the affected notes are reindexed.
// Both are deployment facts rather than code faults, and both are the kind of
// thing that ships as a silent no-op unless something asserts the chain.

func indexNote(t *testing.T, idx *Index, rel, title, body string) {
	t.Helper()
	raw := "---\ntitle: " + title + "\nstatus: active\n---\n\n" + body
	n := note.Parse(rel, raw, time.Now())
	if err := idx.Upsert(n, time.Now().UnixNano(), int64(len(raw))); err != nil {
		t.Fatalf("indexing %s: %v", rel, err)
	}
}

func TestADampenedSpaceIsFlaggedOnTheIndexedRow(t *testing.T) {
	before := note.DampenedSpaces()
	note.SetDampenedSpaces([]string{"Personal"})
	t.Cleanup(func() { note.SetDampenedSpaces(before) })

	idx := openScratch(t)
	indexNote(t, idx, "Personal/Church/lesson.md", "A lesson", "Notes about the lesson.\n")
	indexNote(t, idx, "Agent/memory/semantic/fact.md", "A fact", "Notes about the lesson.\n")

	for _, tc := range []struct {
		rel     string
		flagged bool
	}{
		{"Personal/Church/lesson.md", true},
		{"Agent/memory/semantic/fact.md", false},
	} {
		var flags string
		err := idx.db.QueryRow(`SELECT flags FROM docmeta WHERE path = ?`, tc.rel).Scan(&flags)
		if err != nil {
			t.Fatalf("reading flags for %s: %v", tc.rel, err)
		}
		got := strings.Contains(flags, note.ClassSpace)
		if got != tc.flagged {
			t.Errorf("%s: space flag present = %v, want %v (flags: %q)",
				tc.rel, got, tc.flagged, flags)
		}
	}
}

// The flag has to change the ranking, not merely exist. Two notes with identical
// bodies, so the only thing separating them is the space.
func TestTheDampenedNoteRanksBelowAnIdenticalOne(t *testing.T) {
	before := note.DampenedSpaces()
	note.SetDampenedSpaces([]string{"Personal"})
	t.Cleanup(func() { note.SetDampenedSpaces(before) })

	idx := openScratch(t)
	body := "The staging gate runs before the deployment finishes.\n"
	indexNote(t, idx, "Personal/Home/plan.md", "Plan", body)
	indexNote(t, idx, "Agent/memory/semantic/plan.md", "Plan", body)

	outcome, err := idx.Search(Query{Text: "staging gate deployment", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	results := outcome.Results
	if len(results) < 2 {
		t.Fatalf("expected both notes, got %d — the fixture cannot show an ordering", len(results))
	}
	if !strings.HasPrefix(results[0].Path, "Agent/") {
		t.Errorf("the dampened note ranked first: %s (then %s)",
			results[0].Path, results[1].Path)
	}
	if !strings.HasPrefix(results[1].Path, "Personal/") {
		t.Errorf("the dampened note is not present at all; demote never means exclude: %v",
			[]string{results[0].Path, results[1].Path})
	}
}

// Demote, never exclude. When the dampened note is the only answer, it is still
// the answer — this is the property the directory boundary could not offer, and
// the reason the boundary was worth replacing.
func TestADampenedNoteIsStillReturnedWhenItIsTheOnlyAnswer(t *testing.T) {
	before := note.DampenedSpaces()
	note.SetDampenedSpaces([]string{"Personal"})
	t.Cleanup(func() { note.SetDampenedSpaces(before) })

	idx := openScratch(t)
	indexNote(t, idx, "Personal/Home/Recipes/turkey.md", "Turkey",
		"Brine the turkey overnight before roasting.\n")
	indexNote(t, idx, "Agent/memory/semantic/unrelated.md", "Unrelated",
		"Filing is a frontmatter edit.\n")

	outcome, err := idx.Search(Query{Text: "brine turkey roasting", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	results := outcome.Results
	if len(results) == 0 || !strings.HasPrefix(results[0].Path, "Personal/") {
		t.Errorf("a distinctive match in a dampened space did not surface: %+v", results)
	}
}

// A contract that names nothing leaves ranking exactly as it was.
func TestNoDampenedSpacesLeavesRankingUnchanged(t *testing.T) {
	before := note.DampenedSpaces()
	note.SetDampenedSpaces(nil)
	t.Cleanup(func() { note.SetDampenedSpaces(before) })

	idx := openScratch(t)
	indexNote(t, idx, "Personal/Home/plan.md", "Plan", "The staging gate runs first.\n")

	var flags string
	if err := idx.db.QueryRow(`SELECT flags FROM docmeta WHERE path = ?`,
		"Personal/Home/plan.md").Scan(&flags); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(flags, note.ClassSpace) {
		t.Errorf("a space was dampened with no contract naming one: %q", flags)
	}
}
