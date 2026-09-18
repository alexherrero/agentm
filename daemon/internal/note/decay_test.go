package note

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

// The floor is the property that distinguishes this curve from the one it
// replaces, and the design says so in the strongest terms it uses about ranking:
// "the sixteenth is a floor, not a waypoint." A memory nobody has needed in four
// years is cold rather than worthless, and a floorless curve makes it
// unreachable rather than merely unlikely.
func TestTheCurveNeverReachesZero(t *testing.T) {
	for _, days := range []float64{1825, 1826, 3650, 36500, 365000} {
		if got := DecayScore(days); got < DecayFloor {
			t.Errorf("%v days scores %v, below the %v floor", days, got, DecayFloor)
		}
		if got := DecayScore(days); got <= 0 {
			t.Errorf("%v days scores %v — the curve reached zero", days, got)
		}
	}
}

func TestTheBands(t *testing.T) {
	for _, tc := range []struct {
		days float64
		want float64
	}{
		{0, 1.0},
		{181, 1.0},
		{182, 1.0},
		{183, 0.5},
		{365, 0.5},
		{366, 0.125},
		{1095, 0.125},
		{1096, 0.0625},
		{1825, 0.0625},
		{1826, DecayFloor},
	} {
		if got := DecayScore(tc.days); got != tc.want {
			t.Errorf("%v days scored %v, want %v", tc.days, got, tc.want)
		}
	}
}

// A clock that went backwards — a timezone change, an NTP correction, an mtime
// from the future on a synced mount — must not read as the far side of the curve.
func TestANegativeAgeReadsAsFresh(t *testing.T) {
	if got := DecayScore(-30); got != 1.0 {
		t.Errorf("a negative age scored %v, want 1.0", got)
	}
}

func writeSidecar(t *testing.T, vault string, entries map[string]string) {
	t.Helper()
	var b []byte
	b = append(b, []byte(`{"version":1,"entries":{`)...)
	first := true
	for slug, date := range entries {
		if !first {
			b = append(b, ',')
		}
		first = false
		b = append(b, []byte(`"`+slug+`":{"last_access":"`+date+`"}`)...)
	}
	b = append(b, []byte(`}}`)...)
	if err := os.WriteFile(filepath.Join(vault, ".lifecycle.json"), b, 0o644); err != nil {
		t.Fatal(err)
	}
}

// Only a genuine recall resets the clock. The access record is what carries
// that, and it has to win over the frontmatter dates — otherwise a note read
// yesterday still ranks as though it were last touched years ago.
func TestAGenuineRecallWinsOverTheFrontmatterDates(t *testing.T) {
	vault := t.TempDir()
	writeSidecar(t, vault, map[string]string{"read-recently": "2026-08-01"})
	log := NewAccessLog(vault)
	now := time.Date(2026, 8, 20, 0, 0, 0, 0, time.UTC)

	days, ok := ElapsedDays(log, "read-recently", "read-recently", "2016-01-01", "", "2015-01-01",
		"frontmatter:captured", now)
	if !ok {
		t.Fatal("no anchor resolved")
	}
	if days > 30 {
		t.Errorf("elapsed %v days — the ten-year-old frontmatter beat the recent recall", days)
	}
	if DecayScore(days) != 1.0 {
		t.Errorf("a note recalled three weeks ago scored %v", DecayScore(days))
	}
}

// `updated`, not `captured`. An entry substantively edited today is fresh
// regardless of when it was first written; anchoring to `captured` would
// penalize a frequently-maintained reference for staleness it does not have.
func TestUpdatedIsThePreferredFallback(t *testing.T) {
	vault := t.TempDir()
	log := NewAccessLog(vault)
	now := time.Date(2026, 8, 20, 0, 0, 0, 0, time.UTC)

	days, ok := ElapsedDays(log, "no-record", "no-record", "2026-08-19", "", "2015-01-01",
		"frontmatter:captured", now)
	if !ok {
		t.Fatal("no anchor resolved")
	}
	if days > 2 {
		t.Errorf("elapsed %v days — `captured` beat `updated`", days)
	}
}

