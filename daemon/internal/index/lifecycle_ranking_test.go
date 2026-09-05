package index

import (
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// The lifecycle axis in ranking (filing v2 part 6, task 1). A dormant twin ranks
// below its active twin and stays present; an archived twin is absent from
// everyday search, counted, and back on the explicit archive query.

func indexLifecycle(t *testing.T, idx *Index, rel, title, lifecycle, body string) {
	t.Helper()
	raw := "---\ntitle: " + title + "\nstatus: active\n"
	if lifecycle != "" {
		raw += "lifecycle: " + lifecycle + "\n"
	}
	raw += "---\n\n" + body
	n := note.Parse(rel, raw, time.Now())
	if err := idx.Upsert(n, time.Now().UnixNano(), int64(len(raw))); err != nil {
		t.Fatalf("indexing %s: %v", rel, err)
	}
}

func TestADormantTwinRanksBelowItsActiveTwinAndStaysPresent(t *testing.T) {
	idx := openScratch(t)
	body := "The release gate waits for the checks to finish before the tag.\n"
	// The dormant twin sorts first by path, so a tie would put it on top: the
	// ordering below is the demotion, not the tiebreak.
	indexLifecycle(t, idx, "Agent/memory/semantic/a-dormant.md", "Gate", "dormant", body)
	indexLifecycle(t, idx, "Agent/memory/semantic/b-active.md", "Gate", "active", body)

	for _, mode := range []string{ModeAnd, ModeFusion} {
		out, err := idx.Search(Query{Text: "release gate checks", K: 5, Mode: mode})
		if err != nil {
			t.Fatalf("%s: search: %v", mode, err)
		}
		if len(out.Results) != 2 {
			t.Fatalf("%s: expected both twins, got %v — demote never means exclude", mode, resultPaths(out.Results))
		}
		if !strings.HasSuffix(out.Results[0].Path, "b-active.md") || !strings.HasSuffix(out.Results[1].Path, "a-dormant.md") {
			t.Errorf("%s: the dormant twin did not rank below its active twin: %v", mode, resultPaths(out.Results))
		}
		if !strings.Contains(out.Results[1].Penalty, note.ClassDormant) {
			t.Errorf("%s: the demotion is not visible on the row: penalty=%q", mode, out.Results[1].Penalty)
		}
		if out.ArchivedHidden != 0 {
			t.Errorf("%s: nothing is archived here, yet archived_hidden=%d", mode, out.ArchivedHidden)
		}
	}
}

func TestAnArchivedNoteIsWalledFromEverydaySearchAndBackOnTheExplicitQuery(t *testing.T) {
	idx := openScratch(t)
	body := "The release gate waits for the checks to finish before the tag.\n"
	indexLifecycle(t, idx, "Agent/memory/semantic/a-archived.md", "Gate", "archived", body)
	indexLifecycle(t, idx, "Agent/memory/semantic/b-active.md", "Gate", "active", body)

	for _, mode := range []string{ModeAnd, ModeFusion} {
		everyday, err := idx.Search(Query{Text: "release gate checks", K: 5, Mode: mode})
		if err != nil {
			t.Fatalf("%s: search: %v", mode, err)
		}
		if len(everyday.Results) != 1 || !strings.HasSuffix(everyday.Results[0].Path, "b-active.md") {
			t.Errorf("%s: everyday search should see only the active twin: %v", mode, resultPaths(everyday.Results))
		}
		if everyday.ArchivedHidden != 1 {
			t.Errorf("%s: the wall must count what it hid: archived_hidden=%d, want 1", mode, everyday.ArchivedHidden)
		}
		if everyday.Matched != 2 {
			t.Errorf("%s: the window saw both rows before the wall: matched=%d, want 2", mode, everyday.Matched)
		}

		explicit, err := idx.Search(Query{Text: "release gate checks", K: 5, Mode: mode, IncludeArchived: true})
		if err != nil {
			t.Fatalf("%s: explicit archive query: %v", mode, err)
		}
		if len(explicit.Results) != 2 {
			t.Fatalf("%s: the explicit archive query should see both: %v", mode, resultPaths(explicit.Results))
		}
		if !strings.HasSuffix(explicit.Results[0].Path, "b-active.md") || !strings.HasSuffix(explicit.Results[1].Path, "a-archived.md") {
			t.Errorf("%s: included, the archived twin is present and demoted, not restored to parity: %v", mode, resultPaths(explicit.Results))
		}
		if explicit.ArchivedHidden != 0 {
			t.Errorf("%s: nothing hidden on the explicit query, got archived_hidden=%d", mode, explicit.ArchivedHidden)
		}
	}
}

// A note that is the only answer and happens to be archived is still absent
// from everyday search — that is the contract's own choice, and the outcome
// says so rather than returning nothing silently.
func TestTheWallIsVisibleWhenItEmptiesTheResult(t *testing.T) {
	idx := openScratch(t)
	indexLifecycle(t, idx, "Agent/memory/semantic/only.md", "Only", "archived",
		"The obsolete deployment runbook named the old bastion host.\n")
	out, err := idx.Search(Query{Text: "bastion runbook", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) != 0 || out.ArchivedHidden != 1 {
		t.Errorf("got %v with archived_hidden=%d; want no rows and archived_hidden=1", resultPaths(out.Results), out.ArchivedHidden)
	}
}

func TestAPinnedTwinIsNeitherDemotedNorWalled(t *testing.T) {
	idx := openScratch(t)
	body := "The release gate waits for the checks to finish before the tag.\n"
	indexLifecycle(t, idx, "Agent/memory/semantic/a-pinned.md", "Gate", "pinned", body)
	indexLifecycle(t, idx, "Agent/memory/semantic/b-active.md", "Gate", "active", body)
	out, err := idx.Search(Query{Text: "release gate checks", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) != 2 {
		t.Fatalf("expected both, got %v", resultPaths(out.Results))
	}
	if out.Results[0].Score != out.Results[1].Score {
		t.Errorf("pinned and active twins should tie on score: %v vs %v", out.Results[0].Score, out.Results[1].Score)
	}
	for _, r := range out.Results {
		if strings.HasSuffix(r.Path, "a-pinned.md") && !strings.Contains(r.Penalty, note.ClassDurable) {
			t.Errorf("the pinned twin should carry the durable class: penalty=%q", r.Penalty)
		}
	}
}

// The dense arm is walled too, and its count reaches the outcome: a query whose
// vector matches an archived note exactly still does not get it back on an
// everyday hybrid search, and the explicit archive query does.
func TestTheWallReachesTheDenseArm(t *testing.T) {
	x := newTestIndex(t)
	addNote(t, x, "memory/live.md", "live", "the live note says something else entirely")
	indexLifecycle(t, x, "memory/cold.md", "cold", "archived", "the archived note is the only cosine match\n")
	if err := x.PutVectors("m", []VectorRow{
		{DocID: docID(t, x, "memory/cold.md"), MtimeNS: 1, Vec: unit(1, 0, 0)},
		{DocID: docID(t, x, "memory/live.md"), MtimeNS: 1, Vec: unit(0, 0, 1)},
	}); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}
	everyday, err := x.Search(Query{Text: "live note", K: 5, Mode: ModeHybrid, Vector: unit(1, 0, 0), EmbedModel: "m"})
	if err != nil {
		t.Fatalf("hybrid: %v", err)
	}
	for _, r := range everyday.Results {
		if r.Path == "memory/cold.md" {
			t.Errorf("the dense arm returned an archived note on an everyday query: %v", resultPaths(everyday.Results))
		}
	}
	if everyday.ArchivedHidden < 1 {
		t.Errorf("the dense arm's wall did not count: archived_hidden=%d", everyday.ArchivedHidden)
	}
	explicit, err := x.Search(Query{Text: "live note", K: 5, Mode: ModeHybrid, Vector: unit(1, 0, 0), EmbedModel: "m", IncludeArchived: true})
	if err != nil {
		t.Fatalf("hybrid, explicit: %v", err)
	}
	var found bool
	for _, r := range explicit.Results {
		found = found || r.Path == "memory/cold.md"
	}
	if !found || explicit.ArchivedHidden != 0 {
		t.Errorf("the explicit archive query should surface the archived note through the dense arm: %v (archived_hidden=%d)",
			resultPaths(explicit.Results), explicit.ArchivedHidden)
	}
}
