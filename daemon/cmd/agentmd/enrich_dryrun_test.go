package main

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/index"
)

// agentm-vault plan 09, task 7 review: the dry run is what sizes a night before
// anything is spent, and plan 09 put the project records in the queue it sizes.
// Two things it says have to hold. The queue's two populations are counted
// apart, because one number over both reads as a card count and is not. And what
// it measures after a cursor is what the night would run after that cursor —
// the lister pages the queue by position, so a dry run comparing paths measures
// a different set the moment a card's path sorts after a record's.

// captureStdout runs f with os.Stdout on a pipe and returns what it printed.
// Read while f runs rather than after it: a dry run over a long queue prints
// more than a pipe holds, and a reader that waited would deadlock the writer.
func captureStdout(t *testing.T, f func() error) string {
	t.Helper()
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	saved := os.Stdout
	os.Stdout = w
	read := make(chan string, 1)
	go func() {
		b, _ := io.ReadAll(r)
		read <- string(b)
	}()
	ferr := f()
	os.Stdout = saved
	w.Close()
	out := <-read
	r.Close()
	if ferr != nil {
		t.Fatalf("the dry run: %v\n%s", ferr, out)
	}
	return out
}

var owedLine = regexp.MustCompile(`owed the deep pass (\d+) · the light pass (\d+) · ` +
	`unchanged at this pass (\d+) · unreadable (\d+)`)

// dryRunOwed are the four counts the dry run's second line prints, in its order.
func dryRunOwed(t *testing.T, out string) [4]int {
	t.Helper()
	m := owedLine.FindStringSubmatch(out)
	if m == nil {
		t.Fatalf("no line saying what the queue is owed:\n%s", out)
	}
	var got [4]int
	for i := range got {
		n, err := strconv.Atoi(m[i+1])
		if err != nil {
			t.Fatalf("the owed line did not parse (%v): %q", err, m[0])
		}
		got[i] = n
	}
	return got
}

// dryRunOffered are the paths the dry run lists as the next page. They come from
// the lister the night itself pages with, so what the counts are checked against
// is the real queue's answer rather than a second copy of the rule.
func dryRunOffered(out string) []string {
	paths := []string{}
	listing := false
	for _, line := range strings.Split(out, "\n") {
		if strings.Contains(line, "the queue would offer:") {
			listing = true
			continue
		}
		if !listing {
			continue
		}
		if !strings.HasPrefix(line, "    ") {
			break
		}
		paths = append(paths, strings.TrimSpace(line))
	}
	return paths
}

