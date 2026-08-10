package health

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

// Every expectation in this file is a hand-written literal from the design, not
// a value recomputed from Evaluate's own arithmetic. A check that derives what it
// expects from the code it checks proves only that they agree with each other.
//
// The design's sentence, which these pin: "under a standing daily ingest, fifty
// fresh unfiled items every morning is an ordinary Tuesday, while the oldest
// unfiled item being three days old means the pipeline has stalled."

var testThresholds = Thresholds{
	UnfiledAge:   72 * time.Hour,
	UnfiledCount: 1000,
	IndexStale:   15 * time.Minute,
	ProbeStale:   48 * time.Hour,
	ProbeBudget:  10 * time.Second,
}

// now is a fixed clock. A test that reads the wall clock is a test whose failure
// depends on when it ran.
var now = time.Date(2026, 8, 9, 12, 0, 0, 0, time.UTC)

// healthy is the shape of a daemon with nothing wrong: everything below varies
// one field from it, so what turned a report red is the thing the test changed.
//
// Its whole queue is post-baseline, which is the ordinary steady state once the
// inherited backlog has been drained or aged past. The inherited case has its
// own tests below.
func healthy() Input {
	return Input{
		Now:                now,
		Uptime:             30 * 24 * time.Hour,
		Unfiled:            50,
		UnfiledSince:       50,
		OldestUnfiled:      now.Add(-6 * time.Hour),
		OldestUnfiledSince: now.Add(-6 * time.Hour),
		Baseline:           now.Add(-90 * 24 * time.Hour),
		Documents:          8864,
		LastReconcile:      now.Add(-2 * time.Minute),
		GitAvailable:       true,
		Probe: ProbeState{
			OK: true, Recorded: true, Elapsed: Duration(84 * time.Millisecond),
		},
		ProbeAt:    now.Add(-3 * time.Hour),
		Thresholds: testThresholds,
	}
}

// queueOf builds an input whose whole queue is post-baseline, so a test that is
// about a threshold does not have to restate the baseline split.
func queueOf(count int, oldest time.Duration) Input {
	in := healthy()
	in.Unfiled, in.UnfiledSince = count, count
	in.OldestUnfiled = now.Add(-oldest)
	in.OldestUnfiledSince = in.OldestUnfiled
	return in
}

func TestFiftyFreshUnfiledItemsIsAnOrdinaryTuesday(t *testing.T) {
	rep := Evaluate(healthy())
	if rep.Red() {
		t.Fatalf("a queue of 50 fresh unfiled items paged the operator: %s", codes(rep))
	}
	if rep.Queue.Unfiled != 50 {
		t.Errorf("queue reported %d unfiled, want 50", rep.Queue.Unfiled)
	}
	if got := time.Duration(rep.Queue.OldestAge); got != 6*time.Hour {
		t.Errorf("oldest age = %s, want 6h", got)
	}
}

func TestTheQueueIsAgeDominant(t *testing.T) {
	// Three items, one of them four days old. Tiny queue, stalled pipeline.
	rep := Evaluate(queueOf(3, 96*time.Hour))
	if !rep.Red() {
		t.Fatal("a four-day-old unfiled item did not page, so the threshold is not age-dominant")
	}
	if !has(rep, AlertQueueAge) {
		t.Errorf("expected a %s alert, got %s", AlertQueueAge, codes(rep))
	}

	// And the mirror case: a big queue that is draining is not an emergency.
	if rep := Evaluate(queueOf(900, 2*time.Hour)); rep.Red() {
		t.Errorf("900 items with a two-hour-old head paged: %s", codes(rep))
	}
}

func TestTheAgeThresholdIsThreeDays(t *testing.T) {
	for _, tc := range []struct {
		age time.Duration
		red bool
	}{
		{71 * time.Hour, false},
		{72 * time.Hour, false}, // at the threshold, not past it
		{73 * time.Hour, true},
	} {
		rep := Evaluate(queueOf(50, tc.age))
		if got := has(rep, AlertQueueAge); got != tc.red {
			t.Errorf("oldest unfiled %s: red=%v, want %v", tc.age, got, tc.red)
		}
	}
}

