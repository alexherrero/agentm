package index

import (
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
	"github.com/alexherrero/agentm/daemon/internal/rules"
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
	note.SetDampenedSpaces([]string{"personal"})
	t.Cleanup(func() { note.SetDampenedSpaces(before) })

	idx := openScratch(t)
	indexNote(t, idx, "personal/Church/lesson.md", "A lesson", "Notes about the lesson.\n")
	indexNote(t, idx, "agent/memory/semantic/fact.md", "A fact", "Notes about the lesson.\n")

	for _, tc := range []struct {
		rel     string
		flagged bool
	}{
		{"personal/Church/lesson.md", true},
		{"agent/memory/semantic/fact.md", false},
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
	note.SetDampenedSpaces([]string{"personal"})
	t.Cleanup(func() { note.SetDampenedSpaces(before) })

	idx := openScratch(t)
	body := "The staging gate runs before the deployment finishes.\n"
	indexNote(t, idx, "personal/Home/plan.md", "Plan", body)
	indexNote(t, idx, "agent/memory/semantic/plan.md", "Plan", body)

	outcome, err := idx.Search(Query{Text: "staging gate deployment", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	results := outcome.Results
	if len(results) < 2 {
		t.Fatalf("expected both notes, got %d — the fixture cannot show an ordering", len(results))
	}
	if !strings.HasPrefix(results[0].Path, "agent/") {
		t.Errorf("the dampened note ranked first: %s (then %s)",
			results[0].Path, results[1].Path)
	}
	if !strings.HasPrefix(results[1].Path, "personal/") {
		t.Errorf("the dampened note is not present at all; demote never means exclude: %v",
			[]string{results[0].Path, results[1].Path})
	}
}

// Demote, never exclude. When the dampened note is the only answer, it is still
// the answer — this is the property the directory boundary could not offer, and
// the reason the boundary was worth replacing.
func TestADampenedNoteIsStillReturnedWhenItIsTheOnlyAnswer(t *testing.T) {
	before := note.DampenedSpaces()
	note.SetDampenedSpaces([]string{"personal"})
	t.Cleanup(func() { note.SetDampenedSpaces(before) })

	idx := openScratch(t)
	indexNote(t, idx, "personal/Home/Recipes/turkey.md", "Turkey",
		"Brine the turkey overnight before roasting.\n")
	indexNote(t, idx, "agent/memory/semantic/unrelated.md", "Unrelated",
		"Filing is a frontmatter edit.\n")

	outcome, err := idx.Search(Query{Text: "brine turkey roasting", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	results := outcome.Results
	if len(results) == 0 || !strings.HasPrefix(results[0].Path, "personal/") {
		t.Errorf("a distinctive match in a dampened space did not surface: %+v", results)
	}
}

// A contract that names nothing leaves ranking exactly as it was.
func TestNoDampenedSpacesLeavesRankingUnchanged(t *testing.T) {
	before := note.DampenedSpaces()
	note.SetDampenedSpaces(nil)
	t.Cleanup(func() { note.SetDampenedSpaces(before) })

	idx := openScratch(t)
	indexNote(t, idx, "personal/Home/plan.md", "Plan", "The staging gate runs first.\n")

	var flags string
	if err := idx.db.QueryRow(`SELECT flags FROM docmeta WHERE path = ?`,
		"personal/Home/plan.md").Scan(&flags); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(flags, note.ClassSpace) {
		t.Errorf("a space was dampened with no contract naming one: %q", flags)
	}
}

// The drop folder's own dampening, end to end (agentm-vault plan 16).
//
// The plan asserted a contract line — `agent/inbox` in `dampened_spaces` — and
// never proved the ranker honours it for that space. The existing tests here
// all use `personal`, so "a card in the inbox ranks below a filed card of equal
// match" was a criterion nothing measured. Two notes with identical bodies, so
// the only thing separating them is the folder.
func TestAnInboxCardRanksBelowAnIdenticalFiledCard(t *testing.T) {
	before := note.DampenedSpaces()
	note.SetDampenedSpaces([]string{"personal", "agent/diagnostics", "agent/inbox"})
	t.Cleanup(func() { note.SetDampenedSpaces(before) })

	idx := openScratch(t)
	body := "The settle window leaves a card that is still arriving.\n"
	indexNote(t, idx, "agent/inbox/a-thought.md", "A thought", body)
	indexNote(t, idx, "agent/memory/semantic/a-thought.md", "A thought", body)

	outcome, err := idx.Search(Query{Text: "settle window arriving card", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	results := outcome.Results
	if len(results) < 2 {
		t.Fatalf("expected both notes, got %d — the fixture cannot show an ordering", len(results))
	}
	if !strings.HasPrefix(results[0].Path, "agent/memory/") {
		t.Errorf("the inbox card ranked first: %s (then %s)", results[0].Path, results[1].Path)
	}
	if !strings.HasPrefix(results[1].Path, "agent/inbox/") {
		t.Errorf("the inbox card is not present at all; dampened is not walled: %v",
			[]string{results[0].Path, results[1].Path})
	}
}

// Dampened, and deliberately not exempt. The part chose this over
// `recall_exempt_areas` on the reasoning that a card you cannot find until you
// triage it is a card you triage in order to find it — which makes the inbox a
// queue to be drained rather than a place a thought can rest. That reasoning is
// only true if a card in the folder still comes back when it is the answer.
func TestAnInboxCardIsStillReturnedWhenItIsTheOnlyAnswer(t *testing.T) {
	before := note.DampenedSpaces()
	note.SetDampenedSpaces([]string{"agent/inbox"})
	t.Cleanup(func() { note.SetDampenedSpaces(before) })

	idx := openScratch(t)
	indexNote(t, idx, "agent/inbox/drive-route.md", "Drive route",
		"The mirror was the route the whole time.\n")
	indexNote(t, idx, "agent/memory/semantic/unrelated.md", "Unrelated",
		"Filing is a frontmatter edit.\n")

	outcome, err := idx.Search(Query{Text: "mirror route whole time", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(outcome.Results) == 0 || !strings.HasPrefix(outcome.Results[0].Path, "agent/inbox/") {
		t.Errorf("a distinctive match in the drop folder did not surface: %+v", outcome.Results)
	}
}

// The other half of the part's criterion, and the one that keeps "dampened" from
// quietly becoming "exempt": the wall still walls. `recall_exempt_areas` is a
// different list with a different meaning, and the inbox is not on it.
func TestTheWallStillWallsWhileTheInboxIsOnlyDampened(t *testing.T) {
	beforeD, beforeE := note.DampenedSpaces(), note.RecallExemptAreas()
	note.SetDampenedSpaces([]string{"agent/inbox"})
	note.SetRecallExemptAreas([]string{"personal/Home/Important Docs"})
	t.Cleanup(func() {
		note.SetDampenedSpaces(beforeD)
		note.SetRecallExemptAreas(beforeE)
	})

	idx := openScratch(t)
	indexNote(t, idx, "agent/inbox/passport.md", "Passport note",
		"The recovery codes live in the safe.\n")
	indexNote(t, idx, "personal/Home/Important Docs/passport.md", "Passport",
		"The recovery codes live in the safe.\n")

	outcome, err := idx.Search(Query{Text: "recovery codes safe", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	for _, r := range outcome.Results {
		if strings.HasPrefix(r.Path, "personal/Home/Important Docs") {
			t.Errorf("the walled area was served: %s — dampening has been confused "+
				"with exemption", r.Path)
		}
	}
	// And the dampened card, which is not walled, is still there.
	var sawInbox bool
	for _, r := range outcome.Results {
		if strings.HasPrefix(r.Path, "agent/inbox/") {
			sawInbox = true
		}
	}
	if !sawInbox {
		t.Errorf("the inbox card was walled rather than dampened: %+v", outcome.Results)
	}
}

// The contract lines the AgentKV layout rulings added, read from the packaged
// contract itself rather than restated here: `resources/watchlist` joins
// `dampened_spaces` (narrowed on 2026-09-25 from all of `resources`, so a topic
// card still answers a question about its topic), and `standards/templates`
// joins the wall. A test that set the lists by hand would pass with the
// contract unchanged; this one fails the day any of it drifts.
func TestThePackagedContractDampensTheWatchlistAndWallsTheTemplates(t *testing.T) {
	t.Setenv("AGENTM_STORAGE_RULES", "")
	contract, err := rules.Load("")
	if err != nil {
		t.Fatalf("loading the packaged contract: %v", err)
	}
	if !contract.IsPackagedDefault {
		t.Fatalf("expected the embedded contract, got %s", contract.Source)
	}
	beforeD, beforeE := note.DampenedSpaces(), note.RecallExemptAreas()
	note.SetDampenedSpaces(contract.DampenedSpaces)
	note.SetRecallExemptAreas(contract.RecallExemptAreas)
	t.Cleanup(func() {
		note.SetDampenedSpaces(beforeD)
		note.SetRecallExemptAreas(beforeE)
	})

	idx := openScratch(t)
	body := "FTS5 columnsize stores a per-row token count for bm25.\n"
	indexNote(t, idx, "resources/watchlist/openai-research/fts5-columnsize.md", "FTS5 columnsize", body)
	indexNote(t, idx, "agent/memory/semantic/fts5-columnsize.md", "FTS5 columnsize", body)
	indexNote(t, idx, "resources/topics/sqlite/columnsize-detail.md", "Columnsize detail", body)
	// The template carries the query's words too, so only the wall keeps it out:
	// a body that missed the query would pass with the wall gone.
	indexNote(t, idx, "standards/templates/charter.md", "Charter template",
		"Placeholder. "+body)

	for _, tc := range []struct {
		rel      string
		dampened bool
	}{
		{"resources/watchlist/openai-research/fts5-columnsize.md", true},
		{"resources/topics/sqlite/columnsize-detail.md", false},
	} {
		var flags string
		if err := idx.db.QueryRow(`SELECT flags FROM docmeta WHERE path = ?`, tc.rel).Scan(&flags); err != nil {
			t.Fatalf("reading %s's flags: %v", tc.rel, err)
		}
		if got := strings.Contains(flags, note.ClassSpace); got != tc.dampened {
			t.Errorf("%s: dampened = %v, want %v (flags %q)", tc.rel, got, tc.dampened, flags)
		}
	}

	outcome, err := idx.Search(Query{Text: "fts5 columnsize token count", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	var paths []string
	for _, r := range outcome.Results {
		paths = append(paths, r.Path)
		if strings.HasPrefix(r.Path, "standards/templates/") {
			t.Errorf("a template's placeholder text was served: %s", r.Path)
		}
	}
	// The dampened watchlist item ranks below the identical memory and is still
	// present; the undampened topic card is present too.
	pos := map[string]int{}
	for i, p := range paths {
		pos[p] = i + 1
	}
	mem, watch := pos["agent/memory/semantic/fts5-columnsize.md"], pos["resources/watchlist/openai-research/fts5-columnsize.md"]
	if mem == 0 || watch == 0 || watch < mem {
		t.Errorf("want the memory above the dampened watchlist item, both present: %v", paths)
	}
	if pos["resources/topics/sqlite/columnsize-detail.md"] == 0 {
		t.Errorf("the undampened topic card is missing: %v", paths)
	}
}