func TestTheDryRunCountsCardsAndRecordsApartAndSizesWhatTheCursorWouldServe(t *testing.T) {
	state := t.TempDir()
	t.Setenv("AGENTM_STATE_DIR", state)
	t.Setenv("AGENTM_STORAGE_RULES", "")
	os.Unsetenv("AGENTM_STORAGE_RULES")
	vault := t.TempDir()
	writeRules(t, vault, "reference")

	// A memory root that sorts after `Projects`, where the queue's order and path
	// order disagree: the records are last in the queue and first in path order.
	// The live `Agent` root happens to agree with path order, which is why a dry
	// run that compared paths looked right there.
	kernel := filepath.Join(t.TempDir(), "agentm-config.json")
	if err := os.WriteFile(kernel,
		[]byte(`{"plugins.obsidian-vault.memory_root": "agent"}`), 0o644); err != nil {
		t.Fatal(err)
	}

	const (
		firstCard = "agent/memory/semantic/a-card.md"
		lastCard  = "agent/memory/semantic/b-card.md"
		charter   = "projects/agentm/_index.md"
		decision  = "projects/agentm/decisions/keep-the-wall.md"
		research  = "projects/agentm/research/wall/sources.md"
	)
	// Stamped by this pass, so it is owed the light pass while everything else is
	// owed the deep one: a count that took the cards for the records, or the
	// records for the cards, would report a different split.
	stamped := fmt.Sprintf("---\ntype: reference\nenriched_at: 2026-09-01T00:00:00Z\n"+
		"enriched_by: %s\n---\n\nArchived notes stay walled.\n", enrich.PassVersion)
	notes := map[string]string{
		firstCard: "---\ntype: reference\nstatus: unfiled\n---\n\nDrive corrupts git.\n",
		lastCard:  "---\ntype: reference\nstatus: unfiled\n---\n\nThe daemon runs the source clone.\n",
		charter:   "---\nkind: charter\n---\n\n# agentm\n\nWhat it is.\n",
		decision:  stamped,
		research:  "---\ntype: reference\n---\n\nWhat the sources said.\n",
		// A session's files, which are in the projects space and in neither count.
		"projects/agentm/tracker.md":          "---\nkind: tracker\n---\n\n## State\n\nBuilding.\n",
		"projects/agentm/tasks/t/plan.md":     "# Build it\n\n- [ ] the first step\n",
		"projects/agentm/tasks/t/progress.md": "# Progress\n\n- started\n",
	}
	idxPath := filepath.Join(t.TempDir(), "index.db")
	x, err := index.Open(idxPath, vault, "agent", false)
	if err != nil {
		t.Fatal(err)
	}
	for rel, body := range notes {
		putNote(t, x, vault, rel, body)
	}
	x.Close()

	// A page bigger than the queue, so the listing is the whole population the
	// cursor would serve rather than a first slice of it.
	dryRun := func(after string) string {
		args := []string{"-config", kernel, "-vault", vault, "-index", idxPath,
			"-page-size", "10", "-dry-run"}
		if after != "" {
			args = append(args, "-after", after)
		}
		return captureStdout(t, func() error { return cmdEnrich(args) })
	}

	cases := []struct {
		name    string
		after   string
		owed    [4]int
		offered []string
	}{
		{"the whole queue", "", [4]int{4, 1, 0, 0},
			[]string{firstCard, lastCard, charter, decision, research}},
		// The cursor plan 09 made possible: the cards are done and the records
		// are what is left. Comparing paths counted none of them, because every
		// `projects/…` path sorts before every `agent/…` one.
		{"a cursor on the last card", lastCard, [4]int{2, 1, 0, 0},
			[]string{charter, decision, research}},
		// And inside the records, where comparing paths counted the two cards
		// again — a night already past them.
		{"a cursor on the charter", charter, [4]int{1, 1, 0, 0},
			[]string{decision, research}},
	}
	for _, c := range cases {
		out := dryRun(c.after)
		// The summary is about the queue, not the slice, so it says the same
		// thing under every cursor: an empty drop folder, two cards and three
		// records, with the tracker, plan and progress log counted as none of
		// them. Three populations since agentm-vault plan 16 put the inbox at
		// the front of the queue; the thing being pinned is unchanged — each
		// population is counted as itself, because one number over them all
		// read as a card count and was not.
		if want := "dry run: 0 inbox card(s) under agent/inbox/, 2 card(s) under " +
			"agent/memory/semantic/ and 3 project record(s), served in that " +
			"order and oldest first\n"; !strings.Contains(out, want) {
			t.Errorf("%s: the summary line is not %q:\n%s", c.name, strings.TrimSuffix(want, "\n"), out)
		}
		owed := dryRunOwed(t, out)
		if owed != c.owed {
			t.Errorf("%s: owed deep·light·unchanged·unreadable %v, want %v:\n%s",
				c.name, owed, c.owed, out)
		}
		if got := dryRunOffered(out); !reflect.DeepEqual(got, c.offered) {
			t.Errorf("%s: the queue would offer %v, want %v", c.name, got, c.offered)
		}
		// The two halves of the dry run measure one population: what it counted
		// is what the lister would serve. A count taken over any other set can
		// be right about neither.
		if total := owed[0] + owed[1] + owed[2] + owed[3]; total != len(c.offered) {
			t.Errorf("%s: sized %d notes and the lister would serve %d",
				c.name, total, len(c.offered))
		}
	}
}
