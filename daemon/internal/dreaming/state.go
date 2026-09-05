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
	LastDone    time.Time `json:"last_done"`
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
