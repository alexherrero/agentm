package sources

import (
	"context"
	"database/sql"
	"path/filepath"
	"testing"
	"time"

	_ "modernc.org/sqlite"
)

func newRegistry(t *testing.T) *Registry {
	t.Helper()
	dsn := "file:" + filepath.Join(t.TempDir(), "index.db") +
		"?_pragma=journal_mode(WAL)&_pragma=busy_timeout(10000)"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		t.Fatalf("opening the test database: %v", err)
	}
	db.SetMaxOpenConns(1)
	t.Cleanup(func() { db.Close() })
	r, err := Open(db)
	if err != nil {
		t.Fatalf("opening the registry: %v", err)
	}
	return r
}

func mustID(t *testing.T, raw string) ID {
	t.Helper()
	id, err := ParseID(raw)
	if err != nil {
		t.Fatalf("ParseID(%q): %v", raw, err)
	}
	return id
}

// The lookup that saves the money: an identity already registered at its current
// hash is skipped without a model call.
func TestAnImmutableSourceAtItsCurrentHashIsSeen(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	id := mustID(t, "email:<abc@example.com>")
	hash := HashContent("the message body")

	seen, err := r.Seen(ctx, id, hash, "v1")
	if err != nil {
		t.Fatal(err)
	}
	if seen {
		t.Fatal("a source nothing has registered was reported as processed")
	}

	if err := r.Register(ctx, Unit{
		ID: id, Kind: Immutable, Hash: hash, Version: "v1", Yield: 3,
	}); err != nil {
		t.Fatal(err)
	}
	if seen, _ = r.Seen(ctx, id, hash, "v1"); !seen {
		t.Error("a registered source at its own hash was not recognised, so it " +
			"would be mined again at full price on every sweep")
	}
}

// Both halves of the key matter. Content, so an edited page is re-read; version,
// so a better pass can be run over material it has already seen.
func TestSeenIsKeyedOnContentAndVersionTogether(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	id := mustID(t, "url:https://example.com/a")
	hash := HashContent("first fetch")
	if err := r.Register(ctx, Unit{
		ID: id, Kind: Immutable, Hash: hash, Version: "v1", Yield: 1,
	}); err != nil {
		t.Fatal(err)
	}

	for name, tc := range map[string]struct{ hash, version string }{
		"the page changed":  {HashContent("second fetch"), "v1"},
		"the pass improved": {hash, "v2"},
		"both":              {HashContent("second fetch"), "v2"},
	} {
		seen, err := r.Seen(ctx, id, tc.hash, tc.version)
		if err != nil {
			t.Fatal(err)
		}
		if seen {
			t.Errorf("%s: the source still reads as processed, so the better "+
				"reading never happens", name)
		}
	}
}

// A growing source is never "seen". A live log always has a possible new tail,
// and the question to ask of one is where its cursor is.
func TestAGrowingSourceIsNeverSeen(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	id := mustID(t, "claude-session:01JQ8")
	if err := r.Register(ctx, Unit{
		ID: id, Kind: Growing, Cursor: "msg-40", Version: "v1", Yield: 2,
	}); err != nil {
		t.Fatal(err)
	}
	// Even handed a hash, which a caller might compute out of habit.
	seen, err := r.Seen(ctx, id, HashContent("the log so far"), "v1")
	if err != nil {
		t.Fatal(err)
	}
	if seen {
		t.Error("a growing source reported as finished; its new tail would never " +
			"be read")
	}
}

// A growing source is consumed from its cursor, and each sweep picks up exactly
// the new tail.
func TestAGrowingSourceIsConsumedFromItsCursor(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	id := mustID(t, "claude-session:01JQ8")

	start, err := r.Cursor(ctx, id)
	if err != nil {
		t.Fatal(err)
	}
	if start != "" {
		t.Errorf("an unknown source starts at %q, want the beginning", start)
	}

	if err := r.Advance(ctx, id, "msg-40", "v1", 2); err != nil {
		t.Fatal(err)
	}
	if got, _ := r.Cursor(ctx, id); got != "msg-40" {
		t.Errorf("cursor = %q after the first sweep, want msg-40", got)
	}

	if err := r.Advance(ctx, id, "msg-95", "v1", 3); err != nil {
		t.Fatal(err)
	}
	if got, _ := r.Cursor(ctx, id); got != "msg-95" {
		t.Errorf("cursor = %q after the second sweep, want msg-95", got)
	}

	// The yield accumulates rather than being replaced. A transcript mined
	// across forty sweeps whose yield reported only the last tail would say a
	// long session produced almost nothing.
	rec, ok, err := r.Lookup(ctx, id)
	if err != nil || !ok {
		t.Fatalf("Lookup: ok=%v err=%v", ok, err)
	}
	if rec.Yield != 5 {
		t.Errorf("Yield = %d after sweeps of 2 and 3, want 5", rec.Yield)
	}
}

