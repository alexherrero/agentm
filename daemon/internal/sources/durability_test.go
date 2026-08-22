package sources

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"
)

func scanOf(rows ...Provenance) CorpusScan {
	return func(context.Context) ([]Provenance, error) { return rows, nil }
}

// The durability bar, read the way it can honestly be read.
//
// Not row equality — first-seen timestamps and the exact clock of an earlier
// pass are gone, and asserting otherwise would be asserting a fiction. What has
// to survive is the decision: every source that was skippable before the table
// was wiped is skippable after it, and every source that was not still is not.
func TestRebuildPreservesEverySkipDecision(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)

	mined := mustID(t, "email:<mined@example.com>")
	minedHash := HashContent("a message worth keeping")
	empty := mustID(t, "email:<empty@example.com>")
	emptyHash := HashContent("a receipt")
	live := mustID(t, "claude-session:01JQ8")
	unread := mustID(t, "email:<unread@example.com>")

	// Three registered sources: one that produced memories, one that produced
	// nothing, and one still being appended to.
	if err := r.Register(ctx, Unit{
		ID: mined, Kind: Immutable, Hash: minedHash, Version: "v1", Yield: 2,
	}); err != nil {
		t.Fatal(err)
	}
	if err := r.Register(ctx, Unit{
		ID: empty, Kind: Immutable, Hash: emptyHash, Version: "v1", Yield: 0,
	}); err != nil {
		t.Fatal(err)
	}
	if err := r.Advance(ctx, live, "msg-95", "v1", 3); err != nil {
		t.Fatal(err)
	}

	type probe struct {
		id      ID
		hash    string
		version string
	}
	probes := []probe{
		{mined, minedHash, "v1"},
		{mined, HashContent("edited since"), "v1"},
		{mined, minedHash, "v2"},
		{empty, emptyHash, "v1"},
		{live, HashContent("the log so far"), "v1"},
		{unread, HashContent("never opened"), "v1"},
	}
	before := make([]bool, len(probes))
	for i, p := range probes {
		got, err := r.Seen(ctx, p.id, p.hash, p.version)
		if err != nil {
			t.Fatal(err)
		}
		before[i] = got
	}
	// The premise, so the equality below cannot pass trivially.
	if !before[0] || !before[3] {
		t.Fatalf("the live registry does not recognise its own writes: %v", before)
	}
	if before[1] || before[2] || before[4] || before[5] {
		t.Fatalf("the live registry recognises something it should not: %v", before)
	}

	cursorBefore, err := r.Cursor(ctx, live)
	if err != nil || cursorBefore != "msg-95" {
		t.Fatalf("cursor before the rebuild is %q (%v)", cursorBefore, err)
	}

	// The committed file carries the two classes a corpus scan cannot reach.
	dir := t.TempDir()
	if _, err := r.SaveSidecar(ctx, dir, time.Now()); err != nil {
		t.Fatal(err)
	}
	side, err := LoadSidecar(dir)
	if err != nil {
		t.Fatal(err)
	}

	// Lose the table, then rebuild from what the corpus and the file can prove.
	// Only the mined source appears in the scan: the other two produced no
	// memory carrying provenance, which is exactly why they are in the file.
	rep, err := r.Rebuild(ctx, scanOf(Provenance{
		Source: mined.String(), Hash: minedHash, Version: "v1", Memories: 2,
	}), side)
	if err != nil {
		t.Fatalf("Rebuild: %v", err)
	}
	if rep.FromCorpus != 1 {
		t.Errorf("recovered %d rows from the corpus, want 1", rep.FromCorpus)
	}
	if rep.FromSidecar != 2 {
		t.Errorf("recovered %d rows from the committed file, want 2 — the "+
			"zero-yield source and the growing cursor", rep.FromSidecar)
	}

	for i, p := range probes {
		got, err := r.Seen(ctx, p.id, p.hash, p.version)
		if err != nil {
			t.Fatal(err)
		}
		if got != before[i] {
			t.Errorf("%s at %s/%s: Seen was %v before the rebuild and %v after — "+
				"the registry lost a decision, not just a row",
				p.id, p.hash[:8], p.version, before[i], got)
		}
	}

	if got, _ := r.Cursor(ctx, live); got != cursorBefore {
		t.Errorf("the growing cursor came back as %q, want %q — its tail would be "+
			"re-read from the beginning", got, cursorBefore)
	}
}

