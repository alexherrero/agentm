// The daemon reads the vault's objects through go-git, and go-git v5.19.2
// reads less of an object database than git does. Two gaps matter here, both
// in its filesystem storage:
//
//   - It lists only packs named `pack-<hash>.pack` (dotgit.go, objectPacks),
//     and opens a pack by rebuilding that name. CLI git reads any `*.pack`
//     with an index beside it. git's loose-objects maintenance writes
//     `loose-<hash>.pack`, and once a later repack prunes the loose copies,
//     those objects exist for every git client on the machine except this one.
//   - It loads the list of packs once per process and keeps it (object.go,
//     requireIndex). A `git gc` run by anything else moves objects into a new
//     pack and deletes the old one, and a long-running daemon goes on looking
//     in packs that are gone.
//
// On 2026-10-04 the first gap stopped every commit for eleven hours. A restart
// fixes the second gap and not the first, which is why the restart didn't help.
//
// The repair works around the library rather than forking it. On a missing
// object the repository is reopened, which re-reads the pack list. If a pack
// go-git can't list is present, CLI git first folds every object into one
// `pack-` pack with `git repack -a -d --keep-unreachable`, under index.lock,
// and the distinct object count is checked before and after. The operation is
// retried once. Renaming a `loose-` pack to `pack-` was rejected: the name is
// git's to give, and a renamed pack is a state no git tool produced.
package vcs

import (
	"bytes"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/go-git/go-git/v5/plumbing"
	"github.com/go-git/go-git/v5/storage/filesystem/dotgit"
)

// RepackRetryAfter is how long a failed repack is left before the daemon tries
// another. The retry of the operation that hit the missing object still
// happens once on every call; only the repack itself waits, so a missing git
// binary costs one warning an hour rather than one per commit cycle. The stall
// stays visible on the status surface meanwhile.
const RepackRetryAfter = time.Hour

// missingObject reports whether err is go-git failing to find an object or a
// pack it expected — the symptom of both gaps above. go-git doesn't always
// keep the sentinel wrapped on the way out, so its message is matched too.
func missingObject(err error) bool {
	if err == nil {
		return false
	}
	if errors.Is(err, plumbing.ErrObjectNotFound) || errors.Is(err, dotgit.ErrPackfileNotFound) {
		return true
	}
	msg := err.Error()
	return strings.Contains(msg, plumbing.ErrObjectNotFound.Error()) ||
		strings.Contains(msg, dotgit.ErrPackfileNotFound.Error())
}

// unlistedPacks returns the packs in objects/pack that CLI git reads and
// go-git doesn't: a `*.pack` with its `.idx` beside it, under a name that
// doesn't start with `pack-`. Dot-files are skipped, because git writes a
// pack in progress as `.tmp-<pid>-pack-<hash>.pack` and a repack running in
// another process is not a reason to start a second one.
func unlistedPacks(gitDir string) ([]string, error) {
	packDir := filepath.Join(gitDir, "objects", "pack")
	entries, err := os.ReadDir(packDir)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, nil
		}
		return nil, err
	}
	var out []string
	for _, e := range entries {
		name := e.Name()
		if e.IsDir() || !strings.HasSuffix(name, ".pack") ||
			strings.HasPrefix(name, "pack-") || strings.HasPrefix(name, ".") {
			continue
		}
		idx := strings.TrimSuffix(name, ".pack") + ".idx"
		if _, err := os.Stat(filepath.Join(packDir, idx)); err != nil {
			continue
		}
		out = append(out, name)
	}
	return out, nil
}

// SetLogger gives the repository somewhere to say what it repaired. Without
// one, it says so on the default logger rather than not at all.
func (r *Repo) SetLogger(l *slog.Logger) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.log = l
}

func (r *Repo) logger() *slog.Logger {
	if r.log != nil {
		return r.log
	}
	return slog.Default()
}

