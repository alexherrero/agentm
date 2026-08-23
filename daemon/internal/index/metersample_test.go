package index

import (
	"context"
	"testing"
)

// The window the meters measure. Two properties matter and both were learned
// from the live corpus rather than reasoned about: the sample has to prefer
// notes the dense arm has reached, and it has to be ordered so that two runs
// describe the same set.

// sampleVault writes notes with staggered capture dates, and embeds only the
// older half — which is the shape the live corpus actually has, because the
// embedder trails capture.
func sampleVault(t *testing.T) *Index {
	t.Helper()
	x, vault := newVaultIndex(t)
	for _, n := range []struct{ rel, captured string }{
		{"memory/old-a.md", "2026-01-01T00:00:00Z"},
		{"memory/old-b.md", "2026-01-02T00:00:00Z"},
		{"memory/new-a.md", "2026-08-01T00:00:00Z"},
		{"memory/new-b.md", "2026-08-02T00:00:00Z"},
	} {
		writeVaultNote(t, vault, n.rel,
			"---\ntitle: t\ncaptured: "+n.captured+"\n---\n\nSome words about a thing.\n")
	}
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}
	embedOnly(t, x, "memory/old-a.md", "memory/old-b.md")
	// A note with a chunk-1 vector and no chunk 0: a long note the embedder
	// split. It must not qualify, because chunk 1 is a fragment and the meters
	// are about whole notes — and without this the fixture never wrote a
	// non-zero chunk, so selecting every chunk looked identical to selecting
	// the first.
	embedChunk(t, x, "memory/new-a.md", 1)
	return x
}

// embedChunk writes a current vector for one chunk of a note.
func embedChunk(t *testing.T, x *Index, rel string, chunk int) {
	t.Helper()
	var id, mtime int64
	if err := x.db.QueryRow(
		`SELECT id, mtime_ns FROM docmeta WHERE path = ?`, rel).
		Scan(&id, &mtime); err != nil {
		t.Fatalf("%s: %v", rel, err)
	}
	if _, err := x.db.Exec(
		`INSERT INTO embeddings (doc_id, chunk_idx, model, dim, mtime_ns, vec)
		 VALUES (?, ?, 'm', 3, ?, ?)`,
		id, chunk, mtime, encodeVec([]float32{0, 1, 0})); err != nil {
		t.Fatal(err)
	}
}

// embedOnly gives exactly these notes a current chunk-0 vector.
func embedOnly(t *testing.T, x *Index, rels ...string) {
	t.Helper()
	for _, rel := range rels {
		var id int64
		var mtime int64
		if err := x.db.QueryRow(
			`SELECT id, mtime_ns FROM docmeta WHERE path = ?`, rel).
			Scan(&id, &mtime); err != nil {
			t.Fatalf("%s: %v", rel, err)
		}
		if _, err := x.db.Exec(
			`INSERT INTO embeddings (doc_id, chunk_idx, model, dim, mtime_ns, vec)
			 VALUES (?, 0, 'm', 3, ?, ?)`,
			id, mtime, encodeVec([]float32{1, 0, 0})); err != nil {
			t.Fatal(err)
		}
	}
}

func rels(rows []MeterSample) []string {
	out := make([]string, len(rows))
	for i, r := range rows {
		out[i] = r.Rel
	}
	return out
}

// The bar, and the reason this parameter exists at all.
//
// On the live corpus the 500 most recently captured notes carried *zero*
// vectors, because the embedder trails capture by one to two thousand notes. A
// window chosen by recency alone therefore made both embedding meters unable to
// run — every night, while reporting it as a missing embedder.
func TestTheWindowPrefersNotesTheDenseArmHasReached(t *testing.T) {
	x := sampleVault(t)
	got, err := x.RecentForMeters(context.Background(), 10, "m",
		[]string{"memory"}, true)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 {
		t.Fatalf("window = %v, want the two whole-note embedded ones", rels(got))
	}
	for _, r := range got {
		if r.Vec == nil {
			t.Errorf("%s came back with no vector from a vectors-only window", r.Rel)
		}
		if r.Rel == "memory/new-a.md" {
			t.Error("a note with only a chunk-1 vector qualified; the meters are " +
				"about whole notes, and a fragment is not one")
		}
	}
}