// The zero-yield class specifically. It is the one a corpus scan can never
// reach, and the most expensive kind to forget: a source read and found empty is
// the material least likely to repay a second reading.
func TestAZeroYieldSourceSurvivesOnlyThroughTheCommittedFile(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	empty := mustID(t, "email:<empty@example.com>")
	hash := HashContent("a receipt")
	if err := r.Register(ctx, Unit{
		ID: empty, Kind: Immutable, Hash: hash, Version: "v1", Yield: 0,
	}); err != nil {
		t.Fatal(err)
	}

	// A rebuild with the file: it comes back.
	dir := t.TempDir()
	if _, err := r.SaveSidecar(ctx, dir, time.Now()); err != nil {
		t.Fatal(err)
	}
	side, err := LoadSidecar(dir)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := r.Rebuild(ctx, scanOf(), side); err != nil {
		t.Fatal(err)
	}
	if seen, _ := r.Seen(ctx, empty, hash, "v1"); !seen {
		t.Error("a zero-yield source did not survive a rebuild that had the file")
	}

	// And without it, it does not — which is the whole reason the file exists.
	if _, err := r.Rebuild(ctx, scanOf(), Sidecar{}); err != nil {
		t.Fatal(err)
	}
	if seen, _ := r.Seen(ctx, empty, hash, "v1"); seen {
		t.Error("a zero-yield source survived a rebuild with no file, so this " +
			"test cannot show the file is load-bearing")
	}
}

// A memory that names its source without saying what that source contained is
// found but not skippable, and the rebuild says so by name.
func TestASourceWithNoRecordedHashIsReportedRatherThanGuessed(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	id := mustID(t, "url:https://example.com/a")

	rep, err := r.Rebuild(ctx, scanOf(Provenance{
		Source: id.String(), Hash: "", Version: "v1", Memories: 3,
	}), Sidecar{})
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.Unrecoverable) != 1 || rep.Unrecoverable[0] != id.String() {
		t.Errorf("Unrecoverable = %v, want the one source with no hash",
			rep.Unrecoverable)
	}
	if rep.FromCorpus != 0 {
		t.Errorf("FromCorpus = %d; a row with no hash was written anyway, and it "+
			"would claim a source was skippable when nothing can check it",
			rep.FromCorpus)
	}
}

// A `source:` that is prose rather than an identity is skipped quietly. The
// corpus has 138 of these and a rebuild that listed them every time would bury
// the entries that matter.
func TestUnparseableSourcesAreSkippedQuietly(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	rep, err := r.Rebuild(ctx, scanOf(
		Provenance{Source: "claude.ai conversation, exported manually", Memories: 1},
		Provenance{Source: "idea-incubator:foo (research-pending)", Memories: 1},
	), Sidecar{})
	if err != nil {
		t.Fatal(err)
	}
	if rep.FromCorpus != 0 {
		t.Errorf("FromCorpus = %d; prose was minted into identities", rep.FromCorpus)
	}
	if len(rep.Unrecoverable) != 0 {
		t.Errorf("Unrecoverable = %v; prose is not a source that failed to "+
			"recover, it is not a source", rep.Unrecoverable)
	}
}

// The file wins over the scan. A growing source may also have produced memories,
// and the scan writes it back as immutable with a hash — the wrong shape for a
// log still being appended to, and one that would stop its tail ever being read.
func TestTheCommittedFileWinsOverTheScanForAGrowingSource(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	live := mustID(t, "claude-session:01JQ8")
	if err := r.Advance(ctx, live, "msg-95", "v1", 4); err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	if _, err := r.SaveSidecar(ctx, dir, time.Now()); err != nil {
		t.Fatal(err)
	}
	side, err := LoadSidecar(dir)
	if err != nil {
		t.Fatal(err)
	}

	// The scan sees the memories that log produced and offers it as immutable.
	if _, err := r.Rebuild(ctx, scanOf(Provenance{
		Source: live.String(), Hash: HashContent("the log so far"),
		Version: "v1", Memories: 4,
	}), side); err != nil {
		t.Fatal(err)
	}

	rec, ok, err := r.Lookup(ctx, live)
	if err != nil || !ok {
		t.Fatalf("Lookup: ok=%v err=%v", ok, err)
	}
	if rec.Kind != Growing {
		t.Errorf("the log came back as %s; its tail would never be read", rec.Kind)
	}
	if rec.Cursor != "msg-95" {
		t.Errorf("cursor = %q, want msg-95", rec.Cursor)
	}
}