// A cursor belongs to the growing shape. Asking an immutable source where its
// cursor is must not hand back a stale one from some other row.
func TestAnImmutableSourceHasNoCursor(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	id := mustID(t, "email:<abc@example.com>")
	// A cursor is set on it deliberately. With the field left empty a lookup
	// that ignored the kind would return the same empty string as one that
	// respected it, and the test could not tell them apart.
	if err := r.Register(ctx, Unit{
		ID: id, Kind: Immutable, Hash: HashContent("body"), Version: "v1",
		Cursor: "not-a-cursor-anybody-should-read",
	}); err != nil {
		t.Fatal(err)
	}
	if got, _ := r.Cursor(ctx, id); got != "" {
		t.Errorf("an immutable source reports a cursor of %q; a growing sweep "+
			"would resume from a position that means nothing", got)
	}
}

// The kind is required, because guessing it wrong is expensive in both
// directions and silent in both.
func TestRegisterRequiresAKindAndItsWatermark(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	id := mustID(t, "email:<abc@example.com>")

	for name, u := range map[string]Unit{
		"no kind":            {ID: id, Hash: HashContent("b"), Version: "v1"},
		"immutable, no hash": {ID: id, Kind: Immutable, Version: "v1"},
		"growing, no cursor": {ID: id, Kind: Growing, Version: "v1"},
		"no identity":        {Kind: Immutable, Hash: "h", Version: "v1"},
		"unknown namespace":  {ID: ID{"mastodon", "12"}, Kind: Immutable, Hash: "h"},
	} {
		if err := r.Register(ctx, u); err == nil {
			t.Errorf("a unit with %s was accepted", name)
		}
	}
}

// Re-registering keeps the first sighting and moves the last. When a source was
// first read is a fact about the corpus; when it was last touched is a fact
// about the pass.
func TestReRegisteringKeepsTheFirstSighting(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	clock := time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC)
	r.SetClock(func() time.Time { return clock })
	id := mustID(t, "url:https://example.com/a")

	if err := r.Register(ctx, Unit{
		ID: id, Kind: Immutable, Hash: HashContent("v1 content"), Version: "v1",
	}); err != nil {
		t.Fatal(err)
	}
	first := clock

	clock = clock.Add(72 * time.Hour)
	if err := r.Register(ctx, Unit{
		ID: id, Kind: Immutable, Hash: HashContent("v2 content"), Version: "v2",
	}); err != nil {
		t.Fatal(err)
	}

	rec, ok, err := r.Lookup(ctx, id)
	if err != nil || !ok {
		t.Fatalf("Lookup: ok=%v err=%v", ok, err)
	}
	if !rec.FirstSeen.Equal(first) {
		t.Errorf("FirstSeen = %s, want the original %s", rec.FirstSeen, first)
	}
	if !rec.LastSeen.Equal(clock) {
		t.Errorf("LastSeen = %s, want %s", rec.LastSeen, clock)
	}
	if rec.Version != "v2" {
		t.Errorf("Version = %q after a re-read at v2", rec.Version)
	}
}

