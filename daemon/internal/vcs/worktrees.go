package vcs

import (
	"context"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/go-git/go-git/v5/config"
)

// A git worktree in the vault.
//
// On 2026-10-02 a background-task chip opened from a session in the vault made a
// worktree under `projects/pixelton/.claude/worktrees/`. Google Drive uploaded it
// file by file, and the `extensions.worktreeConfig` key the desktop app set then
// stopped this daemon committing on its next restart (#859). The
// vault-worktree-guard hook now refuses the tool calls that make one, and
// worktreeconfig.go lets the daemon read past the key. This file covers the
// routes no hook reaches: it finds what got through, so `agentmd status` names it
// the same day, and it removes the record of a worktree that is already gone.
//
// The three signs are the doctor's `vault-worktrees` row's, read the same way:
//
//   - the repository lists a worktree besides its main working tree;
//   - a `.claude/worktrees/` folder under the vault root holds something (the
//     empty one left at the vault root on 2026-08-16 is not a sign);
//   - the repository's config carries an `extensions.` key.
//
// All three are read from the filesystem, so a status call costs a directory
// walk and no git process. The check runs at startup and on every reconcile
// tick, and status reads the last answer.

// WorktreePruneExpire is how long a registered worktree's directory must have
// been gone before the daemon removes its record. It is git's own default for
// `gc.worktreePruneExpire`. `git gc` would do this prune itself, but the daemon
// turns automatic gc off in this repository (maintain.go), so it does the same
// prune, on the same terms. Pruning sooner is not safe. The record holds that
// worktree's HEAD and index, and a directory that is missing may only be on a
// volume that isn't mounted: prune it and a detached commit there loses its
// only ref.
const WorktreePruneExpire = "3.months.ago"

// worktreePruneEvery is how often the daemon tries the prune while a gone
// worktree is registered. A locked record stays however often it is tried.
const worktreePruneEvery = 24 * time.Hour

// worktreePruneTimeout bounds the git process, which runs on the watcher's own
// goroutine.
const worktreePruneTimeout = 30 * time.Second

// CheckWorktrees looks for the signs, removes the records of worktrees gone
// longer than WorktreePruneExpire, and keeps the answer for WorktreeSigns.
func (r *Repo) CheckWorktrees() []string {
	if !r.available || r.gitDir == "" {
		return nil
	}
	r.pruneGoneWorktrees()
	signs := r.readWorktreeSigns()
	r.mu.Lock()
	r.worktreeSigns = signs
	r.mu.Unlock()
	return signs
}

// WorktreeSigns is the last answer CheckWorktrees found, one phrase per sign.
func (r *Repo) WorktreeSigns() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]string(nil), r.worktreeSigns...)
}

// registeredWorktree is one entry under `<git dir>/worktrees/`.
type registeredWorktree struct {
	name string // the admin directory's name
	path string // the worktree's directory, from its `gitdir` file
	gone bool   // that directory's `.git` no longer exists
}

func (r *Repo) registeredWorktrees() []registeredWorktree {
	entries, err := os.ReadDir(filepath.Join(r.gitDir, "worktrees"))
	if err != nil {
		return nil
	}
	var out []registeredWorktree
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		wt := registeredWorktree{name: e.Name()}
		if b, err := os.ReadFile(filepath.Join(r.gitDir, "worktrees", e.Name(), "gitdir")); err == nil {
			dotGit := filepath.FromSlash(strings.TrimSpace(string(b)))
			if !filepath.IsAbs(dotGit) {
				// `git worktree add --relative-paths` writes it relative to the
				// record's own directory.
				dotGit = filepath.Join(r.gitDir, "worktrees", e.Name(), dotGit)
			}
			wt.path = filepath.Dir(filepath.Clean(dotGit))
			_, statErr := os.Stat(dotGit)
			wt.gone = os.IsNotExist(statErr)
		}
		out = append(out, wt)
	}
	return out
}

func (r *Repo) goneWorktrees() []string {
	var gone []string
	for _, wt := range r.registeredWorktrees() {
		if wt.gone {
			gone = append(gone, wt.name)
		}
	}
	return gone
}