// Wipe-then-rebuild, not merge. A row nothing supports any more is exactly the
// row that would claim a source had been read when nothing can show it.
func TestRebuildDropsRowsNothingSupports(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	stale := mustID(t, "email:<deleted@example.com>")
	hash := HashContent("its memories were all deleted")
	if err := r.Register(ctx, Unit{
		ID: stale, Kind: Immutable, Hash: hash, Version: "v1", Yield: 2,
	}); err != nil {
		t.Fatal(err)
	}

	rep, err := r.Rebuild(ctx, scanOf(), Sidecar{})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Dropped != 1 {
		t.Errorf("Dropped = %d, want 1", rep.Dropped)
	}
	if seen, _ := r.Seen(ctx, stale, hash, "v1"); seen {
		t.Error("a source nothing in the corpus supports still reads as processed")
	}
}

// A rebuild with no scan is refused rather than wiping the table and reporting
// the empty result as a recovery.
func TestRebuildRefusesWithNoScan(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	id := mustID(t, "email:<a@example.com>")
	hash := HashContent("body")
	if err := r.Register(ctx, Unit{
		ID: id, Kind: Immutable, Hash: hash, Version: "v1", Yield: 1,
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := r.Rebuild(ctx, nil, Sidecar{}); err == nil {
		t.Fatal("a rebuild with no corpus scan proceeded")
	}
	if seen, _ := r.Seen(ctx, id, hash, "v1"); !seen {
		t.Error("the refused rebuild wiped the table anyway")
	}
}

// A scan that fails stops the rebuild before the wipe. The other order would
// leave an empty registry and a failed run, which is the worst of both.
func TestAFailedScanDoesNotWipeTheTable(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	id := mustID(t, "email:<a@example.com>")
	hash := HashContent("body")
	if err := r.Register(ctx, Unit{
		ID: id, Kind: Immutable, Hash: hash, Version: "v1", Yield: 1,
	}); err != nil {
		t.Fatal(err)
	}

	boom := errors.New("the index went away")
	if _, err := r.Rebuild(ctx, func(context.Context) ([]Provenance, error) {
		return nil, boom
	}, Sidecar{}); !errors.Is(err, boom) {
		t.Fatalf("Rebuild error = %v, want the scan's own", err)
	}
	if seen, _ := r.Seen(ctx, id, hash, "v1"); !seen {
		t.Error("a failed scan left the registry wiped")
	}
}

// --- the committed file itself ----------------------------------------------

// It carries only what a scan cannot reach. A mirror of every row would drift
// from the table between writes with no way to tell which was right.
func TestTheCommittedFileHoldsOnlyTheUnrebuildableClasses(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	if err := r.Register(ctx, Unit{
		ID: mustID(t, "email:<mined@example.com>"), Kind: Immutable,
		Hash: HashContent("a"), Version: "v1", Yield: 3,
	}); err != nil {
		t.Fatal(err)
	}
	if err := r.Register(ctx, Unit{
		ID: mustID(t, "email:<empty@example.com>"), Kind: Immutable,
		Hash: HashContent("b"), Version: "v1", Yield: 0,
	}); err != nil {
		t.Fatal(err)
	}
	if err := r.Advance(ctx, mustID(t, "claude-session:01"), "msg-4", "v1", 1); err != nil {
		t.Fatal(err)
	}

	dir := t.TempDir()
	side, err := r.SaveSidecar(ctx, dir, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if len(side.ZeroYield) != 1 {
		t.Errorf("the file holds %d zero-yield records, want 1", len(side.ZeroYield))
	}
	if len(side.Cursors) != 1 {
		t.Errorf("the file holds %d cursors, want 1", len(side.Cursors))
	}
	blob, err := os.ReadFile(SidecarPath(dir))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(blob), "mined@example.com") {
		t.Errorf("the file mirrors a source the corpus can prove:\n%s", blob)
	}
}

// It says a machine wrote it. This is committed to the vault's history alongside
// the operator's own notes, and a machine-written file that does not say so is
// one somebody will eventually hand-edit.
func TestTheCommittedFileCarriesItsAttribution(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	dir := t.TempDir()
	at := time.Date(2026, 8, 21, 12, 0, 0, 0, time.UTC)
	if _, err := r.SaveSidecar(ctx, dir, at); err != nil {
		t.Fatal(err)
	}
	var side Sidecar
	blob, err := os.ReadFile(SidecarPath(dir))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(blob, &side); err != nil {
		t.Fatal(err)
	}
	if side.WrittenBy != "agentmd" {
		t.Errorf("WrittenBy = %q", side.WrittenBy)
	}
	if !side.WrittenAt.Equal(at) {
		t.Errorf("WrittenAt = %s, want %s", side.WrittenAt, at)
	}
	if side.Note == "" {
		t.Error("the file says nothing about what it is to somebody who opens it")
	}
}

// Two writes over an unchanged registry produce an identical file. This is
// committed, and a file that reordered itself every night would put a diff in
// the history every night that said nothing.
func TestTheCommittedFileIsStableAcrossWrites(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	for _, raw := range []string{
		"email:<z@example.com>", "email:<a@example.com>", "email:<m@example.com>",
	} {
		if err := r.Register(ctx, Unit{
			ID: mustID(t, raw), Kind: Immutable, Hash: HashContent(raw),
			Version: "v1", Yield: 0,
		}); err != nil {
			t.Fatal(err)
		}
	}
	at := time.Date(2026, 8, 21, 12, 0, 0, 0, time.UTC)
	dir := t.TempDir()

	// Inserted z, a, m — so rowid order and id order differ, and a listing that
	// dropped its ORDER BY would come back in insertion order.
	side, err := r.SaveSidecar(ctx, dir, at)
	if err != nil {
		t.Fatal(err)
	}
	var ids []string
	for _, rec := range side.ZeroYield {
		ids = append(ids, rec.ID.String())
	}
	if !sort.StringsAreSorted(ids) {
		t.Errorf("the committed file lists %v; unsorted, it puts a diff in the "+
			"vault's history every night that says nothing", ids)
	}

	var first []byte
	for i := 0; i < 5; i++ {
		if _, err := r.SaveSidecar(ctx, dir, at); err != nil {
			t.Fatal(err)
		}
		blob, err := os.ReadFile(SidecarPath(dir))
		if err != nil {
			t.Fatal(err)
		}
		if i == 0 {
			first = blob
			continue
		}
		if string(blob) != string(first) {
			t.Fatalf("write %d differs from the first:\n%s\n---\n%s", i, first, blob)
		}
	}
}

// A missing file is an empty sidecar. A vault that has never ingested anything
// has none, and refusing to rebuild without one would make the first rebuild
// impossible.
func TestAMissingCommittedFileIsNotAnError(t *testing.T) {
	side, err := LoadSidecar(t.TempDir())
	if err != nil {
		t.Fatalf("LoadSidecar on a fresh vault: %v", err)
	}
	if len(side.ZeroYield) != 0 || len(side.Cursors) != 0 {
		t.Errorf("an absent file produced %+v", side)
	}
}

// A corrupt one is. It is the only copy of what a scan cannot recover, so a
// rebuild stops rather than quietly proceeding without it and reporting a
// recovery that silently dropped half the registry.
func TestACorruptCommittedFileStopsTheRebuild(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(SidecarPath(dir), []byte("{not json"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadSidecar(dir); err == nil {
		t.Error("a corrupt committed file was read as an empty one, which would " +
			"drop every zero-yield record and every cursor without saying so")
	}
}

// The file is written whole. A half-written record of what has already been read
// would silently exempt a source nobody actually looked at.
func TestTheCommittedFileIsWrittenAtomically(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	dir := t.TempDir()
	if _, err := r.SaveSidecar(ctx, dir, time.Now()); err != nil {
		t.Fatal(err)
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if filepath.Ext(e.Name()) == ".tmp" {
			t.Errorf("a temporary file was left behind: %s", e.Name())
		}
	}
}
