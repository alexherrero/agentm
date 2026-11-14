package cardshape

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func card(fields ...string) string {
	return "---\n" + strings.Join(fields, "\n") + "\n---\n\nBody.\n"
}

func statusOf(t *testing.T, text string) string {
	t.Helper()
	for _, line := range strings.Split(strings.SplitN(text, "\n---", 2)[0], "\n") {
		if v, ok := strings.CutPrefix(line, "status: "); ok {
			return v
		}
	}
	return ""
}

// The cases live in one table the Python copy of the rule reads too
// (scripts/test_status_words.py), so the two cannot drift.
func TestStatusFollowsTheNotesState(t *testing.T) {
	blob, err := os.ReadFile(filepath.Join("..", "..", "..", "scripts", "fixtures", "status-sync-cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	var table struct {
		Cases []struct {
			Name  string   `json:"name"`
			Lines []string `json:"lines"`
			Want  string   `json:"want"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(blob, &table); err != nil {
		t.Fatal(err)
	}
	if len(table.Cases) < 10 {
		t.Fatalf("%d cases read; the table did not load", len(table.Cases))
	}
	for _, c := range table.Cases {
		in := card(c.Lines...)
		if got := statusOf(t, SyncStatus(in)); got != c.Want {
			t.Errorf("%s: status %q, want %q\n%s", c.Name, got, c.Want, SyncStatus(in))
		}
	}
}

func TestSyncStatusChangesOnlyTheStatusLine(t *testing.T) {
	in := card("title: a", "type: preference", "status: active", "lifecycle: archived", "lifecycle_since: 2026-11-13", "tags: [x]")
	out := SyncStatus(in)
	if strings.Replace(in, "status: active", "status: archived", 1) != out {
		t.Errorf("more than the status line changed:\n%s", out)
	}
	if again := SyncStatus(out); again != out {
		t.Errorf("a second sync changed an agreeing note:\n%s", again)
	}
}

func TestANoteWithNoStatusIsNotGivenOne(t *testing.T) {
	// A line added here would land out of the card's order; a missing status
	// is the card-shape gate's to report.
	for _, in := range []string{card("title: a", "lifecycle: archived"),
		card("title: a", "consolidated_into: [\"[[l]]\"]", "slug: a")} {
		if got := SyncStatus(in); got != in {
			t.Errorf("a note with no status was given one:\n%s", got)
		}
	}
}

func TestTextWithNoFrontmatterIsLeftAlone(t *testing.T) {
	for _, in := range []string{"", "# a heading\n", "---\nno closing fence\n"} {
		if got := SyncStatus(in); got != in {
			t.Errorf("%q came back as %q", in, got)
		}
	}
}

func TestTheBodyIsNeverRead(t *testing.T) {
	in := card("title: a", "status: active") + "\nlifecycle: archived\nconsolidated_into: [x]\n"
	if got := SyncStatus(in); got != in {
		t.Errorf("a line in the body was read as frontmatter:\n%s", got)
	}
}
