package dreaming

// A reviewed manifest of in-place rewrites, made through the journal (task 178).
//
// The one-time cleanups of the AgentKV rulings' Plan C — a session's second
// trace folded into its first, an article's chunk notes and an idea card's
// semantic copies superseded — are planned in Python, where the trace, ingest
// and idea code live. They still have to leave the record the night leaves:
// an intent journaled before each write, a lifecycle line for each supersede,
// a skip rather than a clobber when a note changed under the plan. So the
// plan is written as a manifest, the operator can read it, and this command
// makes it: every act names a note, the sha256 of the bytes the plan read,
// and the bytes to write in their place.
//
// It is all or nothing before the first write. A manifest one of whose notes
// changed since it was planned, or has gone, is refused whole: its acts were
// reviewed together, and half of a fold is not what anyone read.

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
)

// Manifest is the plan a one-time pass wrote.
type Manifest struct {
	// Job names the pass in the journal (`manifest-<name>` by convention).
	Job    string        `json:"job"`
	Reason string        `json:"reason,omitempty"`
	Acts   []ManifestAct `json:"acts"`
}

// ManifestAct is one rewrite of a note in place.
type ManifestAct struct {
	// Rel is relative to the memory root; `../` reaches the rest of the vault
	// (an idea card in `personal/ideas/`), and nothing may reach past it.
	Rel     string `json:"rel"`
	Before  string `json:"before_sha256"`
	After   string `json:"after"`
	Summary string `json:"summary,omitempty"`
	// From and To are the note's lifecycle state before and after, when the
	// act moves it on the axis (a supersede); they become the lifecycle
	// journal's line, the same as the night's own transitions.
	From string `json:"from,omitempty"`
	To   string `json:"to,omitempty"`
}

// ManifestResult is what a check or an application found.
type ManifestResult struct {
	Mode    string   `json:"mode"`
	Job     string   `json:"job"`
	Acts    int      `json:"acts"`
	Ready   int      `json:"ready"`
	Changed []string `json:"changed,omitempty"`
	Missing []string `json:"missing,omitempty"`
	Refused []string `json:"refused,omitempty"`
	Pending int      `json:"pending,omitempty"`
	RunID   string   `json:"run_id,omitempty"`
	Applied int      `json:"applied"`
	Skipped int      `json:"skipped"`
	Resumed int      `json:"resumed"`
}

// ApplyManifestOptions carries the run's switches.
type ApplyManifestOptions struct {
	Apply    bool
	Now      time.Time
	LockWait time.Duration
	RunID    string
}

// ErrManifestNotReady is a manifest refused before its first write: a note
// changed since it was planned, has gone, or lies outside the vault.
var ErrManifestNotReady = errors.New("the manifest is not ready to apply; nothing was written")

var manifestJob = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,63}$`)

// LoadManifest reads a manifest file.
func LoadManifest(path string) (Manifest, error) {
	var m Manifest
	raw, err := os.ReadFile(path)
	if err != nil {
		return m, err
	}
	if err := json.Unmarshal(raw, &m); err != nil {
		return m, fmt.Errorf("%s: %w", path, err)
	}
	return m, nil
}

// checkManifest reads every act's note against the plan. It writes nothing.
func checkManifest(root string, m Manifest, res *ManifestResult) map[int][]byte {
	current := map[int][]byte{}
	vault := filepath.Clean(vaultRootOf(root))
	if !manifestJob.MatchString(m.Job) {
		res.Refused = append(res.Refused, fmt.Sprintf("job %q is not a lower-case name", m.Job))
	}
	seen := map[string]bool{}
	for i, a := range m.Acts {
		abs := filepath.Clean(filepath.Join(root, filepath.FromSlash(a.Rel)))
		inVault, err := filepath.Rel(vault, abs)
		switch {
		case a.Rel == "" || filepath.IsAbs(filepath.FromSlash(a.Rel)) || strings.HasPrefix(a.Rel, "/") ||
			strings.HasPrefix(a.Rel, `\`) || filepath.VolumeName(filepath.FromSlash(a.Rel)) != "" ||
			err != nil || inVault == ".." || strings.HasPrefix(inVault, ".."+string(filepath.Separator)):
			res.Refused = append(res.Refused, a.Rel+": outside the vault")
			continue
		case strings.HasPrefix(filepath.ToSlash(inVault), ".git/") || strings.HasPrefix(filepath.ToSlash(inVault), ".obsidian/"):
			res.Refused = append(res.Refused, a.Rel+": not a note")
			continue
		case seen[abs]:
			res.Refused = append(res.Refused, a.Rel+": named twice")
			continue
		case a.After == "":
			res.Refused = append(res.Refused, a.Rel+": no bytes to write")
			continue
		}
		seen[abs] = true
		cur, err := os.ReadFile(abs)
		switch {
		case os.IsNotExist(err):
			res.Missing = append(res.Missing, a.Rel)
		case err != nil:
			res.Refused = append(res.Refused, a.Rel+": "+err.Error())
		case Hash(cur) != a.Before:
			res.Changed = append(res.Changed, a.Rel)
		default:
			current[i] = cur
			res.Ready++
		}
	}
	return current
}

// ApplyManifest checks a manifest and, with Apply, makes it through the
// journal under the pass's own lock, after settling anything a crashed pass
// left half-done. Without Apply it writes nothing at all.
func ApplyManifest(cfg *config.Config, m Manifest, opt ApplyManifestOptions) (ManifestResult, error) {
	now := opt.Now
	if now.IsZero() {
		now = time.Now().UTC()
	}
	if opt.LockWait <= 0 {
		opt.LockWait = 2 * time.Second
	}
	res := ManifestResult{Mode: "check", Job: m.Job, Acts: len(m.Acts)}
	if opt.Apply {
		res.Mode = "apply"
	}
	root := filepath.Join(cfg.VaultPath, filepath.FromSlash(cfg.MemoryRoot))
	lock, err := Acquire(SingletonLockDir(cfg.EngineStateDir), 30*time.Second, opt.LockWait)
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
	runID, pending := Unfinished(entries)
	if !opt.Apply {
		res.Pending = len(pending)
	} else if runID != "" {
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
	current := checkManifest(root, m, &res)
	if len(res.Refused)+len(res.Changed)+len(res.Missing) > 0 {
		if opt.Apply {
			return res, ErrManifestNotReady
		}
		return res, nil
	}
	if !opt.Apply {
		return res, nil
	}
	newRun := opt.RunID
	if newRun == "" {
		newRun = newRunID(now)
	}
	res.RunID = newRun
	if err := journal.Append(Entry{Kind: KindRunStart, RunID: newRun, TS: now, Mode: "apply"}); err != nil {
		return res, err
	}
	var rep Report
	for i, a := range m.Acts {
		in := Intent{Job: m.Job, Rel: filepath.ToSlash(filepath.Clean(filepath.FromSlash(a.Rel))),
			Before: current[i], After: []byte(a.After), Summary: a.Summary}
		if a.From != "" || a.To != "" {
			reason := a.Summary
			if reason == "" {
				reason = m.Reason
			}
			in.Meta = map[string]string{"from": a.From, "to": a.To, "reason": reason}
		}
		if _, err := commitOne(journal, root, newRun, in, now, &rep); err != nil {
			return res, err
		}
	}
	res.Applied, res.Skipped = rep.Applied, rep.Skipped
	err = journal.Append(Entry{Kind: KindRunDone, RunID: newRun, TS: now,
		Outcome: fmt.Sprintf("%s: %d act(s), %d applied, %d skipped", m.Job, len(m.Acts), rep.Applied, rep.Skipped)})
	return res, err
}
