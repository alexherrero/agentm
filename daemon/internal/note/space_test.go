package note

import (
	"testing"
	"time"
)

func withDampened(t *testing.T, spaces []string) {
	t.Helper()
	before := DampenedSpaces()
	SetDampenedSpaces(spaces)
	t.Cleanup(func() { SetDampenedSpaces(before) })
}

// The whole point of the change: a space is demoted, not hidden. A note that
// cannot be returned at all cannot be returned when it is the only answer, and
// an invisible space is how this vault lost 9,786 notes once already.
func TestADampenedNoteIsDemotedNotExcluded(t *testing.T) {
	withDampened(t, []string{"personal"})

	flags := classify("personal/Church/Prepared Lessons/lesson.md", "", "Some lesson notes.\n", "active", "", 0, false)
	m := Multiplier(flags)

	if m >= 1.0 {
		t.Errorf("multiplier %v — a dampened space is not being demoted at all", m)
	}
	if m <= 0 {
		t.Errorf("multiplier %v — a dampened space must still be rankable", m)
	}
}

func TestAnUndampenedSpaceIsUntouched(t *testing.T) {
	withDampened(t, []string{"personal"})
	flags := classify("agent/memory/semantic/a-fact.md", "", "A durable fact.\n", "active", "", 0, false)
	for _, f := range flags {
		if f == ClassSpace {
			t.Errorf("a note outside the dampened set carries %q: %v", ClassSpace, flags)
		}
	}
}

func TestMatchingIsOnTheFirstSegmentOnly(t *testing.T) {
	// A folder called `personal` deep in the tree must not silently demote
	// itself — a space is a top-level directory, and matching deeper would make
	// the rule fire on a name rather than on a space.
	withDampened(t, []string{"personal"})
	flags := classify("agent/desk/projects/x/personal/notes.md", "", "Project notes.\n", "active", "", 0, false)
	for _, f := range flags {
		if f == ClassSpace {
			t.Error("a nested folder named `personal` was treated as the Personal space")
		}
	}
}

func TestMatchingIsCaseInsensitive(t *testing.T) {
	withDampened(t, []string{"personal"})
	if !inDampenedSpace("Personal/Home/a.md") { // root-casing: the fold's other spelling, on purpose
		t.Error("case mismatch defeated the match; the vault writes `Personal/`") // root-casing: the fold's other spelling, on purpose
	}
}

// A contract that names no dampened space dampens nothing. That is the old
// behaviour and a legitimate choice, not a broken file.
func TestAnEmptySetDampensNothing(t *testing.T) {
	withDampened(t, nil)
	if inDampenedSpace("personal/Home/a.md") {
		t.Error("something was dampened with no set configured")
	}
}

// The safe direction when the contract will not parse. Dampening too little is a
// leak the operator can see; dampening too much is an answer that never arrives.
func TestNothingIsDampenedBeforeAnythingIsSet(t *testing.T) {
	withDampened(t, nil)
	flags := classify("personal/Church/x.md", "", "Body.\n", "active", "", 0, false)
	for _, f := range flags {
		if f == ClassSpace {
			t.Error("a space was dampened with no contract loaded")
		}
	}
}

// Demotion compounds with the other classes rather than replacing them, so a
// note that is both dampened and a fragment is demoted twice and still ranked.
func TestDampeningCompoundsWithOtherClasses(t *testing.T) {
	withDampened(t, []string{"personal"})
	both := Multiplier([]string{ClassSpace, ClassFragment})
	spaceOnly := Multiplier([]string{ClassSpace})
	if both >= spaceOnly {
		t.Errorf("compounded multiplier %v is not below the single %v", both, spaceOnly)
	}
	if both <= 0 {
		t.Errorf("compounded multiplier %v zeroed the score out", both)
	}
}

func TestSetIsNormalised(t *testing.T) {
	withDampened(t, []string{"  /personal/  ", "", "   "})
	got := DampenedSpaces()
	if len(got) != 1 || got[0] != "personal" {
		t.Errorf("normalised set is %v, want [personal]", got)
	}
}