func TestTheSizeBackstopIsAThousand(t *testing.T) {
	if has(Evaluate(queueOf(1000, time.Hour)), AlertQueueSize) {
		t.Error("1000 unfiled items fired the size backstop; the threshold is past it, not at it")
	}
	rep := Evaluate(queueOf(1001, time.Hour))
	if !has(rep, AlertQueueSize) {
		t.Errorf("1001 unfiled items did not fire the size backstop: %s", codes(rep))
	}
	// The backstop exists for the case age cannot see: a producer that wrote
	// thousands of items in the last hour.
	if has(rep, AlertQueueAge) {
		t.Error("a queue of fresh items fired the age alert")
	}
}

func TestAnEmptyQueueHasNoAge(t *testing.T) {
	in := healthy()
	in.Unfiled, in.UnfiledSince = 0, 0
	in.OldestUnfiled, in.OldestUnfiledSince = time.Time{}, time.Time{}
	rep := Evaluate(in)
	if rep.Red() {
		t.Errorf("an empty queue paged: %s", codes(rep))
	}
	if rep.Queue.OldestAge != 0 || rep.Queue.OldestAt != "" {
		t.Errorf("an empty queue reported an oldest item: %+v", rep.Queue)
	}
}

// TestTheInheritedBacklogIsReportedAndDoesNotPage.
//
// The real vault's first status read was 4,349 unfiled items with the oldest 29
// days old. Both numbers are true and neither is news: the design already
// decided that pile is rank-penalized and drained by dreaming later, and their
// dates come from filesystem mtime, which a sync client can rewrite wholesale.
// Paging daily on it would have made the queue alert useless before it ever
// carried a real signal.
func TestTheInheritedBacklogIsReportedAndDoesNotPage(t *testing.T) {
	in := healthy()
	in.Baseline = now.Add(-24 * time.Hour)
	in.Unfiled = 4349
	in.OldestUnfiled = now.Add(-29 * 24 * time.Hour)
	in.UnfiledSince = 0
	in.OldestUnfiledSince = time.Time{}

	rep := Evaluate(in)
	if rep.Red() {
		t.Fatalf("the inherited backlog paged: %s", codes(rep))
	}
	if rep.Queue.Unfiled != 4349 {
		t.Errorf("the total is %d; the backlog must stay reported, never hidden — "+
			"the previous system's sin was concealing a pile", rep.Queue.Unfiled)
	}
	if rep.Queue.Inherited != 4349 {
		t.Errorf("inherited = %d, want 4349", rep.Queue.Inherited)
	}
	if rep.Queue.InheritedOldestAge == 0 {
		t.Error("the backlog's own age is not reported, so it can be forgotten")
	}
	if rep.Queue.Baseline == "" {
		t.Error("the report does not say where the line was drawn")
	}
}

// TestAStallAfterTheBaselineStillPages is the other half, and the one that
// makes the split honest rather than a mute button.
func TestAStallAfterTheBaselineStillPages(t *testing.T) {
	in := healthy()
	in.Baseline = now.Add(-30 * 24 * time.Hour)
	in.Unfiled = 4350
	in.OldestUnfiled = now.Add(-29 * 24 * time.Hour)
	in.UnfiledSince = 1
	in.OldestUnfiledSince = now.Add(-96 * time.Hour)

	rep := Evaluate(in)
	if !has(rep, AlertQueueAge) {
		t.Fatalf("one four-day-old item behind a large inherited backlog did not page: %s",
			codes(rep))
	}
	if rep.Queue.Since != 1 {
		t.Errorf("since = %d, want 1", rep.Queue.Since)
	}
}

