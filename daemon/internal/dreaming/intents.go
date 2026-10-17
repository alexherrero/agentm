package dreaming

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
)

// An outside pass's own writes, made through the journal (task 192).
//
// The idea judge runs in `agentmd`, because it calls a model and the dreaming
// binary never does. Its writes still have to leave the record the night leaves:
// an intent journaled before each write, an applied or skipped line after it,
// and a skip rather than a clobber when a note changed under the plan. A
// manifest does that for in-place rewrites only; the judge also creates a card
// in the inbox. ApplyIntents is the same run under the same lock for intents
// the pass built itself — a creation (Before nil) or an edit in place.

// IntentsResult is what one ApplyIntents run did.
type IntentsResult struct {
	RunID   string `json:"run_id,omitempty"`
	Applied int    `json:"applied"`
	Skipped int    `json:"skipped"`
	// AppliedRels and SkippedRels name each intent's note, in order, so the
	// pass can tell which of its plans landed.
	AppliedRels []string `json:"applied_rels,omitempty"`
	SkippedRels []string `json:"skipped_rels,omitempty"`
	Resumed     int      `json:"resumed,omitempty"`
}

// ApplyIntents journals and makes `intents` as one run of `job`, under the
// pass's own lock, after settling anything a crashed pass left half-done. Every
// Rel is relative to the memory root; `../` reaches the rest of the vault (an
// idea card in `personal/ideas/`), and nothing may reach past the vault.
func ApplyIntents(cfg *config.Config, job, outcome string, intents []Intent, now time.Time, lockWait time.Duration) (IntentsResult, error) {
	var res IntentsResult
	if len(intents) == 0 {
		return res, nil
	}
	if now.IsZero() {
		now = time.Now().UTC()
	}
	if lockWait <= 0 {
		lockWait = 2 * time.Second
	}
	root := filepath.Join(cfg.VaultPath, filepath.FromSlash(cfg.MemoryRoot))
	vault := filepath.Clean(vaultRootOf(root))
	for _, in := range intents {
		abs := filepath.Clean(filepath.Join(root, filepath.FromSlash(in.Rel)))
		rel, err := filepath.Rel(vault, abs)
		if err != nil || rel == ".." || filepath.IsAbs(filepath.FromSlash(in.Rel)) ||
			len(rel) >= 3 && rel[:3] == ".."+string(filepath.Separator) {
			return res, fmt.Errorf("%s: outside the vault", in.Rel)
		}
	}
	lock, err := Acquire(SingletonLockDir(cfg.EngineStateDir), 30*time.Second, lockWait)
	if err != nil {
		var held *ErrHeld
		if errors.As(err, &held) {
			return res, ErrRefused
		}
		return res, err
	}
	defer lock.Release()
	journal, err := OpenJournal(cfg.EngineStateDir)
	if err != nil {
		return res, err
	}
	entries, err := journal.Read()
	if err != nil {
		return res, err
	}
	if runID, pending := Unfinished(entries); runID != "" {
		for _, e := range pending {
			if _, err := journal.Resolve(root, e, now); err != nil {
				return res, err
			}
			res.Resumed++
		}
		if err := journal.Append(Entry{Kind: KindRunDone, RunID: runID, TS: now,
			Outcome: fmt.Sprintf("resumed: %d intent(s) settled", len(pending))}); err != nil {
			return res, err
		}
	}
	res.RunID = newRunID(now)
	if err := journal.Append(Entry{Kind: KindRunStart, RunID: res.RunID, TS: now, Mode: "apply"}); err != nil {
		return res, err
	}
	var rep Report
	for _, in := range intents {
		in.Job = job
		ok, err := commitOne(journal, root, res.RunID, in, now, &rep)
		if err != nil {
			return res, err
		}
		if ok {
			res.AppliedRels = append(res.AppliedRels, in.Rel)
		} else {
			res.SkippedRels = append(res.SkippedRels, in.Rel)
		}
	}
	res.Applied, res.Skipped = rep.Applied, rep.Skipped
	if outcome == "" {
		outcome = fmt.Sprintf("%s: %d intent(s), %d applied, %d skipped", job, len(intents), rep.Applied, rep.Skipped)
	}
	err = journal.Append(Entry{Kind: KindRunDone, RunID: res.RunID, TS: now, Outcome: outcome})
	return res, err
}

// The idea judge's night, as the dreaming facet reports it (task 192). The judge
// writes this summary into the engine state's `dreaming/` after each run, and
// the dreaming binary, which runs after it, reads the day's summary into the
// facet. Counts only: the remarks themselves are the run record's.
const IdeaJudgeLatestFile = "idea-judge-latest.json"

// IdeaJudgeSummary is one judge run's counts.
type IdeaJudgeSummary struct {
	At       time.Time `json:"at"`
	Mode     string    `json:"mode"`
	Remarks  int       `json:"remarks"`
	Judged   int       `json:"judged"`
	Steering int       `json:"steering"`
	Repeats  int       `json:"repeats"`
	New      int       `json:"new"`
	Removed  int       `json:"removed"`
	Deferred int       `json:"deferred"`
	Written  []string  `json:"written,omitempty"`
}

// IdeaJudgeLatestPath is where the summary lives.
func IdeaJudgeLatestPath(engineStateDir string) string {
	return filepath.Join(engineStateDir, "dreaming", IdeaJudgeLatestFile)
}

// WriteIdeaJudgeSummary records a run's counts for the facet.
func WriteIdeaJudgeSummary(engineStateDir string, s IdeaJudgeSummary) error {
	p := IdeaJudgeLatestPath(engineStateDir)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return err
	}
	blob, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return err
	}
	tmp := p + ".tmp"
	if err := os.WriteFile(tmp, append(blob, '\n'), 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, p)
}

// ReadIdeaJudgeSummary is the judge's summary when it ran on `now`'s UTC day,
// else nil: an older night's counts are not tonight's.
func ReadIdeaJudgeSummary(engineStateDir string, now time.Time) *IdeaJudgeSummary {
	raw, err := os.ReadFile(IdeaJudgeLatestPath(engineStateDir))
	if err != nil {
		return nil
	}
	var s IdeaJudgeSummary
	if json.Unmarshal(raw, &s) != nil || s.At.UTC().Format("2006-01-02") != now.UTC().Format("2006-01-02") {
		return nil
	}
	return &s
}