// And without that restriction the newest notes come back, vectors or not, so a
// corpus that has never been embedded still gets its two lexical meters.
func TestWithoutTheRestrictionTheNewestNotesComeBack(t *testing.T) {
	x := sampleVault(t)
	got, err := x.RecentForMeters(context.Background(), 2, "m",
		[]string{"memory"}, false)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 {
		t.Fatalf("window = %v, want two notes", rels(got))
	}
	for _, r := range got {
		if r.Rel == "memory/old-a.md" || r.Rel == "memory/old-b.md" {
			t.Errorf("window = %v, want the two newest", rels(got))
		}
	}
}

// The window is recent rather than arbitrary: a cap smaller than the corpus
// takes the newest of what qualifies, not the first rows the table offers.
func TestTheWindowTakesTheNewestQualifyingNotes(t *testing.T) {
	x := sampleVault(t)
	got, err := x.RecentForMeters(context.Background(), 1, "m",
		[]string{"memory"}, true)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].Rel != "memory/old-b.md" {
		t.Errorf("window = %v, want the newer of the two embedded notes", rels(got))
	}
}

// Oldest-first, because the lexical meters slide a window along the sample and a
// reader expects a period to read forwards.
func TestTheWindowReadsOldestFirst(t *testing.T) {
	x := sampleVault(t)
	got, err := x.RecentForMeters(context.Background(), 10, "m",
		[]string{"memory"}, true)
	if err != nil {
		t.Fatal(err)
	}
	for i := 1; i < len(got); i++ {
		if got[i-1].Captured > got[i].Captured {
			t.Fatalf("window is not oldest-first: %v", rels(got))
		}
	}
	if got[0].Captured == "" {
		t.Error("no capture date came back, so no window can be reported")
	}
}

// Two runs describe the same set in the same order. The nightly trend compares
// one night's numbers against the last, and a window that reshuffled would move
// every meter for a reason nothing in the corpus caused.
func TestTheWindowIsStableAcrossRuns(t *testing.T) {
	x := sampleVault(t)
	first, err := x.RecentForMeters(context.Background(), 10, "m", []string{"memory"}, true)
	if err != nil {
		t.Fatal(err)
	}
	second, err := x.RecentForMeters(context.Background(), 10, "m", []string{"memory"}, true)
	if err != nil {
		t.Fatal(err)
	}
	a, b := rels(first), rels(second)
	if len(a) != len(b) {
		t.Fatalf("%v then %v", a, b)
	}
	for i := range a {
		if a[i] != b[i] {
			t.Fatalf("the window reshuffled: %v then %v", a, b)
		}
	}
}

// A stale vector — one written against an older version of the note — is not a
// current vector. Measuring a note's drift against the embedding of what it used
// to say is measuring the wrong thing.
func TestAStaleVectorDoesNotQualify(t *testing.T) {
	x := sampleVault(t)
	if _, err := x.db.Exec(
		`UPDATE embeddings SET mtime_ns = mtime_ns + 1`); err != nil {
		t.Fatal(err)
	}
	got, err := x.RecentForMeters(context.Background(), 10, "m",
		[]string{"memory"}, true)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 0 {
		t.Errorf("window = %v; every vector is stale and none should qualify",
			rels(got))
	}
}

// Another model's vectors are not this model's. A mismatch would look like an
// absent embedder rather than the configuration error it is.
func TestAnotherModelsVectorsDoNotQualify(t *testing.T) {
	x := sampleVault(t)
	got, err := x.RecentForMeters(context.Background(), 10, "a-different-model",
		[]string{"memory"}, true)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 0 {
		t.Errorf("window = %v under a model nothing was embedded with", rels(got))
	}
}
