package dreaming

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// The dual gate. A pass runs when enough time has passed since the last one
// AND something happened in between — a capture landed, a genuine recall was
// served. The first condition keeps the pass off the hot path; the second
// keeps it from re-reading a corpus nothing touched. Either alone would be
// wrong: time alone dreams over an idle vault every week, activity alone
// dreams after every capture.

// Activity is what happened since the last completed pass.
type Activity struct {
	Since    time.Time `json:"since"`
	Captures int       `json:"captures"`
	Recalls  int       `json:"recalls"`
	// Unknown is set when no signal could be read at all (no index, no
	// history); the gate then treats the vault as active rather than never
	// running — a missing instrument must not become a silent stop.
	Unknown bool `json:"unknown,omitempty"`
}

// Any reports whether anything happened.
func (a Activity) Any() bool { return a.Unknown || a.Captures > 0 || a.Recalls > 0 }

// Decision is the gate's answer and its reason, in the words the log shows.
type Decision struct {
	Due      bool          `json:"due"`
	Reason   string        `json:"reason"`
	Elapsed  time.Duration `json:"elapsed"`
	Every    time.Duration `json:"every"`
	Activity Activity      `json:"activity"`
}

// Due decides. `every` is the minimum interval between passes.
func Due(st State, now time.Time, every time.Duration, act Activity) Decision {
	d := Decision{Every: every, Activity: act}
	if st.LastDone.IsZero() {
		d.Elapsed = 0
		if !act.Any() {
			d.Reason = "never run, but nothing to dream about yet (no captures, no recalls)"
			return d
		}
		d.Due, d.Reason = true, "never run"
		return d
	}
	d.Elapsed = now.Sub(st.LastDone)
	if d.Elapsed < every {
		d.Reason = fmt.Sprintf("last pass finished %s ago; next in %s", d.Elapsed.Round(time.Minute), (every - d.Elapsed).Round(time.Minute))
		return d
	}
	if !act.Any() {
		d.Reason = fmt.Sprintf("%s since the last pass, but nothing happened since (no captures, no recalls)", d.Elapsed.Round(time.Minute))
		return d
	}
	d.Due = true
	d.Reason = fmt.Sprintf("%s since the last pass; %d capture(s), %d recall(s) since", d.Elapsed.Round(time.Minute), act.Captures, act.Recalls)
	return d
}

// RecallHistoryPath is the recall counter's append-only ledger —
// `$AGENTM_RECALL_HISTORY`, else `~/.cache/agentm/telemetry/recall-history.jsonl`
// — written only by recall.py's prompt_submit, which makes it the one clock
// that ticks when a human is actually using the memory.
func RecallHistoryPath() string {
	if v := os.Getenv("AGENTM_RECALL_HISTORY"); v != "" {
		return v
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return filepath.Join(home, ".cache", "agentm", "telemetry", "recall-history.jsonl")
}

// RecallsSince counts the recall-history rows stamped after `since`. A
// missing file is zero rows, not an error.
func RecallsSince(path string, since time.Time) (int, error) {
	if path == "" {
		return 0, nil
	}
	f, err := os.Open(path)
	if err != nil {
		if os.IsNotExist(err) {
			return 0, nil
		}
		return 0, err
	}
	defer f.Close()
	n := 0
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 1<<20), 16<<20)
	for sc.Scan() {
		var row struct {
			TS any `json:"ts"`
		}
		if err := json.Unmarshal(sc.Bytes(), &row); err != nil {
			continue
		}
		if t, ok := parseTS(row.TS); ok && t.After(since) {
			n++
		}
	}
	return n, sc.Err()
}

func parseTS(v any) (time.Time, bool) {
	switch x := v.(type) {
	case float64:
		return time.Unix(int64(x), 0).UTC(), true
	case string:
		for _, layout := range []string{time.RFC3339Nano, time.RFC3339, "2006-01-02T15:04:05", "2006-01-02"} {
			if t, err := time.Parse(layout, x); err == nil {
				return t, true
			}
		}
	}
	return time.Time{}, false
}