func TestCapturedIsTheLastResort(t *testing.T) {
	vault := t.TempDir()
	log := NewAccessLog(vault)
	now := time.Date(2026, 8, 20, 0, 0, 0, 0, time.UTC)

	days, ok := ElapsedDays(log, "no-record", "no-record", "", "", "2026-08-19T05:00:00Z",
		"frontmatter:captured", now)
	if !ok {
		t.Fatal("no anchor resolved from `captured`")
	}
	if days > 2 {
		t.Errorf("elapsed %v days from a one-day-old capture", days)
	}
}

// No anchor is no basis to decay, not maximum age. A note the system knows
// nothing about should not be buried for it.
func TestNoAnchorIsNoBasisToDecay(t *testing.T) {
	vault := t.TempDir()
	log := NewAccessLog(vault)
	if _, ok := ElapsedDays(log, "unknown", "unknown", "", "", "", "mtime", time.Now()); ok {
		t.Error("an anchor resolved from nothing")
	}
}

func TestBothTimestampShapesParse(t *testing.T) {
	vault := t.TempDir()
	log := NewAccessLog(vault)
	now := time.Date(2026, 8, 20, 0, 0, 0, 0, time.UTC)
	for _, stamp := range []string{"2026-08-19", "2026-08-19T05:43:48Z", "2026-08-19T05:43:48+00:00"} {
		days, ok := ElapsedDays(log, "x", "x", stamp, "", "", "mtime", now)
		if !ok {
			t.Errorf("%q did not parse", stamp)
			continue
		}
		if days > 2 {
			t.Errorf("%q resolved to %v days", stamp, days)
		}
	}
}

// A corrupt or missing sidecar costs every note its access anchor and nothing
// more. A ranking pass that refused to run because a cache was unreadable would
// trade a small inaccuracy for no answer at all.
func TestACorruptSidecarIsAnEmptyLogNotAnError(t *testing.T) {
	vault := t.TempDir()
	if err := os.WriteFile(filepath.Join(vault, ".lifecycle.json"),
		[]byte("{not json"), 0o644); err != nil {
		t.Fatal(err)
	}
	log := NewAccessLog(vault)
	if log.Len() != 0 {
		t.Errorf("a corrupt sidecar produced %d entries", log.Len())
	}
	// And the fallback chain still works.
	now := time.Date(2026, 8, 20, 0, 0, 0, 0, time.UTC)
	if _, ok := ElapsedDays(log, "x", "x", "2026-08-19", "", "", "mtime", now); !ok {
		t.Error("a corrupt sidecar broke the frontmatter fallback")
	}
}

func TestAMissingSidecarIsAnEmptyLog(t *testing.T) {
	log := NewAccessLog(t.TempDir())
	if log.Len() != 0 {
		t.Errorf("a missing sidecar produced %d entries", log.Len())
	}
}

func TestRefreshPicksUpAChangedSidecar(t *testing.T) {
	vault := t.TempDir()
	writeSidecar(t, vault, map[string]string{"a": "2026-01-01"})
	log := NewAccessLog(vault)
	if log.Len() != 1 {
		t.Fatalf("initial load has %d entries", log.Len())
	}

	// A later mtime, so the refresh has something to notice.
	time.Sleep(10 * time.Millisecond)
	writeSidecar(t, vault, map[string]string{"a": "2026-01-01", "b": "2026-02-01"})
	log.Refresh()
	if log.Len() != 2 {
		t.Errorf("after refresh there are %d entries, want 2", log.Len())
	}
}

// With no contract behind it the curve is the one this package carried as
// constants, so a vault whose contract predates the `decay_*` lines ranks
// exactly as it did.
//
// This used to be "the Go bands match the ported Python values", two hand-kept
// copies pinned against each other. They are now one curve, read from the
// contract by both arms, and the cross-arm agreement is proven where it can
// actually fail — scripts/test_decay_curve_parity.py runs the shipped binary
// and the Python function over the same days.
func TestTheDefaultCurveIsTheOneThisPackageShipped(t *testing.T) {
	t.Cleanup(func() { curve.Store(nil) })
	curve.Store(nil)

	want := []Band{{182, 1.0}, {365, 0.5}, {1095, 0.125}, {1825, 0.0625}}
	got := DecayBands()
	if len(got) != len(want) {
		t.Fatalf("%d bands, want %d", len(got), len(want))
	}
	for i, w := range want {
		if got[i] != w {
			t.Errorf("band %d is (%v, %v), want (%v, %v)", i, got[i].Days, got[i].Score, w.Days, w.Score)
		}
	}
}