func TestBaselineIsRecordedOnceAndReadBackAfter(t *testing.T) {
	dir := t.TempDir()
	first := Baseline(dir, time.Time{}, now)
	if !first.Equal(now) {
		t.Fatalf("the first run recorded %s, want %s", first, now)
	}
	// A later run must not move the line, or every restart would forgive
	// whatever accumulated since the last one.
	later := Baseline(dir, time.Time{}, now.Add(72*time.Hour))
	if !later.Equal(first) {
		t.Errorf("a later run moved the baseline to %s", later)
	}
}

func TestAConfiguredBaselineWins(t *testing.T) {
	dir := t.TempDir()
	Baseline(dir, time.Time{}, now)
	want := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	if got := Baseline(dir, want, now); !got.Equal(want) {
		t.Errorf("baseline = %s, want the configured %s", got, want)
	}
}

// TestDegradedGitIsReportedAndDoesNotPage pins a deliberate decision rather than
// an implementation detail. The vault is not a repository until the
// git-transport migration runs, and emailing daily about a migration that is
// scheduled for later is how an alert channel teaches its reader to delete it
// unread. The gate is what makes the degradation bite.
func TestDegradedGitIsReportedAndDoesNotPage(t *testing.T) {
	in := healthy()
	in.GitAvailable = false
	in.GitReason = "vault root is not a git repository (/x/vault) — the git-transport migration has not run"
	rep := Evaluate(in)

	if rep.Red() {
		t.Errorf("degraded git paged the operator: %s", codes(rep))
	}
	if rep.Git.Healthy() {
		t.Error("degraded git reported itself healthy")
	}
	if got := rep.Git.String(); got != "degraded: not a repository" {
		t.Errorf("git state = %q, want %q", got, "degraded: not a repository")
	}
}

func TestHealthyGitSaysSo(t *testing.T) {
	if got := Evaluate(healthy()).Git.String(); got != "healthy" {
		t.Errorf("git state = %q, want %q", got, "healthy")
	}
}

func TestAFailedProbePages(t *testing.T) {
	in := healthy()
	in.Probe = ProbeState{Recorded: true, OK: false, Detail: "the alias query did not return it"}
	rep := Evaluate(in)
	if !has(rep, AlertProbeFailed) {
		t.Fatalf("a failed self-probe did not page: %s", codes(rep))
	}
	if !strings.Contains(rep.Alerts[0].Detail, "alias query") {
		t.Errorf("the alert did not carry the probe's own reason: %q", rep.Alerts[0].Detail)
	}
}

// TestAStoppedProberPages is the failure that hides every other one: a probe
// that passed and then stopped running looks exactly like a healthy system.
func TestAStoppedProberPages(t *testing.T) {
	in := healthy()
	in.ProbeAt = now.Add(-49 * time.Hour)
	if !has(Evaluate(in), AlertProbeStale) {
		t.Error("a self-probe that last passed 49 hours ago did not page")
	}

	in.ProbeAt = now.Add(-47 * time.Hour)
	if has(Evaluate(in), AlertProbeStale) {
		t.Error("a self-probe from 47 hours ago paged; one missed daily run is a blip")
	}
}

func TestAFreshDaemonIsNotRedForNotHavingProbedYet(t *testing.T) {
	in := healthy()
	in.Probe = ProbeState{}
	in.ProbeAt = time.Time{}
	in.Uptime = 2 * time.Minute
	if rep := Evaluate(in); rep.Red() {
		t.Errorf("a two-minute-old daemon was red: %s", codes(rep))
	}

	// But one that has been up for days without ever probing is a stopped
	// scheduler, and that is exactly what the threshold is for.
	in.Uptime = 72 * time.Hour
	if !has(Evaluate(in), AlertProbeStale) {
		t.Error("three days of uptime with no probe ever did not page")
	}
}

func TestAStalledReconcileLoopPages(t *testing.T) {
	in := healthy()
	in.LastReconcile = now.Add(-16 * time.Minute)
	rep := Evaluate(in)
	if !has(rep, AlertIndexStale) {
		t.Fatalf("a reconcile pass 16 minutes overdue did not page: %s", codes(rep))
	}
	if rep.Index.Fresh {
		t.Error("the index reported itself fresh while its alert was firing")
	}
}