// One row per source, whatever happens to it.
func TestReRegisteringDoesNotDuplicate(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	id := mustID(t, "url:https://example.com/a")
	for i := 0; i < 4; i++ {
		if err := r.Register(ctx, Unit{
			ID: id, Kind: Immutable, Hash: HashContent("body"), Version: "v1",
		}); err != nil {
			t.Fatal(err)
		}
	}
	all, err := r.All(ctx, "", 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(all) != 1 {
		t.Errorf("four registrations of one source left %d rows", len(all))
	}
}

// A source read and found to contain nothing is the one class a corpus rebuild
// can never recover — there is no memory carrying its id, because it produced
// none. Without the record it looks exactly like a source nobody has looked at.
func TestZeroYieldSourcesAreDistinguishable(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	empty := mustID(t, "email:<nothing@example.com>")
	useful := mustID(t, "email:<something@example.com>")

	if err := r.Register(ctx, Unit{
		ID: empty, Kind: Immutable, Hash: HashContent("a receipt"), Version: "v1", Yield: 0,
	}); err != nil {
		t.Fatal(err)
	}
	if err := r.Register(ctx, Unit{
		ID: useful, Kind: Immutable, Hash: HashContent("a design decision"),
		Version: "v1", Yield: 2,
	}); err != nil {
		t.Fatal(err)
	}

	zero, err := r.ZeroYield(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(zero) != 1 || zero[0].ID != empty {
		t.Fatalf("zero-yield records are %+v, want only %s", zero, empty)
	}
	if !zero[0].ZeroYield() {
		t.Error("a zero-yield record does not report itself as one")
	}

	// And it is still skipped, which is the point of recording it: a source that
	// yielded nothing is exactly the material least worth reading twice.
	seen, err := r.Seen(ctx, empty, HashContent("a receipt"), "v1")
	if err != nil {
		t.Fatal(err)
	}
	if !seen {
		t.Error("a source read and found empty was not recognised, so it would " +
			"be read again on every sweep")
	}
}

// Every field survives the round trip.
func TestLookupRoundTripsTheRecord(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	at := time.Date(2026, 8, 1, 12, 0, 0, 0, time.UTC)
	r.SetClock(func() time.Time { return at })
	id := mustID(t, "url:https://example.com/a?b=1")
	hash := HashContent("body")

	if err := r.Register(ctx, Unit{
		ID: id, Kind: Immutable, Hash: hash, Version: "v1", Yield: 7,
	}); err != nil {
		t.Fatal(err)
	}
	rec, ok, err := r.Lookup(ctx, id)
	if err != nil || !ok {
		t.Fatalf("Lookup: ok=%v err=%v", ok, err)
	}
	want := Record{
		ID: id, Kind: Immutable, Hash: hash, Version: "v1", Yield: 7,
		FirstSeen: at, LastSeen: at,
	}
	if rec != want {
		t.Errorf("round trip changed the record:\n got %+v\nwant %+v", rec, want)
	}

	if _, ok, err := r.Lookup(ctx, mustID(t, "email:<missing@example.com>")); ok || err != nil {
		t.Errorf("Lookup of an absent source: ok=%v err=%v", ok, err)
	}
}

// An identity whose reference contains a colon comes back whole. Every URL does.
func TestAnIdentityWithColonsRoundTrips(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	id := mustID(t, "url:https://example.com:8443/a")
	if err := r.Register(ctx, Unit{
		ID: id, Kind: Immutable, Hash: HashContent("b"), Version: "v1",
	}); err != nil {
		t.Fatal(err)
	}
	rec, ok, err := r.Lookup(ctx, id)
	if err != nil || !ok {
		t.Fatalf("Lookup: ok=%v err=%v", ok, err)
	}
	if rec.ID != id {
		t.Errorf("identity came back as %+v, want %+v", rec.ID, id)
	}
}

func TestAllFiltersByNamespace(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	for _, raw := range []string{
		"email:<a@example.com>", "email:<b@example.com>", "url:https://example.com/a",
	} {
		id := mustID(t, raw)
		if err := r.Register(ctx, Unit{
			ID: id, Kind: Immutable, Hash: HashContent(raw), Version: "v1",
		}); err != nil {
			t.Fatal(err)
		}
	}
	mail, err := r.All(ctx, Email, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(mail) != 2 {
		t.Errorf("the email namespace holds %d rows, want 2", len(mail))
	}
	for _, rec := range mail {
		if rec.ID.Namespace != Email {
			t.Errorf("a %s row came back from an email filter", rec.ID.Namespace)
		}
	}
}

func TestForgetAndDrop(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	id := mustID(t, "email:<a@example.com>")
	hash := HashContent("body")
	if err := r.Register(ctx, Unit{
		ID: id, Kind: Immutable, Hash: hash, Version: "v1",
	}); err != nil {
		t.Fatal(err)
	}
	if err := r.Forget(ctx, id); err != nil {
		t.Fatal(err)
	}
	if seen, _ := r.Seen(ctx, id, hash, "v1"); seen {
		t.Error("a forgotten source still reads as processed")
	}

	if err := r.Register(ctx, Unit{
		ID: id, Kind: Immutable, Hash: hash, Version: "v1",
	}); err != nil {
		t.Fatal(err)
	}
	n, err := r.Drop(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Errorf("Drop removed %d rows, want 1", n)
	}
}

func TestCountSummarizesTheRegistry(t *testing.T) {
	ctx := context.Background()
	r := newRegistry(t)
	if err := r.Register(ctx, Unit{
		ID: mustID(t, "email:<a@example.com>"), Kind: Immutable,
		Hash: HashContent("a"), Version: "v1", Yield: 3,
	}); err != nil {
		t.Fatal(err)
	}
	if err := r.Register(ctx, Unit{
		ID: mustID(t, "email:<b@example.com>"), Kind: Immutable,
		Hash: HashContent("b"), Version: "v1", Yield: 0,
	}); err != nil {
		t.Fatal(err)
	}
	if err := r.Advance(ctx, mustID(t, "claude-session:01"), "msg-4", "v1", 2); err != nil {
		t.Fatal(err)
	}

	s, err := r.Count(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if s.Total != 3 {
		t.Errorf("Total = %d, want 3", s.Total)
	}
	if s.ByKind[Immutable] != 2 || s.ByKind[Growing] != 1 {
		t.Errorf("ByKind = %v", s.ByKind)
	}
	if s.Memories != 5 {
		t.Errorf("Memories = %d, want 5", s.Memories)
	}
	if s.ZeroYield != 1 {
		t.Errorf("ZeroYield = %d, want 1", s.ZeroYield)
	}
}

func TestOpenRefusesANilHandle(t *testing.T) {
	if _, err := Open(nil); err == nil {
		t.Error("Open(nil) returned a registry")
	}
}

func TestAdvanceRefusesAnEmptyCursor(t *testing.T) {
	if err := newRegistry(t).Advance(context.Background(),
		mustID(t, "claude-session:01"), "", "v1", 0); err == nil {
		t.Error("a growing source was advanced to nowhere")
	}
}
