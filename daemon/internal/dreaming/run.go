package dreaming

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// Options is one invocation's shape.
type Options struct {
	// Apply makes the writes. Off, the pass is report-only: it decides,
	// prints what it would do, journals the run, and touches no note. Off is
	// the default until the parity gate flips it (task 6).
	Apply bool
	// Force skips the dual gate — an operator asking for a pass now.
	Force bool
	// Every is the minimum interval between passes.
	Every time.Duration
	// Now is injectable; zero means the clock.
	Now time.Time
	// Pace sleeps between mutations. Tests use it to land a kill mid-pass;
	// production leaves it zero.
	Pace time.Duration
	// Cap bounds automatic demotions per pass.
	Cap int
	// RunID is injectable; empty means a fresh one.
	RunID string
	// LockWait is how long to wait for a live holder before refusing.
	LockWait time.Duration
}

// Report is the record of one invocation, printed by the command.
type Report struct {
	RunID    string        `json:"run_id,omitempty"`
	Mode     string        `json:"mode"`
	Decision Decision      `json:"decision"`
	Resumed  int           `json:"resumed"`
	Plan     LifecyclePlan `json:"plan"`
	Applied  int           `json:"applied"`
	Skipped  int           `json:"skipped"`
	Outcome  string        `json:"outcome"`
	Refused  string        `json:"refused,omitempty"`
	Root     string        `json:"root,omitempty"`
}

// ErrRefused is returned (with a Report) when the pass could not take its
// lock: another pass is live. The command maps it to exit 3.
var ErrRefused = errors.New("refused")

const (
	OutcomeNotDue   = "not-due"
	OutcomeReported = "reported"
	OutcomeApplied  = "applied"
)

// Run does one pass: lock, resume anything a crash left half-done, decide
// whether a new pass is due, plan, apply (or report), and record.
func Run(cfg *config.Config, opt Options) (Report, error) {
	now := opt.Now
	if now.IsZero() {
		now = time.Now().UTC()
	}
	if opt.Every <= 0 {
		opt.Every = 7 * 24 * time.Hour
	}
	if opt.LockWait <= 0 {
		opt.LockWait = 2 * time.Second
	}
	rep := Report{Mode: "report"}
	if opt.Apply {
		rep.Mode = "apply"
	}
	root := filepath.Join(cfg.VaultPath, filepath.FromSlash(cfg.MemoryRoot))
	rep.Root = root

	lock, err := Acquire(SingletonLockDir(cfg.EngineStateDir), 30*time.Second, opt.LockWait)
	if err != nil {
		var held *ErrHeld
		if errors.As(err, &held) {
			rep.Refused = held.Error()
			rep.Outcome = "refused"
			return rep, ErrRefused
		}
		return rep, err
	}
	defer lock.Release()

	st, err := LoadState(cfg.EngineStateDir)
	if err != nil {
		return rep, err
	}
	journal, err := OpenJournal(cfg.EngineStateDir)
	if err != nil {
		return rep, err
	}

	// Resume first, gate second: a crash's half-done work is finished
	// whether or not a new pass is due.
	entries, err := journal.Read()
	if err != nil {
		return rep, err
	}
	if runID, pending := Unfinished(entries); runID != "" {
		for _, e := range pending {
			if _, err := journal.Resolve(root, e, now); err != nil {
				return rep, err
			}
			rep.Resumed++
		}
		if err := journal.Append(Entry{Kind: KindRunDone, RunID: runID, TS: now,
			Outcome: fmt.Sprintf("resumed: %d intent(s) settled", len(pending))}); err != nil {
			return rep, err
		}
		st.LastDone, st.LastOutcome = now, "resumed"
		if err := SaveState(cfg.EngineStateDir, st); err != nil {
			return rep, err
		}
	}

	act := activity(cfg, st)
	rep.Decision = Due(st, now, opt.Every, act)
	if opt.Force {
		rep.Decision.Due, rep.Decision.Reason = true, "forced"
	}
	if !rep.Decision.Due {
		rep.Outcome = OutcomeNotDue
		return rep, nil
	}

	var contract *rules.Rules
	if cfg.Rules != nil {
		if r, err := cfg.Rules.Get(); err == nil {
			contract = r
		}
	}
	runID := opt.RunID
	if runID == "" {
		runID = newRunID(now)
	}
	rep.RunID = runID
	st.LastStarted, st.LastRunID, st.Runs = now, runID, st.Runs+1
	if err := SaveState(cfg.EngineStateDir, st); err != nil {
		return rep, err
	}
	if err := journal.Append(Entry{Kind: KindRunStart, RunID: runID, TS: now, Mode: rep.Mode}); err != nil {
		return rep, err
	}

	plan, err := PlanLifecycle(root, contract, now, opt.Cap)
	if err != nil {
		return rep, err
	}
	rep.Plan = plan
	if opt.Apply {
		for i, in := range plan.Intents {
			id := fmt.Sprintf("%s-%04d", runID, i+1)
			kind, err := journal.Commit(root, runID, id, in, now)
			if err != nil {
				return rep, err
			}
			if kind == KindSkipped {
				rep.Skipped++
			} else {
				rep.Applied++
				from, to := transitionOf(in)
				if err := AppendLifecycleJournal(cfg.EngineStateDir, in.Rel, from, to, in.Summary, runID, now); err != nil {
					return rep, err
				}
			}
			if opt.Pace > 0 {
				time.Sleep(opt.Pace)
			}
		}
		rep.Outcome = OutcomeApplied
	} else {
		rep.Outcome = OutcomeReported
	}
	if err := journal.Append(Entry{Kind: KindRunDone, RunID: runID, TS: now, Outcome: rep.Outcome}); err != nil {
		return rep, err
	}
	st.LastDone, st.LastOutcome = now, rep.Outcome
	if err := SaveState(cfg.EngineStateDir, st); err != nil {
		return rep, err
	}
	return rep, nil
}

// activity reads what happened since the last completed pass: captures from
// the index, genuine recalls from the recall history. No index at all means
// the instrument is missing, which the gate treats as active.
func activity(cfg *config.Config, st State) Activity {
	act := Activity{Since: st.LastDone}
	if st.LastDone.IsZero() {
		act.Since = time.Time{}
	}
	if cfg.IndexPath != "" {
		if _, err := os.Stat(cfg.IndexPath); err == nil {
			idx, err := index.Open(cfg.IndexPath, cfg.VaultPath, cfg.MemoryRoot, false)
			if err == nil {
				prefix := ""
				if cfg.MemoryRoot != "" {
					prefix = cfg.MemoryRoot + "/"
				}
				if n, err := idx.CapturedSince(act.Since, prefix); err == nil {
					act.Captures = n
				} else {
					act.Unknown = true
				}
				idx.Close()
			} else {
				act.Unknown = true
			}
		} else {
			act.Unknown = true
		}
	} else {
		act.Unknown = true
	}
	if n, err := RecallsSince(RecallHistoryPath(), act.Since); err == nil {
		act.Recalls = n
	}
	return act
}

func newRunID(now time.Time) string {
	var b [4]byte
	_, _ = rand.Read(b[:])
	return now.UTC().Format("20060102-150405") + "-" + hex.EncodeToString(b[:])
}
