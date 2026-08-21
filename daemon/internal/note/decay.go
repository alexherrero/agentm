package note

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// Decay: a memory nobody has needed in years ranks lower, and never disappears.
//
// # Why this is here rather than in Python
//
// It was in Python, and that turned out to mean it almost never ran. The curve
// lives in `lifecycle.compute_decay_score`, which is applied in exactly one
// place — `recall.query`, the in-process engine that the recall hook uses only
// as a *fallback* for when the daemon is absent, slow, or broken. The daemon
// serves the real path and had no decay at all, so a design that says "a decay
// score governs rank" was true of a code path that rarely executes.
//
// That also explains a measurement trap this arc walked into: promoting the
// stepped curve and scoring it against the gold set through the daemon would
// have reported exactly zero effect, and the null would have meant nothing.
//
// # The curve
//
// Full strength through six months of silence, half to a year, an eighth to
// three years, a sixteenth to five — and **the sixteenth is a floor, not a
// waypoint.** The curve never reaches zero, because a memory nobody has needed
// in four years is cold rather than worthless, and a floorless curve makes it
// unreachable rather than merely unlikely.
//
// # What resets the clock
//
// Only a genuine recall. A lint walk, an index rebuild or a nightly pass
// touching a file must never count, or the maintenance machinery quietly
// refreshes everything it inspects and decay stops working. The access record
// lives in the vault's `.lifecycle.json`, written by whatever served the recall;
// when a note has no record, the fallback anchor is `updated` and then
// `captured`.
//
// `updated`, not `captured`, is the right fallback: an entry substantively
// edited today is fresh regardless of when it was first written, and anchoring
// to `captured` would penalize a frequently-maintained reference for staleness
// it does not have.

// The stepped bands, ported from lifecycle.py's `_STEPPED_BANDS` so the two
// curves are the same curve. Each pair is (elapsed days at or below, score).
var decayBands = []struct {
	Days  float64
	Score float64
}{
	{182, 1.0},
	{365, 0.5},
	{1095, 0.125},
	{1825, 0.0625},
}

// DecayFloor is what a memory scores once past the last band. A floor, not a
// waypoint: the curve never reaches zero.
const DecayFloor = 0.0625

// DecayScore maps elapsed days to a multiplier.
func DecayScore(elapsedDays float64) float64 {
	if elapsedDays < 0 {
		// A clock that went backwards — a timezone change, an NTP correction, an
		// mtime from the future on a synced mount. Treated as fresh rather than as
		// a negative age, which would otherwise read as the far side of the curve.
		return 1.0
	}
	for _, b := range decayBands {
		if elapsedDays <= b.Days {
			return b.Score
		}
	}
	return DecayFloor
}

// accessRecord is the shape `.lifecycle.json` carries.
type accessRecord struct {
	// Version is checked rather than ignored, because the failure mode of
	// reading an unrecognised shape is worse than reading nothing: a v2 sidecar
	// misparsed as v1 yields wrong anchors, and a wrong anchor ranks a note
	// confidently rather than declining to.
	Version int `json:"version"`
	Entries map[string]struct {
		LastAccess string `json:"last_access"`
	} `json:"entries"`
}

// accessRecordVersion is the sidecar shape this reader understands, matching
// `lifecycle._load_sidecar`.
const accessRecordVersion = 1

// AccessLog is the recall-access sidecar, read once and cached.
//
// A cache in the strict sense: losing it costs every note its access anchor and
// falls the whole corpus back to `updated`, which is a ranking change and not a
// data loss. It is deliberately not consulted per search — a 50KB parse on the
// hot path would be the kind of thing the capture budget exists to catch.
type AccessLog struct {
	mu      sync.RWMutex
	byslug  map[string]time.Time
	loaded  bool
	vault   string
	modTime time.Time
}

// NewAccessLog reads the sidecar under `vault`. A missing or unparseable file is
// an empty log rather than an error: decay still works from `updated`, and a
// ranking pass that refused to run because a cache was corrupt would be trading
// a small inaccuracy for no answer at all.
func NewAccessLog(vault string) *AccessLog {
	a := &AccessLog{vault: vault, byslug: map[string]time.Time{}}
	a.Refresh()
	return a
}