// The contract's numbers, not the package's.
func TestTheCurveComesFromTheContract(t *testing.T) {
	t.Cleanup(func() { curve.Store(nil) })
	SetDecayBands(180, 365, 1095, 1825, 0.0625)

	cases := []struct {
		days float64
		want float64
	}{
		{0, 1.0},
		{179, 1.0},
		{180, 1.0},
		// 181 is the whole point of reading the contract: the constant said 182,
		// the contract says 180, and this is the day they disagree on.
		{181, 0.5},
		{365, 0.5},
		{366, 0.125},
		{1095, 0.125},
		{1096, 0.0625},
		{1825, 0.0625},
		{100000, 0.0625},
		{-5, 1.0},
	}
	for _, c := range cases {
		if got := DecayScore(c.days); got != c.want {
			t.Errorf("DecayScore(%v) = %v, want %v", c.days, got, c.want)
		}
	}
}

// A curve read half from the contract and half from a constant is the drift
// this change exists to end, and it would be invisible. A contract that
// describes no ascending curve falls the whole thing back to the default.
func TestAnIncoherentContractFallsBackWholesale(t *testing.T) {
	t.Cleanup(func() { curve.Store(nil) })
	for _, bad := range [][5]float64{
		{0, 0, 0, 0, 0},                // names none
		{180, 100, 1095, 1825, 0.0625}, // half before full
		{180, 365, 1095, 0, 0.0625},    // floor day missing
	} {
		SetDecayBands(bad[0], bad[1], bad[2], bad[3], bad[4])
		got := DecayBands()
		if len(got) != 4 || got[0].Days != 182 {
			t.Errorf("SetDecayBands%v left the curve at %v, want the package default", bad, got)
		}
	}
}

// The floor weight is the contract's too, and zero means it named none.
func TestTheFloorWeightComesFromTheContract(t *testing.T) {
	t.Cleanup(func() { curve.Store(nil) })
	SetDecayBands(180, 365, 1095, 1825, 0.02)
	if got := DecayScore(5000); got != 0.02 {
		t.Errorf("DecayScore past the last band = %v, want the contract's 0.02", got)
	}
	SetDecayBands(180, 365, 1095, 1825, 0)
	if got := DecayScore(5000); got != DecayFloor {
		t.Errorf("DecayScore with no floor weight = %v, want the package floor %v", got, DecayFloor)
	}
}

// mtime is not an anchor.
//
// `captured` falls back to the filesystem when the note carries no date, and on
// this corpus that fallback is actively misleading: the type-collapse migration
// rewrote 9,899 notes' frontmatter in an afternoon, so an mtime-anchored curve
// would read the whole migrated corpus as brand new and the few files it skipped
// as uniquely ancient — an ordering with no relationship to how memory is used.
func TestMtimeIsNotAnAnchor(t *testing.T) {
	vault := t.TempDir()
	log := NewAccessLog(vault)
	now := time.Date(2026, 8, 20, 0, 0, 0, 0, time.UTC)

	if _, ok := ElapsedDays(log, "no-record", "no-record", "", "", "2015-01-01", "mtime", now); ok {
		t.Error("a decay age resolved from a filesystem timestamp")
	}
	// The same date, claimed by the note itself, does anchor.
	if _, ok := ElapsedDays(log, "no-record", "no-record", "", "", "2015-01-01",
		"frontmatter:captured", now); !ok {
		t.Error("a note's own `captured` date failed to anchor")
	}
}