// RepairUnlistedPacks repacks the object database if it holds a pack go-git
// can't list, so the daemon doesn't have to fail a commit first to find out.
// The daemon calls it once after Open. It reports whether a repack ran.
func (r *Repo) RepairUnlistedPacks() (bool, error) {
	if !r.available || r.gitDir == "" {
		return false, nil
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	packs, err := unlistedPacks(r.gitDir)
	if err != nil || len(packs) == 0 {
		return false, err
	}
	unlock, err := r.lockIndex()
	if err != nil {
		return false, fmt.Errorf("repack skipped: %w", err)
	}
	defer unlock()
	if err := r.repackHeld(packs, "repaired unlisted packs"); err != nil {
		return false, err
	}
	r.reopenHeld()
	return true, nil
}

// heal is the response to a missing object: repack if a pack go-git can't
// list is present, then reopen so the pack list is read again. The caller
// retries its operation once afterwards, whatever happened here — a failed
// repack still leaves the reopen, which is the whole fix for a pack list
// gone stale.
//
// Caller holds r.mu. haveLock says whether it also holds index.lock; the
// repack needs it, and takes it here when the caller doesn't.
func (r *Repo) heal(cause error, haveLock bool) {
	log := r.logger()
	if r.gitDir != "" {
		packs, err := unlistedPacks(r.gitDir)
		switch {
		case err != nil:
			log.Warn("could not list object packs", "err", err)
		case len(packs) == 0:
		case !r.repackFailedAt.IsZero() && time.Since(r.repackFailedAt) < RepackRetryAfter:
			log.Debug("unlisted packs still present; repack retry not due",
				"packs", packs, "last_failure", r.repackFailedAt)
		default:
			unlock := func() {}
			lockErr := error(nil)
			if !haveLock {
				unlock, lockErr = r.lockIndex()
			}
			if lockErr != nil {
				log.Warn("could not repack unlisted packs", "packs", packs, "err", lockErr)
			} else {
				if err := r.repackHeld(packs, "repaired unlisted packs"); err != nil {
					r.repackFailedAt = time.Now()
					log.Error("could not repack unlisted packs", "packs", packs, "err", err)
				}
				unlock()
			}
		}
	}
	r.reopenHeld()
	log.Info("reopened the repository after a missing object", "cause", cause)
}

// repackHeld folds every object, reachable or not, into one `pack-` pack with
// CLI git, and refuses to report success if the distinct object count fell.
// done is the log message for a repack that worked.
//
// Caller holds r.mu and index.lock.
func (r *Repo) repackHeld(packs []string, done string) error {
	start := time.Now()
	before, err := r.countObjects()
	if err != nil {
		return fmt.Errorf("count objects before repack: %w", err)
	}
	if out, err := r.gitCLI("repack", "-a", "-d", "-q", "--keep-unreachable"); err != nil {
		return fmt.Errorf("git repack: %w: %s", err, strings.TrimSpace(string(out)))
	}
	after, err := r.countObjects()
	if err != nil {
		return fmt.Errorf("count objects after repack: %w", err)
	}
	if after < before {
		return fmt.Errorf("the object count fell from %d to %d across the repack", before, after)
	}
	r.repackFailedAt = time.Time{}
	r.logger().Info(done, "packs", packs,
		"objects_before", before, "objects_after", after,
		"took", time.Since(start).Round(time.Millisecond).String())
	return nil
}

// countObjects is the number of distinct objects git can read in the
// repository, loose or packed, reachable or not. Counting distinct names
// matters: an object held both loose and in a pack, or in two packs, is one
// object, and a repack that keeps it once has lost nothing.
func (r *Repo) countObjects() (int, error) {
	out, err := r.gitCLI("cat-file", "--batch-all-objects", "--batch-check=%(objectname)")
	if err != nil {
		return 0, fmt.Errorf("%w: %s", err, strings.TrimSpace(string(out)))
	}
	return bytes.Count(out, []byte{'\n'}), nil
}

// gitCLI runs CLI git against the repository's git directory. Only stdout is
// returned on success; on failure it carries stderr too, for the log.
func (r *Repo) gitCLI(args ...string) ([]byte, error) {
	cmd := exec.Command("git", append([]string{"--git-dir=" + r.gitDir}, args...)...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Run(); err != nil {
		return append(stdout.Bytes(), stderr.Bytes()...), err
	}
	return stdout.Bytes(), nil
}

// reopenHeld swaps in a freshly opened repository, which is how go-git is
// made to read its pack list again. A failed reopen keeps the old handle:
// a stale view is still better than none.
//
// Caller holds r.mu.
func (r *Repo) reopenHeld() {
	repo, gitDir, err := openAtomic(r.root)
	if err != nil {
		r.logger().Warn("could not reopen the repository", "err", err)
		return
	}
	r.repo, r.gitDir = repo, gitDir
	r.precompose = resolvePrecompose(repo)
}

// lockOwner is what the daemon writes into index.lock while it holds it. The
// shape is a contract: it is the only content the daemon will ever remove a
// lock for, and only when the pid it names is dead.
const lockOwner = "held by agentmd (pid %d)\n"

// ownStaleLockPid reads a lock file and returns the pid it names if it is the
// daemon's own content and that process is no longer running. Any other
// content, a live pid, this process's pid, or a pid whose state can't be
// read all answer false.
func ownStaleLockPid(lockPath string) (int, bool) {
	raw, err := os.ReadFile(lockPath)
	if err != nil {
		return 0, false
	}
	body := strings.TrimSuffix(string(raw), "\n")
	const prefix, suffix = "held by agentmd (pid ", ")"
	if !strings.HasPrefix(body, prefix) || !strings.HasSuffix(body, suffix) {
		return 0, false
	}
	digits := strings.TrimSuffix(strings.TrimPrefix(body, prefix), suffix)
	pid, err := strconv.Atoi(digits)
	if err != nil || pid <= 0 || strconv.Itoa(pid) != digits || pid == os.Getpid() {
		return 0, false
	}
	alive, known := processAlive(pid)
	if !known || alive {
		return 0, false
	}
	return pid, true
}