// A record ranks with its project's activity, and a project the night has not
// read yet ranks at full weight: absent is not quiet. Since task 182 step 4 the
// ranker asks ProjectActivityOf when a query runs, and the index stamps no band:
// a band frozen at index time outlived the reading it came from.
func TestAProjectsRecordsRankWithItsActivity(t *testing.T) {
	t.Cleanup(func() { SetProjectActivity(nil) })
	SetProjectActivity(map[string]float64{"quiet": 0.7, "quieter": 0.5, "cold": 0.3})

	cases := map[string]float64{
		"projects/quiet/decisions/a.md":    0.70,
		"projects/quieter/decisions/a.md":  0.50,
		"projects/cold/decisions/a.md":     0.30,
		"projects/unread/decisions/a.md":   1.00,
		"../projects/quiet/decisions/a.md": 0.70,
		"agent/memory/semantic/a-fact.md":  1.00,
	}
	for rel, want := range cases {
		if got := ProjectActivityOf(rel); got != want {
			t.Errorf("%s ranks %v, want %v", rel, got, want)
		}
		for _, f := range classify(rel, "", "a body", "", "", 0, false) {
			if f == ClassProjectQuiet || f == ClassProjectQuieter || f == ClassProjectCold {
				t.Errorf("%s: the index stamped band %q; the band is read at query time", rel, f)
			}
		}
	}
}

// The night rewrites the readings; a running daemon picks them up on the next
// ranking pass after the file changes, without a restart or a reindex, and
// looks at the file at most every activityRecheck.
func TestTheReadingsReloadWhenTheNightRewritesThem(t *testing.T) {
	t.Cleanup(func() { SetProjectActivity(nil); SetProjectActivitySource(nil, nil) })
	stamp := time.Date(2026, 10, 1, 2, 0, 0, 0, time.UTC)
	reading := map[string]float64{"primos": 1.0}
	loads := 0
	SetProjectActivity(reading)
	SetProjectActivitySource(func() map[string]float64 { loads++; return reading },
		func() time.Time { return stamp })

	now := time.Date(2026, 10, 2, 9, 0, 0, 0, time.UTC)
	// The first refresh after a source is set loads once, so a reading written
	// between the caller's load and the source going in is never missed.
	RefreshProjectActivity(now)
	if loads != 1 {
		t.Fatalf("the first refresh syncs once; loads = %d", loads)
	}
	RefreshProjectActivity(now.Add(activityRecheck + time.Second))
	if loads != 1 {
		t.Fatalf("an unchanged file is not read again; loads = %d", loads)
	}
	reading = map[string]float64{"primos": 0.5}
	stamp = stamp.Add(24 * time.Hour)
	later := now.Add(activityRecheck + 2*time.Second)
	RefreshProjectActivity(later)
	if got := ProjectActivityOf("projects/primos/charter.md"); got != 1.0 || loads != 1 {
		t.Fatalf("inside the recheck window nothing is read: got %v, loads %d", got, loads)
	}
	RefreshProjectActivity(later.Add(activityRecheck + time.Second))
	if got := ProjectActivityOf("projects/primos/charter.md"); got != 0.5 || loads != 2 {
		t.Fatalf("after the window a rewritten file is read once: got %v, loads %d", got, loads)
	}
}

func TestWithNoReadingsNothingRanksByActivity(t *testing.T) {
	t.Cleanup(func() { SetProjectActivity(nil) })
	SetProjectActivity(nil)
	if got := ProjectActivityOf("projects/anything/a.md"); got != 1.0 {
		t.Errorf("activity = %v with no readings, want 1.0", got)
	}
}

// Task 182's release review: the reading the night writes between the caller's
// load and the source going in must still reach the ranker.
func TestAReadingWrittenBetweenLoadAndSourceIsNotLost(t *testing.T) {
	t.Cleanup(func() { SetProjectActivitySource(nil, nil); SetProjectActivity(nil) })
	file := map[string]float64{"x": 1.0}
	mtime := time.Unix(100, 0)
	SetProjectActivity(file)
	file = map[string]float64{"x": 0.3}
	mtime = time.Unix(200, 0)
	SetProjectActivitySource(func() map[string]float64 { return file }, func() time.Time { return mtime })
	RefreshProjectActivity(time.Now())
	if got := ProjectActivityOf("projects/x/a.md"); got != 0.3 {
		t.Fatalf("activity = %v, want the night's 0.3", got)
	}
}