// pruneGoneWorktrees runs git's own prune, with git's own expiry, at most once
// a day while a registered worktree's directory is missing. git decides what
// goes: it keeps a locked record, one still being created, and one gone for
// less than the expiry. So "pruned" is logged only for records that actually
// went.
func (r *Repo) pruneGoneWorktrees() {
	gone := r.goneWorktrees()
	if len(gone) == 0 {
		return
	}
	r.mu.Lock()
	due := r.worktreePruneTriedAt.IsZero() || time.Since(r.worktreePruneTriedAt) >= worktreePruneEvery
	if due {
		r.worktreePruneTriedAt = time.Now()
	}
	r.mu.Unlock()
	if !due {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), worktreePruneTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, "git", "--git-dir="+r.gitDir, "worktree", "prune", "--expire="+WorktreePruneExpire)
	if out, err := cmd.CombinedOutput(); err != nil {
		r.logger().Warn("pruning the records of gone worktrees failed",
			"records", gone, "err", err, "output", strings.TrimSpace(string(out)))
		return
	}
	still := map[string]bool{}
	for _, name := range r.goneWorktrees() {
		still[name] = true
	}
	var pruned []string
	for _, name := range gone {
		if !still[name] {
			pruned = append(pruned, name)
		}
	}
	if len(pruned) > 0 {
		r.logger().Info("pruned the records of worktrees that are gone",
			"records", pruned, "expire", WorktreePruneExpire)
	}
}

func (r *Repo) readWorktreeSigns() []string {
	var signs []string
	if keys := r.extensionKeys(); len(keys) > 0 {
		signs = append(signs, fmt.Sprintf("its git config carries %s, which a worktree leaves behind",
			strings.Join(keys, ", ")))
	}
	for _, wt := range r.registeredWorktrees() {
		where := wt.path
		if where == "" {
			where = wt.name
		}
		sign := fmt.Sprintf("git lists a worktree at %s", r.vaultRelative(where))
		if wt.gone {
			sign += " whose directory is gone"
		}
		signs = append(signs, sign)
	}
	for _, folder := range r.worktreeFolders() {
		signs = append(signs, fmt.Sprintf("%s/ holds a worktree", r.vaultRelative(folder)))
	}
	return signs
}

// extensionKeys reads the config file as it is on disk, past the storer that
// hides `extensions.worktreeConfig` from go-git.
func (r *Repo) extensionKeys() []string {
	f, err := os.Open(filepath.Join(r.gitDir, "config"))
	if err != nil {
		return nil
	}
	defer f.Close()
	cfg, err := config.ReadConfig(f)
	if err != nil || !cfg.Raw.HasSection("extensions") {
		return nil
	}
	var keys []string
	for _, o := range cfg.Raw.Section("extensions").Options {
		keys = append(keys, fmt.Sprintf("extensions.%s=%s", o.Key, o.Value))
	}
	sort.Strings(keys)
	return keys
}

// worktreeFolders is every `.claude/worktrees` directory under the vault root
// that holds something other than a dot-file: Finder leaves a `.DS_Store` in an
// empty folder, and that is not a worktree. The walk starts from the vault
// root with symlinks resolved, since a walk stops at a symlinked root; it skips
// `.git` and doesn't descend into a worktree folder once found.
func (r *Repo) worktreeFolders() []string {
	root := r.root
	if real, err := filepath.EvalSymlinks(root); err == nil {
		root = real
	}
	var found []string
	_ = filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			if d != nil && d.IsDir() && path != root {
				return fs.SkipDir
			}
			return nil
		}
		if !d.IsDir() {
			return nil
		}
		if d.Name() == ".git" {
			return fs.SkipDir
		}
		if d.Name() == "worktrees" && filepath.Base(filepath.Dir(path)) == ".claude" {
			if holdsAWorktree(path) {
				rel, _ := filepath.Rel(root, path)
				found = append(found, filepath.Join(r.root, rel))
			}
			return fs.SkipDir
		}
		return nil
	})
	return found
}

func holdsAWorktree(folder string) bool {
	entries, err := os.ReadDir(folder)
	if err != nil {
		return false
	}
	for _, e := range entries {
		if !strings.HasPrefix(e.Name(), ".") {
			return true
		}
	}
	return false
}

func (r *Repo) vaultRelative(path string) string {
	if rel, err := filepath.Rel(r.root, path); err == nil && !strings.HasPrefix(rel, "..") {
		return filepath.ToSlash(rel)
	}
	return path
}
