// Package vcs is the only thing in the system that runs git.
//
// A private git repository is sync, backup, history, and undo in one — principle
// 2. The daemon commits every change it sees with attribution, so `git log` is
// the record of what changed the vault and why, and a bad write is one revert
// away.
//
// Two facts about the current machine shape this package. The vault still lives
// on a cloud-sync mount and is not yet a git repository; moving it to local disk
// inside one is a later step, explicitly deferred. And the daemon must never
// create that repository on its own initiative — initializing git under 8,864
// files on a synced mount is exactly the unilateral migration the build sequence
// says not to perform. So a missing repository is a capability the daemon reports
// as unavailable, loudly and on every status surface, rather than a condition it
// silently fixes or silently ignores. Principle 4: a missing capability degrades
// visibly, never silently.
package vcs

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/plumbing"
	"github.com/go-git/go-git/v5/plumbing/object"
)

// ErrNoRepo means the vault root is not a git repository. It is a degraded
// capability, not a failure to fix by creating one.
var ErrNoRepo = errors.New("vault root is not a git repository")

// Origin is where a change came from. It is the attribution recorded in the
// commit, and the reason the phone's edits are distinguishable from the operator's
// own and from the daemon's.
type Origin string

const (
	// OriginCapture is the daemon writing a memory through memory_capture.
	OriginCapture Origin = "capture"
	// OriginPhone is an edit that arrived over the phone's sync set.
	OriginPhone Origin = "phone"
	// OriginLocal is a change made on this machine by something other than the
	// daemon — the operator in Obsidian, or in an editor.
	OriginLocal Origin = "local-edit"
	// OriginProbe is the daily self-probe writing or retiring its synthetic
	// note. It is distinguished from an ordinary capture so the one log that is
	// supposed to be readable does not report a memory being saved and deleted
	// every day — those two commits are the daemon proving it works.
	OriginProbe Origin = "self-probe"
)

// Repo is the vault's git repository, or a loud absence of one.
type Repo struct {
	root      string
	repo      *git.Repository
	mu        sync.Mutex
	available bool
	reason    string

	author object.Signature
}

// Open opens the vault's repository. A missing repository is not an error — the
// returned Repo reports itself unavailable and every Commit becomes a no-op that
// says why.
func Open(vaultRoot string) *Repo {
	r := &Repo{
		root: vaultRoot,
		author: object.Signature{
			Name:  "agentm daemon",
			Email: "agentm@localhost",
		},
	}
	// DetectDotGit stays off on purpose: it would walk upward and happily adopt
	// some unrelated ancestor repository, committing vault contents into it.
	repo, err := git.PlainOpen(vaultRoot)
	switch {
	case err == nil:
		r.repo, r.available = repo, true
	case errors.Is(err, git.ErrRepositoryNotExists):
		r.reason = fmt.Sprintf(
			"%v (%s) — the git-transport migration has not run; changes are indexed "+
				"and logged but not committed, and there is no undo until it does",
			ErrNoRepo, vaultRoot)
	default:
		r.reason = fmt.Sprintf("git unavailable at %s: %v", vaultRoot, err)
	}
	return r
}

// Available reports whether commits are actually happening.
func (r *Repo) Available() bool { return r.available }

// Status is the human-readable state, for the status surface.
func (r *Repo) Status() string {
	if r.available {
		return "committing to " + filepath.Join(r.root, ".git")
	}
	return r.reason
}

// Head is the commit the vault currently sits on, which is the point a
// corpus-wide job would be reverted to. Empty with no error means a repository
// with no commits yet.
func (r *Repo) Head() (string, error) {
	if !r.available {
		return "", ErrNoRepo
	}
	ref, err := r.repo.Head()
	if err != nil {
		if errors.Is(err, plumbing.ErrReferenceNotFound) {
			return "", nil
		}
		return "", err
	}
	return ref.Hash().String(), nil
}

