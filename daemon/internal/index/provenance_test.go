package index

import (
	"context"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// addProvenanced indexes a memory carrying its full source provenance.
func addProvenanced(tb testing.TB, x *Index, rel, source, hash, version string) {
	tb.Helper()
	n := note.Note{
		Rel: rel, Title: rel, Body: "body of " + rel,
		Source: source, SourceHash: hash, SourceVersion: version,
		Captured:       time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC),
		CapturedSource: "mtime",
	}
	if err := x.Upsert(n, 1, 10); err != nil {
		tb.Fatalf("indexing %s: %v", rel, err)
	}
}

// The rebuild's input. Everything a `Seen` lookup needs, recovered from what the
// index already caches rather than by re-reading a single email.
func TestProvenanceRecoversWhatAMemoryRecords(t *testing.T) {
	ctx := context.Background()
	x := newTestIndex(t)
	addProvenanced(t, x, "a.md", "email:<abc@example.com>", "hash-abc", "ingest/1")
	addProvenanced(t, x, "b.md", "email:<abc@example.com>", "hash-abc", "ingest/1")
	addProvenanced(t, x, "c.md", "url:https://example.com/a", "hash-url", "ingest/1")
	// A note with no source at all is not a source.
	addProvenanced(t, x, "d.md", "", "", "")

	got, err := x.Provenance(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 {
		t.Fatalf("Provenance returned %d rows, want 2: %+v", len(got), got)
	}

	byID := map[string]SourceProvenance{}
	for _, p := range got {
		byID[p.Source] = p
	}
	email := byID["email:<abc@example.com>"]
	if email.Hash != "hash-abc" {
		t.Errorf("hash = %q; without it the rebuild recovers a name and not a "+
			"skip decision", email.Hash)
	}
	if email.Version != "ingest/1" {
		t.Errorf("version = %q", email.Version)
	}
	if email.Memories != 2 {
		t.Errorf("memories = %d, want 2", email.Memories)
	}
}

// The hash comes from the newest memory the source produced.
//
// A source re-ingested at a better version leaves memories from both passes for
// as long as the older ones are superseded rather than deleted, and what the
// registry should report is where that source stands now — not where it stood
// the first time anything read it.
func TestProvenanceReportsWhereASourceStandsNow(t *testing.T) {
	ctx := context.Background()
	x := newTestIndex(t)
	const source = "email:<abc@example.com>"
	// The first pass, then the second. Distinct paths, so both rows survive the
	// way a superseded memory and its replacement do.
	addProvenanced(t, x, "old.md", source, "hash-v1", "ingest/1")
	addProvenanced(t, x, "new.md", source, "hash-v2", "ingest/2")

	got, err := x.Provenance(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("Provenance returned %d rows for one source", len(got))
	}
	if got[0].Version != "ingest/2" || got[0].Hash != "hash-v2" {
		t.Errorf("Provenance reports %s/%s, want the newer pass — the registry "+
			"would think the source still stood where the first read left it",
			got[0].Hash, got[0].Version)
	}
	if got[0].Memories != 2 {
		t.Errorf("memories = %d, want both passes counted", got[0].Memories)
	}
}

// A memory naming its source without saying what that source contained is the
// state the rebuild reports as unrecoverable, so the scan has to surface it
// rather than dropping the row.
func TestProvenanceKeepsASourceWithNoRecordedHash(t *testing.T) {
	ctx := context.Background()
	x := newTestIndex(t)
	addProvenanced(t, x, "a.md", "url:https://example.com/a", "", "")

	got, err := x.Provenance(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("Provenance dropped a source that has memories: %+v", got)
	}
	if got[0].Hash != "" {
		t.Errorf("hash = %q, want empty — a hash nobody recorded must not be "+
			"invented", got[0].Hash)
	}
}
