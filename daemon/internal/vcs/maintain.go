// The daemon owns the vault repository's packing. Left to git, packing happens
// whenever any client's command decides it is due: `git fetch` and `git
// commit` run `git maintenance run --auto` after their own work, and a session
// or job can run maintenance outright. One of those runs wrote the `loose-`
// pack that stopped every commit on 2026-10-04 (heal.go has that story).
//
// So the repository's own config turns automatic maintenance off for every
// client at once (gc.auto=0, maintenance.auto=false), and the daemon does the
// packing itself, once a day, in the one form go-git reads: a lossless
// `git repack -a -d --keep-unreachable` into a single `pack-` pack, under
// index.lock. Changing the backup job instead would have covered one client
// out of many.
package vcs

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// MaintenanceEvery is how often the daemon folds the vault's loose objects
// and extra packs into one pack.
const MaintenanceEvery = 24 * time.Hour

// ownedConfig is the repo-local config the daemon keeps, and nothing else.
var ownedConfig = []struct{ key, want string }{
	{"gc.auto", "0"},
	{"maintenance.auto", "false"},
}

// OwnMaintenance makes sure the vault repository's own config leaves packing to
// the daemon. It reads and writes only the two keys in ownedConfig, only in the
// repository's config file (`--local`), and logs only when it changes one. The
// global and system config are never touched.
func (r *Repo) OwnMaintenance() error {
	if !r.available || r.gitDir == "" {
		return nil
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, c := range ownedConfig {
		out, err := r.gitCLI("config", "--local", "--get", c.key)
		was := strings.TrimSpace(string(out))
		switch {
		case err == nil && was == c.want:
			continue
		case err != nil && unsetConfig(err):
			was = "(unset)"
		case err != nil:
			return fmt.Errorf("read %s: %w: %s", c.key, err, was)
		}
		if out, err := r.gitCLI("config", "--local", c.key, c.want); err != nil {
			return fmt.Errorf("set %s: %w: %s", c.key, err, strings.TrimSpace(string(out)))
		}
		r.logger().Info("set repo-local git config", "key", c.key, "was", was, "now", c.want)
	}
	return nil
}

// unsetConfig is `git config --get` saying the key isn't set, which it does
// with exit status 1 and nothing else.
func unsetConfig(err error) bool {
	var exit *exec.ExitError
	return errors.As(err, &exit) && exit.ExitCode() == 1
}

// MaintainIfDue runs the daemon's daily repack when one is due and there is
// something to fold: any loose object, or more than one pack. It reports
// whether a repack ran.
//
// Due means a day since the newest `pack-*.idx` was written. The index file is
// the clock because git writes it once, with its pack; a pack's own mtime
// moves whenever a client freshens an object in it. Reading the time from the
// repository means a restart doesn't reset it and there is no state file. A
// repository with no pack yet counts its day from the first check, so a
// freshly started daemon never repacks on its first tick. A busy index.lock
// leaves the repack due, so the next tick tries again.
func (r *Repo) MaintainIfDue(now time.Time) (bool, error) {
	if !r.available || r.gitDir == "" {
		return false, nil
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.lastMaintained.IsZero() {
		r.lastMaintained = newestPackIndex(r.gitDir)
		if r.lastMaintained.IsZero() {
			r.lastMaintained = now
		}
	}
	if now.Sub(r.lastMaintained) < MaintenanceEvery {
		return false, nil
	}
	loose, packs := objectFiles(r.gitDir)
	if loose == 0 && len(packs) <= 1 {
		r.lastMaintained = now
		return false, nil
	}
	unlock, err := r.lockIndex()
	if err != nil {
		return false, fmt.Errorf("daily repack skipped: %w", err)
	}
	defer unlock()
	r.lastMaintained = now
	if err := r.repackHeld(packs, "daily repack"); err != nil {
		return false, fmt.Errorf("daily repack: %w", err)
	}
	r.reopenHeld()
	return true, nil
}

// newestPackIndex is when the newest pack's index was written, or zero when
// there are no packs.
func newestPackIndex(gitDir string) time.Time {
	matches, _ := filepath.Glob(filepath.Join(gitDir, "objects", "pack", "pack-*.idx"))
	var newest time.Time
	for _, m := range matches {
		if fi, err := os.Stat(m); err == nil && fi.ModTime().After(newest) {
			newest = fi.ModTime()
		}
	}
	return newest
}

// objectFiles counts the loose objects and names the packs, from the
// directory listing alone. Dot-files are a pack another process is still
// writing, and aren't counted.
func objectFiles(gitDir string) (loose int, packs []string) {
	objects := filepath.Join(gitDir, "objects")
	dirs, _ := filepath.Glob(filepath.Join(objects, "[0-9a-f][0-9a-f]"))
	for _, d := range dirs {
		entries, err := os.ReadDir(d)
		if err != nil {
			continue
		}
		loose += len(entries)
	}
	entries, _ := os.ReadDir(filepath.Join(objects, "pack"))
	for _, e := range entries {
		if name := e.Name(); strings.HasSuffix(name, ".pack") && !strings.HasPrefix(name, ".") {
			packs = append(packs, name)
		}
	}
	return loose, packs
}