// Dirty reports the vault-relative paths with uncommitted changes.
//
// This is the second half of "is there an undo". A repository gives you one
// only if the state before the job is a state you can name: with unrelated
// edits already sitting in the worktree, reverting the job and reverting the
// operator's afternoon are the same command.
//
// It hashes every tracked file, so it is seconds rather than milliseconds on a
// vault this size. That is the right price for a check that runs once before a
// job that rewrites thousands of notes.
func (r *Repo) Dirty() ([]string, error) {
	if !r.available {
		return nil, ErrNoRepo
	}
	r.mu.Lock()
	defer r.mu.Unlock()

	wt, err := r.repo.Worktree()
	if err != nil {
		return nil, fmt.Errorf("worktree: %w", err)
	}
	status, err := wt.Status()
	if err != nil {
		return nil, fmt.Errorf("status: %w", err)
	}
	var out []string
	for path, st := range status {
		if st.Worktree == git.Unmodified && st.Staging == git.Unmodified {
			continue
		}
		out = append(out, path)
	}
	sort.Strings(out)
	return out, nil
}

// Commit stages the given vault-relative paths and records one commit attributed
// to `origin`. Paths that no longer exist are staged as deletions.
//
// Returns the commit hash, or an empty string when there was nothing to commit or
// git is unavailable.
func (r *Repo) Commit(origin Origin, paths []string) (string, error) {
	if !r.available {
		return "", nil
	}
	if len(paths) == 0 {
		return "", nil
	}

	r.mu.Lock()
	defer r.mu.Unlock()

	wt, err := r.repo.Worktree()
	if err != nil {
		return "", fmt.Errorf("worktree: %w", err)
	}

	sort.Strings(paths)
	staged := 0
	var problems []string
	for _, rel := range paths {
		abs := filepath.Join(r.root, filepath.FromSlash(rel))
		_, statErr := os.Stat(abs)
		if statErr != nil {
			// Gone from disk: stage the deletion. go-git returns an error when the
			// path was never tracked, which is the ordinary case for a temp file
			// that came and went, and is not worth reporting.
			if _, err := wt.Remove(rel); err != nil {
				continue
			}
			staged++
			continue
		}
		if _, err := wt.Add(rel); err != nil {
			problems = append(problems, fmt.Sprintf("%s: %v", rel, err))
			continue
		}
		staged++
	}
	if staged == 0 {
		if len(problems) > 0 {
			return "", fmt.Errorf("nothing could be staged: %s", strings.Join(problems, "; "))
		}
		return "", nil
	}

	status, err := wt.Status()
	if err == nil && status.IsClean() {
		// The watcher can see an event for a write that did not change content —
		// a touch, or a sync round-trip. An empty commit would be noise in the
		// one log that is supposed to be readable.
		return "", nil
	}

	hash, err := wt.Commit(commitMessage(origin, paths), &git.CommitOptions{
		Author:    r.signature(),
		Committer: r.signature(),
	})
	if err != nil {
		return "", fmt.Errorf("commit: %w", err)
	}
	if len(problems) > 0 {
		return hash.String(), fmt.Errorf("committed with problems: %s", strings.Join(problems, "; "))
	}
	return hash.String(), nil
}

func (r *Repo) signature() *object.Signature {
	s := r.author
	s.When = time.Now()
	return &s
}

// commitMessage names what changed and where it came from. The origin line is the
// attribution: an edit that arrived from the phone is marked as such, which is
// what makes the phone's writes distinguishable from the operator's own once
// Syncthing is carrying them.
func commitMessage(origin Origin, paths []string) string {
	subject := ""
	switch origin {
	case OriginCapture:
		subject = "memory: capture"
	case OriginPhone:
		subject = "vault: edits from phone"
	case OriginProbe:
		subject = "memory: self-probe"
	default:
		subject = "vault: local edits"
	}
	if len(paths) == 1 {
		subject += " — " + paths[0]
	} else {
		subject += fmt.Sprintf(" — %d files", len(paths))
	}
	if len(subject) > 72 && len(paths) == 1 {
		subject = subject[:69] + "…"
	}

	var b strings.Builder
	b.WriteString(subject)
	b.WriteString("\n\norigin: ")
	b.WriteString(string(origin))
	b.WriteString("\n")
	if len(paths) > 1 {
		b.WriteString("\n")
		limit := len(paths)
		if limit > 50 {
			limit = 50
		}
		for _, p := range paths[:limit] {
			fmt.Fprintf(&b, "- %s\n", p)
		}
		if len(paths) > limit {
			fmt.Fprintf(&b, "- … and %d more\n", len(paths)-limit)
		}
	}
	return b.String()
}
