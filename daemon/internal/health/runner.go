package health

import (
	"fmt"
	"time"
)

// AlertRunnerStale is the scheduled-job runner no longer cycling (task 198). On
// 2026-10-23 every cycle exited at startup for twelve hours and nothing went
// red, because every surface read what the last cycle did and none read when.
const AlertRunnerStale = "runner-stale"

// Runner states.
const (
	RunnerOK      = "ok"
	RunnerLate    = "late"
	RunnerStopped = "stopped"
	RunnerNone    = "none"
	RunnerUnknown = "unknown"
)

// RunnerInput is pulse.ReadRunner's reading, with the machine's last wake.
type RunnerInput struct {
	Found        bool
	LastCycle    time.Time
	Running      string
	RunningSince time.Time
	StartupError string
	Err          string
	// Wake is the machine's last wake; zero where it cannot be read.
	Wake time.Time
}

// Runner is the runner's standing.
type Runner struct {
	State       string   `json:"state"`
	LastCycleAt string   `json:"last_cycle_at,omitempty"`
	Age         Duration `json:"age,omitempty"`
	Running     string   `json:"running,omitempty"`
	Detail      string   `json:"detail,omitempty"`
}

// String is the status line's one-liner.
func (r Runner) String() string {
	switch r.State {
	case "":
		return "not reported by the running daemon — it predates the runner reading; restart it"
	case RunnerNone:
		return "no cycle recorded"
	}
	return r.Detail
}

// evaluateRunner grades the runner by the rule the memory skill's
// runner_pulse module states, and pins in the same cases: the last cycle's
// start, or a running cycle's, is fresh within one interval, late past one
// and stopped past two. The age counts from the machine's last wake when that
// is later, because launchd counts a StartInterval in awake time.
func evaluateRunner(in Input, r *Report) Runner {
	ri, every := in.Runner, in.Thresholds.RunnerInterval
	if !ri.Found || every <= 0 {
		return Runner{State: RunnerNone}
	}
	if ri.Err != "" {
		return Runner{State: RunnerUnknown, Detail: "unreadable: " + ri.Err}
	}
	began, how := ri.LastCycle, "the last cycle began"
	out := Runner{LastCycleAt: ri.LastCycle.UTC().Format(time.RFC3339)}
	if ri.Running != "" {
		began, how = ri.RunningSince, fmt.Sprintf("a cycle is running `%s`, started", ri.Running)
		out.Running = ri.Running
	}
	age := since(in.Now, began)
	out.Age = Duration(age)
	graded := age
	out.Detail = fmt.Sprintf("%s %s ago", how, short(age))
	if ri.Wake.After(began) && !ri.Wake.After(in.Now) {
		awake := since(in.Now, ri.Wake)
		graded = min(graded, awake)
		out.Detail += fmt.Sprintf(" (the machine woke %s ago)", short(awake))
	}
	switch {
	case graded <= every:
		out.State = RunnerOK
		return out
	case graded <= 2*every:
		out.State = RunnerLate
	default:
		out.State = RunnerStopped
	}
	out.Detail += fmt.Sprintf(" — the runner starts one every %s", short(every))
	if ri.StartupError != "" {
		out.Detail += "; its log ends in a startup error: " + ri.StartupError
	}
	if out.State == RunnerStopped {
		r.add(AlertRunnerStale, "the scheduled-job runner has stopped: "+out.Detail)
	}
	return out
}
