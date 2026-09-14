package index

import (
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// agentm-vault § Projects and tasks, in the index: a progress log is read from a
// bounded head, and a project's completed records rank at x0.30 of their twins.

func writeAndIndex(t *testing.T, x *Index, rel, body string) {
	t.Helper()
	abs := filepath.Join(x.vault, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(abs, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := x.IndexFile(rel); err != nil {
		t.Fatalf("indexing %s: %v", rel, err)
	}
}

func TestAProgressLogIsIndexedFromItsHead(t *testing.T) {
	x := newTestIndex(t)
	head := "# Progress\n\n2026-09-12 the release gate checks ran clean.\n"
	tail := "\n2026-09-13 zanzibar quokka arrived in the tail.\n"
	oneMegabyte := head + filler(1<<20) + tail

	writeAndIndex(t, x, "projects/demo/_harness/progress-big.md", oneMegabyte)
	writeAndIndex(t, x, "projects/demo/_harness/progress-small.md", head)
	// The same megabyte under a name that is not a progress log is read whole.
	writeAndIndex(t, x, "projects/demo/research/dump.md", oneMegabyte)

	tailHits, err := x.Search(Query{Text: "zanzibar quokka", K: 5})
	if err != nil {
		t.Fatalf("tail search: %v", err)
	}
	if got := resultPaths(tailHits.Results); len(got) != 1 || got[0] != "projects/demo/research/dump.md" {
		t.Errorf("a term only in a progress log's tail was found, or the dump lost its tail: %v", got)
	}

	headHits, err := x.Search(Query{Text: "release gate checks", K: 5})
	if err != nil {
		t.Fatalf("head search: %v", err)
	}
	scores := map[string]float64{}
	for _, r := range headHits.Results {
		scores[r.Path] = r.Score
	}
	bigScore, bigOK := scores["projects/demo/_harness/progress-big.md"]
	smallScore, smallOK := scores["projects/demo/_harness/progress-small.md"]
	if !bigOK || !smallOK {
		t.Fatalf("both logs should match their shared head: %v", resultPaths(headHits.Results))
	}
	if bigScore > smallScore {
		t.Errorf("a 1 MB log outranks its own head: %.6f > %.6f", bigScore, smallScore)
	}

	_, body, ok, err := x.DocText("projects/demo/_harness/progress-big.md")
	if err != nil || !ok {
		t.Fatalf("DocText: ok=%v err=%v", ok, err)
	}
	if len(body) > note.ProgressHeadBytes {
		t.Errorf("indexed %d bytes of a progress log, over the %d bound", len(body), note.ProgressHeadBytes)
	}
}

func TestACompletedRecordRanksAtThirtyPercentOfItsTwin(t *testing.T) {
	x := newTestIndex(t)
	body := "The release gate waits for the checks to finish before the tag.\n"
	for _, rel := range []string{"projects/agentm/completed/a-brief.md", "projects/agentm/research/b-brief.md"} {
		raw := "---\ntitle: Gate\nstatus: active\n---\n\n" + body
		n := note.Parse(rel, raw, time.Now())
		if err := x.Upsert(n, 1, int64(len(raw))); err != nil {
			t.Fatalf("indexing %s: %v", rel, err)
		}
	}
	out, err := x.Search(Query{Text: "release gate checks", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	got := resultPaths(out.Results)
	// The completed twin sorts first by path, so a tie would put it on top.
	if len(got) != 2 || got[0] != "projects/agentm/research/b-brief.md" {
		t.Fatalf("the completed record was not demoted below its twin: %v", got)
	}
	done := out.Results[1]
	if !strings.Contains(done.Penalty, note.ClassCompleted) {
		t.Errorf("the class is not visible on the row: %q", done.Penalty)
	}
	if want := done.RawScore * 0.30; math.Abs(done.Score-want) > 1e-9 {
		t.Errorf("completed row scored %.6f, want raw %.6f x 0.30", done.Score, done.RawScore)
	}
	if out.ArchivedHidden != 0 || out.Matched != 2 {
		t.Errorf("a completed record is penalized, never walled: hidden=%d matched=%d", out.ArchivedHidden, out.Matched)
	}
}
