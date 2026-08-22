package index

import (
	"context"
	"reflect"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// addSourced indexes a note carrying the source it was distilled from.
func addSourced(tb testing.TB, x *Index, rel, source string) {
	tb.Helper()
	n := note.Note{
		Rel: rel, Title: rel, Body: "body of " + rel, Source: source,
		Captured:       time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC),
		CapturedSource: "mtime",
	}
	if err := x.Upsert(n, 1, 10); err != nil {
		tb.Fatalf("indexing %s: %v", rel, err)
	}
}

// The lookup that makes re-ingestion source-scoped: which memories did this unit
// produce?
func TestBySourceFindsExactlyWhatOneSourceProduced(t *testing.T) {
	ctx := context.Background()
	x := newTestIndex(t)
	addSourced(t, x, "b.md", "email:<abc@example.com>")
	addSourced(t, x, "a.md", "email:<abc@example.com>")
	addSourced(t, x, "c.md", "email:<other@example.com>")
	addSourced(t, x, "d.md", "")

	got, err := x.BySource(ctx, "email:<abc@example.com>")
	if err != nil {
		t.Fatal(err)
	}
	// Ordered by path, so two runs over an unchanged corpus agree.
	want := []string{"a.md", "b.md"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("BySource = %v, want %v", got, want)
	}
}

// The match is exact. A prefix or substring match over a URL identity would
// sweep in every deeper path on the same host — the direction of error that
// supersedes memories a re-ingest never touched.
func TestBySourceMatchesExactlyRatherThanByPrefix(t *testing.T) {
	ctx := context.Background()
	x := newTestIndex(t)
	addSourced(t, x, "root.md", "url:https://example.com/")
	addSourced(t, x, "deep.md", "url:https://example.com/a/deep-article")

	got, err := x.BySource(ctx, "url:https://example.com/")
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, []string{"root.md"}) {
		t.Errorf("BySource = %v; a prefix match would supersede the deeper "+
			"article too", got)
	}
}

// An empty source matches every note that has none, which is most of the corpus.
// That is not a scope any supersession should have.
func TestBySourceRefusesAnEmptySource(t *testing.T) {
	x := newTestIndex(t)
	addSourced(t, x, "a.md", "")
	if _, err := x.BySource(context.Background(), ""); err == nil {
		t.Error("an empty source was accepted as a supersession scope")
	}
}

// A source nothing produced is an empty answer rather than an error — an
// ordinary outcome for a unit being mined for the first time.
func TestBySourceReturnsNothingForAnUnknownSource(t *testing.T) {
	x := newTestIndex(t)
	addSourced(t, x, "a.md", "email:<abc@example.com>")
	got, err := x.BySource(context.Background(), "email:<never-seen@example.com>")
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 0 {
		t.Errorf("BySource on an unknown source returned %v", got)
	}
}

// The rebuild path's input: every distinct source the corpus records, with how
// many memories each produced.
func TestSourcesCountsWhatTheCorpusRecords(t *testing.T) {
	ctx := context.Background()
	x := newTestIndex(t)
	addSourced(t, x, "a.md", "email:<abc@example.com>")
	addSourced(t, x, "b.md", "email:<abc@example.com>")
	addSourced(t, x, "c.md", "url:https://example.com/a")
	addSourced(t, x, "d.md", "")

	got, err := x.Sources(ctx)
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]int{
		"email:<abc@example.com>":   2,
		"url:https://example.com/a": 1,
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("Sources = %v, want %v — a note with no source is not a source",
			got, want)
	}
}

// A note whose source changes stops being attributed to the old one. Without
// this a re-filed memory would be superseded by a re-ingest of material it no
// longer came from.
func TestChangingANotesSourceMovesIt(t *testing.T) {
	ctx := context.Background()
	x := newTestIndex(t)
	addSourced(t, x, "a.md", "email:<first@example.com>")
	addSourced(t, x, "a.md", "email:<second@example.com>")

	stale, err := x.BySource(ctx, "email:<first@example.com>")
	if err != nil {
		t.Fatal(err)
	}
	if len(stale) != 0 {
		t.Errorf("the note is still attributed to its old source: %v", stale)
	}
	current, err := x.BySource(ctx, "email:<second@example.com>")
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(current, []string{"a.md"}) {
		t.Errorf("BySource on the new source = %v", current)
	}
}