// An unrecognised sidecar version reads as no sidecar rather than as a shape it
// understands. A wrong anchor ranks a note confidently; a missing one declines
// to.
//
// Version 2 was the unknown shape when this was written, and is the shipped one
// now: keys are paths, and an entry may carry a fingerprint. Version 3 stands in
// for whatever comes next.
func TestAnUnknownSidecarVersionIsIgnored(t *testing.T) {
	vault := t.TempDir()
	blob := `{"version":3,"entries":{"x":{"last_access":"2026-08-19"}}}`
	if err := os.WriteFile(filepath.Join(vault, ".lifecycle.json"),
		[]byte(blob), 0o644); err != nil {
		t.Fatal(err)
	}
	if n := NewAccessLog(vault).Len(); n != 0 {
		t.Errorf("read %d entries from a version-3 sidecar; the shape is not understood", n)
	}
}

// The keying, and the fallback that keeps a vault whose sidecar predates it.
func TestTheClockIsKeyedOnThePathAndFallsBackToTheSlugOnlyOnVersionOne(t *testing.T) {
	// Two notes, one basename. The collision the re-keying exists to end: on
	// the live sidecar 26 keys matched more than one file, `progress` eight.
	v2 := t.TempDir()
	blob := `{"version":2,"entries":{` +
		`"projects/agentm/progress.md":{"last_access":"2026-09-10"},` +
		`"projects/blog/progress.md":{"last_access":"2026-01-01"}}}`
	if err := os.WriteFile(filepath.Join(v2, ".lifecycle.json"), []byte(blob), 0o644); err != nil {
		t.Fatal(err)
	}
	log := NewAccessLog(v2)
	a, okA := log.LastAccess("projects/agentm/progress.md", "progress")
	b, okB := log.LastAccess("projects/blog/progress.md", "progress")
	if !okA || !okB {
		t.Fatal("a path-keyed entry was not found by its path")
	}
	if a.Equal(b) {
		t.Error("two notes sharing a basename share one clock")
	}
	// The slug is not a key in version 2, and asking by it finds nothing —
	// otherwise the collision would come back through the reader.
	if _, ok := log.LastAccess("projects/crickets/progress.md", "progress"); ok {
		t.Error("a version-2 lookup fell back to the basename")
	}

	v1 := t.TempDir()
	if err := os.WriteFile(filepath.Join(v1, ".lifecycle.json"),
		[]byte(`{"version":1,"entries":{"a-fact":{"last_access":"2026-08-19"}}}`), 0o644); err != nil {
		t.Fatal(err)
	}
	old := NewAccessLog(v1)
	if _, ok := old.LastAccess("agent/memory/semantic/a-fact.md", "a-fact"); !ok {
		t.Error("a version-1 sidecar lost its clocks; the fallback is what carries " +
			"a vault from before the re-keying until the migration runs")
	}
}

// `created` is the rung that carries this corpus.
//
// The chain is `last_access` -> `updated` -> `created` -> a frontmatter
// `captured`, and the middle two are not interchangeable here: 69.7% of notes
// carry `created` and 7.6% carry `updated`. A chain that stopped at `updated`
// left 87.7% of the corpus with no age at all, which read as "this corpus has no
// age signal" and was really "the port dropped a rung."
func TestCreatedAnchorsWhenUpdatedIsAbsent(t *testing.T) {
	vault := t.TempDir()
	log := NewAccessLog(vault)
	now := time.Date(2026, 8, 20, 0, 0, 0, 0, time.UTC)

	days, ok := ElapsedDays(log, "no-record", "no-record", "", "2020-08-20", "", "mtime", now)
	if !ok {
		t.Fatal("a note carrying only `created` resolved no anchor")
	}
	if days < 2100 || days > 2250 {
		t.Errorf("elapsed %v days from a six-year-old `created`", days)
	}
	// And `updated` still wins when both are there: an entry substantively
	// edited today is fresh however long ago it was first written.
	days, ok = ElapsedDays(log, "no-record", "no-record", "2026-08-19", "2015-01-01", "", "mtime", now)
	if !ok {
		t.Fatal("no anchor resolved")
	}
	if days > 2 {
		t.Errorf("elapsed %v days — `created` beat `updated`", days)
	}
}
