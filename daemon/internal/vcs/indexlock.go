// The daemon shares .git/index with every other git client on the machine —
// the operator's CLI above all — and go-git speaks none of git's concurrency
// protocol: it ignores index.lock and rewrites the index file in place. Left
// that way, a daemon commit cycle that overlaps a CLI `git rm` silently
// discards the CLI's staging: the daemon reads the index, the CLI stages,
// the daemon writes what it read. That exact clobber fired twice during the
// 2026-08-11 rehoming pass.
//
// The repair is to speak the protocol rather than invent one:
//
//  1. Every index-mutating operation (Commit, SweepDeletions) holds
//     .git/index.lock for its whole read-modify-write span — the same file
//     C-git takes, so mutual exclusion extends to every git client rather
//     than only between the daemon's own goroutines.
//  2. The index itself is written to a temp file and renamed into place, so
//     a concurrent reader (`git status` in a shell) sees the old index or
//     the new one and never a torn one. C-git gets this for free because it
//     accumulates the new index IN index.lock and renames that; here the
//     lock is held across a longer span than one write, so the payload
//     travels in its own temp file and the lock stays a pure mutex.
//
// The lock is never stolen. A lock that will not clear means another git
// process is mid-operation or died mid-operation, and both are conditions a
// human should look at; the daemon retries with backoff, then skips the
// cycle loudly and lets the next debounce try again.
package vcs

import (
	"bufio"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/go-git/go-billy/v5/osfs"
	"github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/plumbing/cache"
	formatindex "github.com/go-git/go-git/v5/plumbing/format/index"
	"github.com/go-git/go-git/v5/storage/filesystem"
)

// DefaultIndexLockWait bounds how long a mutating operation waits for another
// git client to release index.lock before giving up the cycle. CLI operations
// hold the lock for milliseconds; anything approaching this bound is either a
// very large operation or a stale lock, and both deserve a loud skip rather
// than a silent queue.
const DefaultIndexLockWait = 10 * time.Second

// resolveGitDir finds the real git directory for a worktree root: `.git` as a
// directory is itself the git dir, and `.git` as a file is git's pointer
// (`gitdir: <path>`), the separate-git-dir shape the vault uses so the object
// database stays out of the Drive sync set.
func resolveGitDir(root string) (string, error) {
	dotgit := filepath.Join(root, ".git")
	fi, err := os.Stat(dotgit)
	if err != nil {
		return "", err
	}
	if fi.IsDir() {
		return dotgit, nil
	}
	raw, err := os.ReadFile(dotgit)
	if err != nil {
		return "", err
	}
	line := strings.TrimSpace(string(raw))
	const prefix = "gitdir:"
	if !strings.HasPrefix(line, prefix) {
		return "", fmt.Errorf(".git file at %s does not carry a gitdir pointer", root)
	}
	target := strings.TrimSpace(strings.TrimPrefix(line, prefix))
	if !filepath.IsAbs(target) {
		target = filepath.Join(root, target)
	}
	return target, nil
}

// openAtomic opens the repository the way PlainOpen would, but with the index
// write path replaced by the atomic one above. Returns the repository and the
// resolved git directory. Any resolution surprise falls back to a plain open —
// today's behavior, working but unguarded — rather than failing the daemon.
func openAtomic(root string) (*git.Repository, string, error) {
	gitDir, err := resolveGitDir(root)
	if err != nil {
		repo, perr := git.PlainOpen(root)
		return repo, "", errorsJoin(err, perr)
	}
	st := filesystem.NewStorage(osfs.New(gitDir), cache.NewObjectLRUDefault())
	wrapped := &atomicIndexStorage{Storage: st, gitDir: gitDir}
	repo, err := git.Open(wrapped, osfs.New(root))
	if err != nil {
		return nil, "", err
	}
	return repo, gitDir, nil
}

func errorsJoin(a, b error) error {
	if b == nil {
		return a
	}
	return b
}

// atomicIndexStorage overrides exactly one verb of the filesystem storer: the
// index write. Everything go-git stages or commits funnels through SetIndex,
// so replacing it here covers wt.Add, wt.Commit, the deletion quarantine and
// the NFC recompose without touching any of them.
type atomicIndexStorage struct {
	*filesystem.Storage
	gitDir string
}

func (s *atomicIndexStorage) SetIndex(idx *formatindex.Index) (err error) {
	tmp, err := os.CreateTemp(s.gitDir, "index.agentmd-*")
	if err != nil {
		return fmt.Errorf("index temp file: %w", err)
	}
	tmpName := tmp.Name()
	defer func() {
		if err != nil {
			tmp.Close()
			os.Remove(tmpName)
		}
	}()
	bw := bufio.NewWriter(tmp)
	if err = formatindex.NewEncoder(bw).Encode(idx); err != nil {
		return fmt.Errorf("encode index: %w", err)
	}
	if err = bw.Flush(); err != nil {
		return fmt.Errorf("flush index: %w", err)
	}
	if err = tmp.Sync(); err != nil {
		return fmt.Errorf("sync index: %w", err)
	}
	if err = tmp.Close(); err != nil {
		return fmt.Errorf("close index temp: %w", err)
	}
	if err = os.Rename(tmpName, filepath.Join(s.gitDir, "index")); err != nil {
		return fmt.Errorf("rename index into place: %w", err)
	}
	return nil
}

// lockIndex takes git's own index.lock and returns the release function. It
// waits out a busy lock with backoff up to r.lockWait, and refuses — loudly,
// naming the lock and its age — rather than stealing one that will not clear.
//
// Reentrancy is deliberately absent: r.mu already serializes the daemon's own
// goroutines, so the only contention here is with other processes, and a
// second acquisition from the same goroutine is a bug worth deadlocking on in
// a test rather than silently permitting.
func (r *Repo) lockIndex() (func(), error) {
	if r.gitDir == "" {
		// Unwrapped fallback open — no known git dir to lock in. Degrades to
		// the old unguarded behavior rather than refusing to commit at all.
		return func() {}, nil
	}
	lockPath := filepath.Join(r.gitDir, "index.lock")
	wait := r.lockWait
	if wait <= 0 {
		wait = DefaultIndexLockWait
	}
	deadline := time.Now().Add(wait)
	backoff := 25 * time.Millisecond
	for {
		f, err := os.OpenFile(lockPath, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o644)
		if err == nil {
			fmt.Fprintf(f, "held by agentmd (pid %d)\n", os.Getpid())
			f.Close()
			return func() { os.Remove(lockPath) }, nil
		}
		if !errors.Is(err, fs.ErrExist) {
			return nil, fmt.Errorf("index.lock at %s: %w", lockPath, err)
		}
		if time.Now().After(deadline) {
			age := "unknown age"
			if fi, statErr := os.Stat(lockPath); statErr == nil {
				age = time.Since(fi.ModTime()).Round(time.Second).String() + " old"
			}
			return nil, fmt.Errorf(
				"another git process holds %s (%s); waited %s. Not stealing it: "+
					"if a git command is running, let it finish — if nothing is, "+
					"the lock is stale from a crashed process and safe to remove "+
					"by hand", lockPath, age, wait)
		}
		time.Sleep(backoff)
		if backoff < 250*time.Millisecond {
			backoff *= 2
		}
	}
}
