package index

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// An entity page dates a mention by the note's own frontmatter, never by its
// file (task 182 step 6). A move, a rename or a machine rewrite sets the
// modification time: the task-177 move of 2026-09-28 made old dev-setup plans,
// which carry no frontmatter date, read as dev-setup work that day.

func TestNoteDayNeverFallsBackToTheFile(t *testing.T) {
	if got := noteDay("", "", "2026-09-28T12:00:00Z", "mtime", ""); got != "" {
		t.Errorf("a note with no date of its own is undated, not dated by its file: %q", got)
	}
	for _, c := range []struct{ updated, created, captured, want string }{
		{`"2026-06-26"`, "2026-06-01", "", "2026-06-26"},
		{"", "2026-06-01", "", "2026-06-01"},
		{"", "", "2026-05-19T09:00:00Z", "2026-05-19"},
	} {
		if got := noteDay(c.updated, c.created, c.captured, "frontmatter:captured", ""); got != c.want {
			t.Errorf("noteDay(%q, %q, %q) = %q, want %q", c.updated, c.created, c.captured, got, c.want)
		}
	}
}

// Enrichment's rewrite sets `updated` to its own day; that is a pass, not work.
func TestAnEnrichmentRewriteDoesNotDateTheMention(t *testing.T) {
	if got := noteDay(`"2026-10-01"`, "2026-06-26", "", "frontmatter:created", `"2026-10-01T09:12:42Z"`); got != "2026-06-26" {
		t.Errorf("an updated on the enrichment day falls back to created; got %q", got)
	}
	if got := noteDay("2026-10-01", "2026-06-26", "", "frontmatter:created", "2026-09-20T09:00:00Z"); got != "2026-10-01" {
		t.Errorf("an updated after the last enrichment is someone's edit; got %q", got)
	}
}

func TestAMovedNoteKeepsItsDateInTheEntityRows(t *testing.T) {
	idx := openScratch(t)
	plan := "---\nparent_design_doc: wiki/designs/x.md\n---\n\n# Plan: install\n"
	dated := "---\ntitle: a decision\nkind: decision\nupdated: 2026-06-26\n---\n\nA ruling.\n"
	enriched := "---\ntitle: stack\ntype: reference\ncreated: 2026-06-19\nupdated: \"2026-10-01\"\n" +
		"enriched_at: \"2026-10-01T09:00:00Z\"\n---\n\nThe stack.\n"
	moved := time.Date(2026, 9, 28, 12, 0, 0, 0, time.UTC).UnixNano()
	for rel, raw := range map[string]string{
		"projects/dev-setup/completed/tasks/001-install/plan.md": plan,
		"projects/dev-setup/decisions/a.md":                      dated,
		"agent/memory/semantic/stack-dev-setup.md":               enriched,
	} {
		if err := idx.Upsert(note.Parse(rel, raw, time.Now()), moved, int64(len(raw))); err != nil {
			t.Fatal(err)
		}
	}
	rows, err := idx.NoteRows(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	got := map[string]string{}
	for _, r := range rows {
		got[r.Path] = r.Date
	}
	if d := got["projects/dev-setup/completed/tasks/001-install/plan.md"]; d != "" {
		t.Errorf("the moved plan has no date of its own; got %q from its file", d)
	}
	if d := got["projects/dev-setup/decisions/a.md"]; !strings.HasPrefix(d, "2026-06-26") {
		t.Errorf("the decision keeps its own date; got %q", d)
	}
	if d := got["agent/memory/semantic/stack-dev-setup.md"]; d != "2026-06-19" {
		t.Errorf("an enrichment rewrite does not date the card; got %q", d)
	}
}
