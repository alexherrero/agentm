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
			"---\ntitle: t\nstatus: active\ncaptured: "+n.captured+
				"\n---\n\nSome words about a thing.\n")
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

// The population. Learned from the live corpus after the fact: the window was
// 79% `_inbox` and dreaming's own staging files, and every number the meters
// reported was mostly about them.

// populationVault holds one filed memory and one of everything that looks like
// one without being one.
func populationVault(t *testing.T) *Index {
	t.Helper()
	x, vault := newVaultIndex(t)
	for _, n := range []struct{ rel, status string }{
		{"memory/filed.md", "active"},
		// Statuses that are not the filed live corpus.
		{"memory/raw.md", "unfiled"},
		{"memory/mined.md", "proposed"},
		{"memory/replaced.md", "superseded"},
		{"memory/aged.md", "expired"},
		{"memory/adhoc.md", "research-partial"},
		{"memory/blank.md", ""},
		// `active`, and still not filed memories — this is the half a status
		// filter alone does not catch. Measured live: 765 `_inbox` notes and 263
		// in `_archive` carry `active` from a pass that never reconciled them.
		{"memory/_inbox/clipping.md", "active"},
		{"memory/_archive/old.md", "active"},
		{"desk/scratch/run-1/01-dedup-merge.proposal.md", "active"},
		{"memory/_shelf/parked.md", "active"},
		// `kind:` and no `type:` — the contract's enum does not cover it, and
		// it is a mined supplement awaiting promotion, not a filed memory.
		{"memory/_opinions/good/mined-supplement.md", "active"},
		// Filing-v2 part 3: the lanes live under crystallized/<opinion>/ and
		// stay out; a crystallized memory beside them is filed and live.
		{"memory/crystallized/good/lane-supplement.md", "active"},
		{"memory/crystallized/lesson.md", "active"},
		// A directory whose name merely resembles an excluded one. `_` is a LIKE
		// wildcard, so an unescaped `_inbox` pattern matches this too.
		{"memory/Xinbox/real.md", "active"},
		// The name as a substring rather than a path segment. This repo really
		// does hold notes about the inbox, and a pattern matching the name
		// anywhere in the path would drop them.
		{"memory/notes-on-_inbox-triage.md", "active"},
		{"memory/scratchpad-conventions.md", "active"},
	} {
		writeVaultNote(t, vault, n.rel,
			"---\ntitle: t\nstatus: "+n.status+
				"\ncaptured: 2026-08-01T00:00:00Z\n---\n\nSome words about a thing.\n")
	}
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}
	return x
}

func TestTheMetersMeasureTheFiledLiveCorpusOnly(t *testing.T) {
	x := populationVault(t)
	got := relsOf(t, x, 100, []string{"memory", "desk"}, false)
	want := []string{
		"memory/Xinbox/real.md", "memory/crystallized/lesson.md", "memory/filed.md",
		"memory/notes-on-_inbox-triage.md", "memory/scratchpad-conventions.md",
	}
	if !equalRels(got, want) {
		t.Fatalf("window = %v, want %v", got, want)
	}
}

func TestEveryStatusThatIsNotActiveIsOutOfThePopulation(t *testing.T) {
	x := populationVault(t)
	got := relsOf(t, x, 100, []string{"memory", "desk"}, false)
	for _, rel := range []string{
		"memory/raw.md", "memory/mined.md", "memory/replaced.md",
		"memory/aged.md", "memory/adhoc.md", "memory/blank.md",
	} {
		if contains(got, rel) {
			t.Errorf("%s is in the window and its status is not %q", rel, MeterStatus)
		}
	}
}

func TestAnActiveNoteInAnExcludedDirectoryIsStillExcluded(t *testing.T) {
	x := populationVault(t)
	got := relsOf(t, x, 100, []string{"memory", "desk"}, false)
	// Every one of these is `status: active`, so only the directory rule can
	// drop them. Without it the status filter passes all four.
	for _, rel := range []string{
		"memory/_inbox/clipping.md",
		"memory/_archive/old.md",
		"desk/scratch/run-1/01-dedup-merge.proposal.md",
		"memory/_shelf/parked.md",
		"memory/_opinions/good/mined-supplement.md",
	} {
		if contains(got, rel) {
			t.Errorf("%s is in the window; %v should have dropped it",
				rel, MeterExcludedDirs)
		}
	}
}

func TestACrystallizedLaneIsOutWhileTheClassIsIn(t *testing.T) {
	x := populationVault(t)
	got := relsOf(t, x, 100, []string{"memory", "desk"}, false)
	if contains(got, "memory/crystallized/good/lane-supplement.md") {
		t.Errorf("a lane entry under crystallized/ is in the window; %v should have dropped it",
			MeterExcludedNested)
	}
	if !contains(got, "memory/crystallized/lesson.md") {
		t.Errorf("a crystallized memory is out of the window; only the lanes beneath the class are excluded")
	}
}

func TestTheExclusionMatchesADirectoryNameAndNotAWildcard(t *testing.T) {
	x := populationVault(t)
	got := relsOf(t, x, 100, []string{"memory", "desk"}, false)
	// `_` is a LIKE single-character wildcard. An unescaped `%/_inbox/%` also
	// matches `memory/Xinbox/real.md`, which is an ordinary filed memory.
	if !contains(got, "memory/Xinbox/real.md") {
		t.Fatalf("window = %v; Xinbox is not _inbox and belongs in it", got)
	}
	// And a path segment, not a substring. A note *about* the inbox is a filed
	// memory; `%_inbox%` would drop it along with the directory.
	for _, rel := range []string{
		"memory/notes-on-_inbox-triage.md",
		"memory/scratchpad-conventions.md",
	} {
		if !contains(got, rel) {
			t.Errorf("%s is out of the window; the name appears in the filename, "+
				"not as a directory", rel)
		}
	}
}

func TestTheExcludedNamesAreTheOnesRecallExcludes(t *testing.T) {
	// recall.py excludes `scratch`, `_inbox` and `_archive` by directory name.
	// The meters add `_shelf` and `_opinions`. Asserted as a list rather than inferred from a
	// query, so a name added on one side and not the other is a red test rather
	// than a slow divergence between what is searched and what is measured.
	want := []string{"_inbox", "_archive", "scratch", "_shelf", "_opinions"}
	if len(MeterExcludedDirs) != len(want) {
		t.Fatalf("MeterExcludedDirs = %v, want %v", MeterExcludedDirs, want)
	}
	for i, w := range want {
		if MeterExcludedDirs[i] != w {
			t.Fatalf("MeterExcludedDirs = %v, want %v", MeterExcludedDirs, want)
		}
	}
}

func relsOf(t *testing.T, x *Index, n int, scope []string, withVectors bool) []string {
	t.Helper()
	rows, err := x.RecentForMeters(context.Background(), n, "m", scope, withVectors)
	if err != nil {
		t.Fatal(err)
	}
	out := make([]string, 0, len(rows))
	for _, r := range rows {
		out = append(out, r.Rel)
	}
	return out
}

func contains(hay []string, needle string) bool {
	for _, h := range hay {
		if h == needle {
			return true
		}
	}
	return false
}

func equalRels(got, want []string) bool {
	if len(got) != len(want) {
		return false
	}
	for _, w := range want {
		if !contains(got, w) {
			return false
		}
	}
	return true
}
