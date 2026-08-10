package health

import (
	"encoding/json"
	"os"
	"path/filepath"
	"time"
)

// The queue baseline separates the backlog the daemon inherited from the queue
// it is responsible for.
//
// This exists because of what the first run against the real vault reported:
// 4,349 unfiled items, the oldest 29 days old. Both numbers are true and
// neither is news. The design already decided what happens to that pile —
// rank-penalize it, and let dreaming drain it on dreaming's own schedule once
// dreaming exists — so an alert that fires on it every day is an alert about a
// decision already made, and the operator would filter the sender inside a
// week. That is the same failure the git-degraded alert was left out to avoid.
//
// There is a second reason, and it is the stronger one. Almost no note in the
// existing corpus carries a `captured` field, so their dates come from
// filesystem mtime — which a cloud-sync client can rewrite wholesale, and
// evidently has. The age of an inherited item is therefore approximate in a way
// nothing can fix from here. Going forward it is exact: the daemon writes
// `captured` on everything it captures, and a note the operator creates by hand
// has an mtime that genuinely is its creation time. So the threshold is applied
// where the number it reads is trustworthy.
//
// Nothing is hidden. The total, the inherited count, and the baseline date are
// on every status surface — this changes what pages, not what is reported.

// baselineFile is where the inherited-backlog boundary is recorded.
const baselineFile = "queue-baseline.json"

type baselineRecord struct {
	At   time.Time `json:"at"`
	Note string    `json:"note"`
}

// Baseline resolves the inherited-backlog boundary.
//
// An explicitly configured date wins. Otherwise the first run records `now` and
// every later run reads it back, so the boundary is the moment this daemon first
// took responsibility for the queue rather than a date anyone has to maintain.
func Baseline(stateDir string, configured time.Time, now time.Time) time.Time {
	if !configured.IsZero() {
		return configured.UTC()
	}
	path := filepath.Join(stateDir, baselineFile)
	if blob, err := os.ReadFile(path); err == nil {
		var rec baselineRecord
		if err := json.Unmarshal(blob, &rec); err == nil && !rec.At.IsZero() {
			return rec.At.UTC()
		}
	}

	rec := baselineRecord{
		At: now.UTC(),
		Note: "Unfiled items captured before this are the backlog this daemon " +
			"inherited: reported on every status surface, and not paged about, " +
			"because their fate is already decided and their dates are " +
			"mtime-derived. Set daemon.queue_baseline to override.",
	}
	if blob, err := json.MarshalIndent(rec, "", "  "); err == nil {
		if err := os.MkdirAll(stateDir, 0o755); err == nil {
			tmp := path + ".tmp"
			if os.WriteFile(tmp, append(blob, '\n'), 0o644) == nil {
				_ = os.Rename(tmp, path)
			}
		}
	}
	return rec.At
}
