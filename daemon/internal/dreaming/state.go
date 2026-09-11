package dreaming

import (
	"encoding/json"
	"os"
	"path/filepath"
	"time"
)

// State is what the pass remembers between runs: when it last started and
// finished, which run that was, and how it ended. It lives under the engine
// state dir — durable machine state, not a deletable cache — beside the
// journal, so a resume after a crash reads both from one place.
type State struct {
	LastStarted time.Time `json:"last_started"`
	// LastDone is when the last *applying* pass finished, and it is the clock
	// the gate reads. A report-only pass does not move it (agentm-vault plan
	// 04): three hand-run diagnostics on 2026-09-05/06 each reset it, and the
	// maps and the copy collapse sat frozen for a week behind them.
	LastDone time.Time `json:"last_done"`
	// LastReport is when the last report-only pass finished — its own stamp,
	// so a diagnostic is still recorded without touching the clock.
	LastReport  time.Time `json:"last_report,omitempty"`
	LastRunID   string    `json:"last_run_id,omitempty"`
	LastOutcome string    `json:"last_outcome,omitempty"`
	Runs        int       `json:"runs"`
	// ClassPopulations is the last pass's flat count per class, so the next
	// pass can say what grew.
	ClassPopulations map[string]int `json:"class_populations,omitempty"`
	// LastPassVersion is the filing pass version the last pass judged under;
	// a change triggers the sampled re-classification diff.
	LastPassVersion string `json:"last_pass_version,omitempty"`
}

// Dir is the pass's own directory under the engine state dir.
func Dir(engineStateDir string) string { return filepath.Join(engineStateDir, "dreaming") }

func statePath(engineStateDir string) string { return filepath.Join(Dir(engineStateDir), "state.json") }

// LastReportPath is where a completed pass leaves the report it rendered —
// the same JSON `agentmdream run -json` prints — for the morning note to read
// (filing-v2 remainders task 4; the dreaming scorecard read it until plan 04). A start that was refused or not due
// leaves the previous report where it was: the file describes the last pass
// that happened, never a pass that did not.
func LastReportPath(engineStateDir string) string {
	return filepath.Join(Dir(engineStateDir), "last-report.json")
}

// SaveLastReport writes the report atomically (a temp file renamed into
// place), so a reader never sees half of one.
func SaveLastReport(engineStateDir string, rep Report) error {
	if err := os.MkdirAll(Dir(engineStateDir), 0o755); err != nil {
		return err
	}
	blob, err := json.MarshalIndent(rep, "", "  ")
	if err != nil {
		return err
	}
	tmp := LastReportPath(engineStateDir) + ".tmp"
	if err := os.WriteFile(tmp, blob, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, LastReportPath(engineStateDir))
}

// LoadState reads the state, or returns a zero State when none exists yet.
func LoadState(engineStateDir string) (State, error) {
	var s State
	blob, err := os.ReadFile(statePath(engineStateDir))
	if err != nil {
		if os.IsNotExist(err) {
			return s, nil
		}
		return s, err
	}
	if err := json.Unmarshal(blob, &s); err != nil {
		return State{}, err
	}
	return s, nil
}

// SaveState writes the state atomically (tmp + rename).
func SaveState(engineStateDir string, s State) error {
	if err := os.MkdirAll(Dir(engineStateDir), 0o755); err != nil {
		return err
	}
	blob, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return err
	}
	p := statePath(engineStateDir)
	tmp := p + ".tmp"
	if err := os.WriteFile(tmp, append(blob, '\n'), 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, p)
}