// Refresh re-reads the sidecar when it has changed on disk.
func (a *AccessLog) Refresh() {
	path := filepath.Join(a.vault, ".lifecycle.json")
	info, err := os.Stat(path)
	if err != nil {
		return
	}
	a.mu.RLock()
	unchanged := a.loaded && info.ModTime().Equal(a.modTime)
	a.mu.RUnlock()
	if unchanged {
		return
	}

	blob, err := os.ReadFile(path)
	if err != nil {
		return
	}
	var rec accessRecord
	if err := json.Unmarshal(blob, &rec); err != nil {
		return
	}
	if rec.Version != accessRecordVersion {
		return
	}

	parsed := make(map[string]time.Time, len(rec.Entries))
	for slug, e := range rec.Entries {
		if t, err := time.Parse("2006-01-02", strings.TrimSpace(e.LastAccess)); err == nil {
			parsed[slug] = t
		}
	}

	a.mu.Lock()
	a.byslug = parsed
	a.loaded = true
	a.modTime = info.ModTime()
	a.mu.Unlock()
}

// LastAccess returns the recorded genuine-recall date for a slug.
func (a *AccessLog) LastAccess(slug string) (time.Time, bool) {
	a.mu.RLock()
	defer a.mu.RUnlock()
	t, ok := a.byslug[slug]
	return t, ok
}

// Len is how many notes carry an access record.
func (a *AccessLog) Len() int {
	a.mu.RLock()
	defer a.mu.RUnlock()
	return len(a.byslug)
}

// ElapsedDays resolves a note's age by the anchor chain the design specifies:
// genuine recall first, then `updated`, then `captured`. Returns false when no
// anchor resolves, which callers treat as no basis to decay rather than as
// maximum age.
func ElapsedDays(log *AccessLog, slug, updated, created, captured, capturedSrc string,
	now time.Time) (float64, bool) {
	var anchor time.Time
	if log != nil {
		if t, ok := log.LastAccess(slug); ok {
			anchor = t
		}
	}
	if anchor.IsZero() {
		anchor = parseAnchor(updated)
	}
	// `created` is where the Python chain ends, and on this corpus it is the rung
	// that matters: 69.7% of notes carry it and 7.6% carry `updated`. A chain
	// that stops at `updated` leaves seven notes in ten with no age at all.
	if anchor.IsZero() {
		anchor = parseAnchor(created)
	}
	// `captured` only when the note said so itself. It falls back to the
	// filesystem, and mtime is the one anchor this corpus cannot use: the
	// type-collapse migration rewrote 9,899 notes' frontmatter in an afternoon,
	// which would make every one of them look freshly updated to a curve reading
	// the filesystem — and, worse, make the handful it missed look uniquely old.
	if anchor.IsZero() && strings.HasPrefix(capturedSrc, "frontmatter:") {
		anchor = parseAnchor(captured)
	}
	if anchor.IsZero() {
		// No basis to compute an age. Fresh rather than ancient: a note with no
		// date is a note nothing is known about, and the safe direction is to
		// leave its rank alone.
		return 0, false
	}
	return now.Sub(anchor).Hours() / 24, true
}

// parseAnchor accepts the two shapes the corpus writes: a bare date, and an
// RFC3339 timestamp.
func parseAnchor(s string) time.Time {
	s = strings.TrimSpace(s)
	if s == "" {
		return time.Time{}
	}
	for _, layout := range []string{time.RFC3339, "2006-01-02T15:04:05Z", "2006-01-02"} {
		if t, err := time.Parse(layout, s); err == nil {
			return t
		}
	}
	if len(s) >= 10 {
		if t, err := time.Parse("2006-01-02", s[:10]); err == nil {
			return t
		}
	}
	return time.Time{}
}

// IsDecayExempt reports whether a note never ages.
//
// One flag, set at index time by `isDurable`, which is where the four routes
// into durability are read — see classify.go. This is a lookup on the search hot
// path rather than a re-derivation, for the same reason the class penalties are.
func IsDecayExempt(flags []string) bool {
	for _, f := range flags {
		if f == ClassDurable {
			return true
		}
	}
	return false
}
