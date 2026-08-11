// Package gate answers one question: is it safe to start a job that rewrites
// the corpus?
//
// The question has a specific history. The alias backfill edited 1,930 notes
// with a hand-rolled revert journal as its only undo, because the vault is not
// a git repository yet — and the missing repository turned out to be the binding
// constraint twice in a single session. Dreaming's inbox drain is a larger job
// than that one, and the build sequence's answer was an ordering sentence: git
// transport lands before the first corpus-wide pass. A sentence in a design
// document is not a precondition. This is.
//
// The contract: no corpus-wide write job — migration, backfill,
// reclassification, dreaming's future drain — starts unless this passes. It is
// machine-readable on purpose, so a job script asks rather than a person
// remembering to.
package gate

import (
	"errors"
	"fmt"
	"strings"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/vcs"
)

// CorpusWrite is the gate's name on the command line.
const CorpusWrite = "corpus-write"

// Reason codes. Stable strings — a job script may branch on them.
const (
	ReasonGitDegraded = "git-degraded"
	ReasonDirtyTree   = "uncommitted-changes"
	ReasonUnreadable  = "repository-unreadable"
)

// Result is the gate's verdict.
type Result struct {
	Gate string `json:"gate"`
	Pass bool   `json:"pass"`
	// Reasons is empty exactly when Pass is true.
	Reasons []Reason `json:"reasons"`
	// Head is the commit a job started now would be reverted to. It is the
	// gate's other half: a job that has to be undone needs a point to be undone
	// to, and the gate is the last thing that saw one.
	Head  string `json:"head,omitempty"`
	Vault string `json:"vault"`
}

// Reason is one thing standing in the way.
type Reason struct {
	Code   string `json:"code"`
	Detail string `json:"detail"`
	// Remedy is what to do about it, because a gate that refuses without saying
	// how to satisfy it gets worked around rather than satisfied.
	Remedy string `json:"remedy"`
}

// ErrRefused is returned alongside a failing Result, so a caller that only
// checks the error still fails closed.
var ErrRefused = errors.New("corpus-write gate refused")

// Evaluate runs the gate against the configured vault.
//
// A returned error other than ErrRefused means the gate could not decide, and
// that is also a refusal — every caller must treat "I don't know" as "no". The
// Result is populated either way.
func Evaluate(cfg *config.Config) (Result, error) {
	res := Result{Gate: CorpusWrite, Vault: cfg.VaultPath, Reasons: []Reason{}}

	repo := vcs.Open(cfg.VaultPath)
	if !repo.Available() {
		res.Reasons = append(res.Reasons, Reason{
			Code:   ReasonGitDegraded,
			Detail: repo.Status(),
			Remedy: "run the git-transport migration, or initialize a repository at the " +
				"vault root yourself — the daemon will not do it on its own initiative",
		})
		return res, ErrRefused
	}

	head, err := repo.Head()
	if err != nil {
		res.Reasons = append(res.Reasons, Reason{
			Code:   ReasonUnreadable,
			Detail: fmt.Sprintf("could not read HEAD: %v", err),
			Remedy: "check the repository at " + cfg.VaultPath + " by hand",
		})
		return res, ErrRefused
	}
	res.Head = head
	if head == "" {
		res.Reasons = append(res.Reasons, Reason{
			Code:   ReasonGitDegraded,
			Detail: "the repository has no commits, so there is no state to revert to",
			Remedy: "commit the vault as it stands before starting a corpus-wide job",
		})
		return res, ErrRefused
	}

	dirty, err := repo.Dirty()
	if err != nil {
		res.Reasons = append(res.Reasons, Reason{
			Code:   ReasonUnreadable,
			Detail: fmt.Sprintf("could not read the worktree status: %v", err),
			Remedy: "check the repository at " + cfg.VaultPath + " by hand",
		})
		return res, ErrRefused
	}
	if len(dirty) > 0 {
		res.Reasons = append(res.Reasons, Reason{
			Code: ReasonDirtyTree,
			Detail: fmt.Sprintf(
				"%d path(s) carry uncommitted changes, so undoing this job and undoing "+
					"whatever else is in the worktree would be the same operation: %s",
				len(dirty), sample(dirty)),
			Remedy: "let the daemon commit them — within a second of the last write for " +
				"an ordinary file, or on the next reconcile pass for a tracked file " +
				"under a dot directory. The one case it will not act on by itself is a " +
				"file under a dot directory that git does not track yet, since that is " +
				"how a sync client's staging churn would otherwise enter history: " +
				"commit that one yourself, or add it to .gitignore",
		})
		return res, ErrRefused
	}

	res.Pass = true
	return res, nil
}

// Explain renders the verdict for a person.
func (r Result) Explain() string {
	var b strings.Builder
	verdict := "REFUSED"
	if r.Pass {
		verdict = "PASS"
	}
	fmt.Fprintf(&b, "gate %s: %s\n", r.Gate, verdict)
	fmt.Fprintf(&b, "  vault: %s\n", r.Vault)
	if r.Head != "" {
		fmt.Fprintf(&b, "  head:  %s  (the point a job started now would be reverted to)\n", r.Head)
	}
	for _, reason := range r.Reasons {
		fmt.Fprintf(&b, "\n  %s\n", reason.Code)
		fmt.Fprintf(&b, "    %s\n", reason.Detail)
		fmt.Fprintf(&b, "    fix: %s\n", reason.Remedy)
	}
	if r.Pass {
		b.WriteString("\n  A corpus-wide write job may start. Record the head above; it is the undo.\n")
	} else {
		b.WriteString("\n  No corpus-wide write job may start. There is no undo for one.\n")
	}
	return b.String()
}

func sample(paths []string) string {
	const limit = 5
	if len(paths) <= limit {
		return strings.Join(paths, ", ")
	}
	return fmt.Sprintf("%s, … and %d more",
		strings.Join(paths[:limit], ", "), len(paths)-limit)
}
