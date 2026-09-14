package index

import (
	"math"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// agentm-vault § Projects and tasks: a card whose `project:` matches the
// session's binding ranks a little above an equal card that does not. The ranker
// never multiplies above 1.0 (the clamp in penalizeRankAndDecay assumes it), so
// the lift is expressed as a mild dampening of every note the query's project
// does not match — a modest boost, not a wall, and nothing at all for a query
// that names no project.

func indexProjectNote(t *testing.T, idx *Index, rel, title, project, body string) {
	t.Helper()
	raw := "---\ntitle: " + title + "\nstatus: active\n"
	if project != "" {
		raw += "project: " + project + "\n"
	}
	raw += "---\n\n" + body
	n := note.Parse(rel, raw, time.Now())
	if err := idx.Upsert(n, time.Now().UnixNano(), int64(len(raw))); err != nil {
		t.Fatalf("indexing %s: %v", rel, err)
	}
}

const projectBody = "The release gate waits for the checks to finish before the tag.\n"

func TestAMatchingProjectRanksAboveItsEqualTwin(t *testing.T) {
	idx := openScratch(t)
	// The unmatched twin sorts first by path, so a tie would put it on top: the
	// order below is the boost, not the tiebreak.
	indexProjectNote(t, idx, "Agent/memory/semantic/a-crickets.md", "Gate", "crickets", projectBody)
	indexProjectNote(t, idx, "Agent/memory/semantic/b-agentm.md", "Gate", "agentm", projectBody)
	indexProjectNote(t, idx, "Agent/memory/semantic/c-unstamped.md", "Gate", "", projectBody)

	for _, mode := range []string{ModeAnd, ModeFusion} {
		out, err := idx.Search(Query{Text: "release gate checks", K: 5, Mode: mode, Project: "agentm"})
		if err != nil {
			t.Fatalf("%s: search: %v", mode, err)
		}
		got := resultPaths(out.Results)
		if len(got) != 3 || !strings.HasSuffix(got[0], "b-agentm.md") {
			t.Fatalf("%s: the matching card is not first: %v", mode, got)
		}
		for _, r := range out.Results[1:] {
			if want := r.RawScore * note.ProjectMismatch; math.Abs(r.Score-want) > 1e-9 {
				t.Errorf("%s: %s scored %.6f, want raw %.6f x %.2f", mode, r.Path, r.Score, r.RawScore, note.ProjectMismatch)
			}
		}
		if first := out.Results[0]; first.Score != first.RawScore {
			t.Errorf("%s: the matching card was dampened: %.6f against raw %.6f", mode, first.Score, first.RawScore)
		}
	}
}

func TestAQueryWithoutAProjectRanksAsBefore(t *testing.T) {
	idx := openScratch(t)
	indexProjectNote(t, idx, "Agent/memory/semantic/a-crickets.md", "Gate", "crickets", projectBody)
	indexProjectNote(t, idx, "Agent/memory/semantic/b-agentm.md", "Gate", "agentm", projectBody)

	for _, mode := range []string{ModeAnd, ModeFusion} {
		out, err := idx.Search(Query{Text: "release gate checks", K: 5, Mode: mode})
		if err != nil {
			t.Fatalf("%s: search: %v", mode, err)
		}
		got := resultPaths(out.Results)
		if len(got) != 2 || !strings.HasSuffix(got[0], "a-crickets.md") {
			t.Errorf("%s: without a project the tie must fall to path order: %v", mode, got)
		}
		for _, r := range out.Results {
			if r.Score != r.RawScore {
				t.Errorf("%s: %s was adjusted with no project asked for: %.6f against %.6f", mode, r.Path, r.Score, r.RawScore)
			}
		}
	}
}

// Modest, not a wall: a clearly better match from another project keeps its place.
func TestTheProjectBoostDoesNotOverturnAClearlyBetterMatch(t *testing.T) {
	idx := openScratch(t)
	indexProjectNote(t, idx, "Agent/memory/semantic/a-better.md", "Release gate checks", "crickets",
		"The release gate checks the release. "+projectBody)
	indexProjectNote(t, idx, "Agent/memory/semantic/b-agentm.md", "Unrelated", "agentm",
		"A note that mentions the gate, the release and checks once.\n")

	out, err := idx.Search(Query{Text: "release gate checks", K: 5, Mode: ModeAnd, Project: "agentm"})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	got := resultPaths(out.Results)
	if len(got) != 2 || !strings.HasSuffix(got[0], "a-better.md") {
		t.Errorf("a modest boost overturned a clearly better match: %v", got)
	}
}

func TestAProjectMatchesWithoutRegardToCase(t *testing.T) {
	idx := openScratch(t)
	indexProjectNote(t, idx, "Agent/memory/semantic/a-crickets.md", "Gate", "crickets", projectBody)
	indexProjectNote(t, idx, "Agent/memory/semantic/b-agentm.md", "Gate", "AgentM", projectBody)

	out, err := idx.Search(Query{Text: "release gate checks", K: 5, Project: "agentm"})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if got := resultPaths(out.Results); len(got) != 2 || !strings.HasSuffix(got[0], "b-agentm.md") {
		t.Errorf("a project spelled with capitals did not match: %v", got)
	}
}

// The dense arm has its own call into the ranker, and a boost in one arm only is
// a disagreement one step further along. Driven with hand-written vectors: the
// unmatched note is the better cosine match, so without the project it wins the
// dense arm and, with the lexical arm tied, the fusion.
func TestTheProjectBoostRunsInsideTheDenseArm(t *testing.T) {
	x := newTestIndex(t)
	for _, n := range []note.Note{
		{Rel: "memory/a-other.md", Title: "gate", Body: "the release gate body", Project: "crickets"},
		{Rel: "memory/b-agentm.md", Title: "gate", Body: "the release gate body", Project: "agentm"},
	} {
		n.Captured, n.CapturedSource = time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), "mtime"
		if err := x.Upsert(n, 1, int64(len(n.Body))); err != nil {
			t.Fatalf("indexing %s: %v", n.Rel, err)
		}
	}
	if err := x.PutVectors("m", []VectorRow{
		{DocID: docID(t, x, "memory/a-other.md"), MtimeNS: 1, Vec: unit(1, 0, 0)},
		{DocID: docID(t, x, "memory/b-agentm.md"), MtimeNS: 1, Vec: unit(0.99, 0.14, 0)},
	}); err != nil {
		t.Fatalf("PutVectors: %v", err)
	}

	without, err := x.Search(Query{Text: "release gate", K: 5, Mode: ModeHybrid, Vector: unit(1, 0, 0), EmbedModel: "m"})
	if err != nil {
		t.Fatalf("hybrid without a project: %v", err)
	}
	if got := resultPaths(without.Results); len(got) == 0 || got[0] != "memory/a-other.md" {
		t.Fatalf("the fixture should favour the unmatched note without a project: %v", got)
	}

	with, err := x.Search(Query{Text: "release gate", K: 5, Mode: ModeHybrid, Vector: unit(1, 0, 0), EmbedModel: "m", Project: "agentm"})
	if err != nil {
		t.Fatalf("hybrid with a project: %v", err)
	}
	if got := resultPaths(with.Results); len(got) == 0 || got[0] != "memory/b-agentm.md" {
		t.Errorf("the dense arm ignored the project: %v", got)
	}
}

func TestTheProjectIsIndexedFromFrontmatter(t *testing.T) {
	idx := openScratch(t)
	indexProjectNote(t, idx, "Agent/memory/semantic/b-agentm.md", "Gate", `"agentm"`, projectBody)
	var project string
	if err := idx.db.QueryRow(`SELECT project FROM docmeta WHERE path = ?`,
		"Agent/memory/semantic/b-agentm.md").Scan(&project); err != nil {
		t.Fatalf("reading the project column: %v", err)
	}
	if project != "agentm" {
		t.Errorf("indexed project = %q, want agentm (quotes stripped)", project)
	}
}
