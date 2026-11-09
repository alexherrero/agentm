package health

import (
	"strings"
	"testing"
	"time"
)

// The runner's grade, on the cases scripts/test_runner_pulse.py pins for the
// memory skill's runner_pulse (task 198).

const every = 30 * time.Minute

func runnerInput(age time.Duration) Input {
	in := baseInput()
	in.Thresholds.RunnerInterval = every
	in.Runner = RunnerInput{Found: true, LastCycle: in.Now.Add(-age)}
	return in
}

func TestRunnerPulseGrades(t *testing.T) {
	for _, c := range []struct {
		age  time.Duration
		want string
		red  bool
	}{
		{time.Minute, RunnerOK, false},
		{every, RunnerOK, false},
		{every + time.Second, RunnerLate, false},
		{2 * every, RunnerLate, false},
		{2*every + time.Second, RunnerStopped, true},
	} {
		rep := Evaluate(runnerInput(c.age))
		if rep.Runner.State != c.want || rep.Red() != c.red {
			t.Errorf("age %s: state %q red %v, want %q red %v", c.age, rep.Runner.State, rep.Red(), c.want, c.red)
		}
	}
}

func TestRunnerPulseNamesTheStartupError(t *testing.T) {
	in := runnerInput(12 * time.Hour)
	in.Runner.StartupError = "runner.manifest.ManifestError: PyYAML not available — cannot parse job manifests"
	rep := Evaluate(in)
	if len(rep.Alerts) != 1 || rep.Alerts[0].Code != AlertRunnerStale ||
		!strings.Contains(rep.Alerts[0].Detail, "startup error: runner.manifest.ManifestError: PyYAML") {
		t.Fatalf("alerts = %+v", rep.Alerts)
	}
}

func TestRunnerPulseARunningCycleIsNotSilent(t *testing.T) {
	in := runnerInput(3 * every)
	in.Runner.Running, in.Runner.RunningSince = "enrich-nightly", in.Now.Add(-every)
	rep := Evaluate(in)
	if rep.Runner.State != RunnerOK || !strings.Contains(rep.Runner.Detail, "running `enrich-nightly`") {
		t.Fatalf("runner = %+v", rep.Runner)
	}
}

func TestRunnerPulseCountsFromTheLastWake(t *testing.T) {
	in := runnerInput(8 * time.Hour)
	in.Runner.Wake = in.Now.Add(-10 * time.Minute)
	if rep := Evaluate(in); rep.Runner.State != RunnerOK || !strings.Contains(rep.Runner.Detail, "woke 10m0s ago") {
		t.Fatalf("runner = %+v", rep.Runner)
	}
	in.Runner.Wake = in.Now.Add(-2*every - time.Minute)
	if rep := Evaluate(in); rep.Runner.State != RunnerStopped {
		t.Fatalf("awake past two intervals: runner = %+v", rep.Runner)
	}
	// A wake before the last cycle changes nothing.
	in = runnerInput(3 * every)
	in.Runner.Wake = in.Now.Add(-4 * every)
	if rep := Evaluate(in); rep.Runner.State != RunnerStopped {
		t.Fatalf("runner = %+v", rep.Runner)
	}
}

func TestRunnerPulseNoneIsReportedNotAlerted(t *testing.T) {
	in := baseInput()
	in.Thresholds.RunnerInterval = every
	if rep := Evaluate(in); rep.Runner.State != RunnerNone || rep.Red() {
		t.Fatalf("runner = %+v", rep.Runner)
	}
}