func TestFingerprintIsStableAndOrderIndependent(t *testing.T) {
	a := Report{Alerts: []Alert{{Code: AlertQueueAge}, {Code: AlertProbeFailed}}}
	b := Report{Alerts: []Alert{{Code: AlertProbeFailed}, {Code: AlertQueueAge}}}
	if a.Fingerprint() != b.Fingerprint() {
		t.Errorf("the same two conditions fingerprinted differently: %q vs %q",
			a.Fingerprint(), b.Fingerprint())
	}
	if a.Fingerprint() != "probe-failed+queue-age" {
		t.Errorf("fingerprint = %q, want %q", a.Fingerprint(), "probe-failed+queue-age")
	}
	if (Report{}).Fingerprint() != "" {
		t.Error("a report with no alerts produced a fingerprint")
	}
}

func TestDurationReadsAsSomethingAPersonCanCheck(t *testing.T) {
	for _, tc := range []struct {
		in   time.Duration
		want string
	}{
		{0, "0s"},
		{840 * time.Millisecond, "840ms"},
		{90 * time.Second, "1m30s"},
		{6 * time.Hour, "6h0m0s"},
		{73 * time.Hour, "3d1h"},
		{72 * time.Hour, "3d"},
	} {
		if got := Duration(tc.in).String(); got != tc.want {
			t.Errorf("%s rendered as %q, want %q", tc.in, got, tc.want)
		}
	}
}

// TestDurationSurvivesTheRoundTripThroughJSON is a regression with a name.
//
// The status surface renders a long age as "4d1h" so a person can read it, and
// `agentmd status` reads that same document back over HTTP. time.ParseDuration
// has no day unit, so the CLI broke on exactly the reports it exists to
// show — a queue stalled for four days, a daemon up for a month. Every test
// before this one used a duration short enough to dodge it; the real vault did
// not.
func TestDurationSurvivesTheRoundTripThroughJSON(t *testing.T) {
	for _, in := range []time.Duration{
		0,
		840 * time.Millisecond,
		90 * time.Second,
		6 * time.Hour,
		72 * time.Hour,
		97 * time.Hour,
		28*24*time.Hour + 22*time.Hour,
	} {
		blob, err := Duration(in).MarshalJSON()
		if err != nil {
			t.Fatalf("%s: marshal: %v", in, err)
		}
		var back Duration
		if err := back.UnmarshalJSON(blob); err != nil {
			t.Fatalf("%s marshalled as %s and would not read back: %v", in, blob, err)
		}
		if back.String() != Duration(in).String() {
			t.Errorf("%s round-tripped to %s", Duration(in), back)
		}
	}
}

func TestAReportSurvivesTheRoundTripThroughJSON(t *testing.T) {
	in := queueOf(50, 97*time.Hour) // red, and past the day boundary
	in.Uptime = 30 * 24 * time.Hour
	before := Evaluate(in)

	blob, err := json.Marshal(before)
	if err != nil {
		t.Fatal(err)
	}
	var after Report
	if err := json.Unmarshal(blob, &after); err != nil {
		t.Fatalf("a red report would not read back, which is what `agentmd status` "+
			"does on every call: %v\n%s", err, blob)
	}
	if after.Level != LevelRed || after.Queue.OldestAge != before.Queue.OldestAge {
		t.Errorf("the report changed across the round trip:\n  before %+v\n  after  %+v",
			before.Queue, after.Queue)
	}
}

func has(r Report, code string) bool {
	for _, a := range r.Alerts {
		if a.Code == code {
			return true
		}
	}
	return false
}

func codes(r Report) string {
	if len(r.Alerts) == 0 {
		return "(no alerts)"
	}
	out := make([]string, 0, len(r.Alerts))
	for _, a := range r.Alerts {
		out = append(out, a.Code)
	}
	return strings.Join(out, ", ")
}
