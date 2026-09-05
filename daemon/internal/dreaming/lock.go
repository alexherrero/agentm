// Package dreaming is the second binary's engine: the dreaming pass that runs
// triggered-and-exits under a dual gate, takes a lock, journals every
// mutation before it makes it, and resumes from that journal after a crash.
//
// Filing v2 part 6 (task 3, the scaffold). The Python dreaming layer
// (harness/skills/memory/scripts/dream.py) keeps running beside this binary;
// the binary lands job by job, report-only by default, until recorded-output
// fixtures prove parity. Nothing here is a resident process: `Run` does one
// pass and returns.
package dreaming

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// ErrHeld is returned when the lock is held by a live holder.
type ErrHeld struct {
	Dir string
	Age time.Duration
}

func (e *ErrHeld) Error() string {
	return fmt.Sprintf("lock %s is held (last heartbeat %s ago)", e.Dir, e.Age.Round(time.Millisecond))
}

// Lock is a directory lock in the protocol scripts/vault_lock.py speaks:
// the lock is a directory created with mkdir (atomic on every filesystem
// that matters), liveness is the directory's own mtime — a heartbeat touches
// it every `stale/2` — and a holder whose heartbeat is older than `stale` is
// dead and may be taken over. The protocol itself carries no PID, so a
// Python holder killed with -9 leaves a frozen mtime and is taken over only
// after the stale window; a Go holder also writes its pid beside the
// protocol (see Acquire), so a Go taker can take over a dead one at once.
//
// Two locks use it: the pass's singleton (SingletonLockDir — a second start
// is refused) and the vault mutex the Python writers share (VaultLockDir —
// the same sha256-of-realpath key, so a mutation here waits for a capture
// there and the other way round).
type Lock struct {
	Dir   string
	stale time.Duration
	stop  chan struct{}
	done  chan struct{}
	once  sync.Once
}

// VaultLockDir is `<XDG_CACHE_HOME or ~/.cache>/agentm/locks/<sha256(realpath(vault))>/lock`
// — byte for byte what vault_lock.py's `_lockdir_for` derives, so the two
// implementations contend on the same directory.
func VaultLockDir(vault string) (string, error) {
	abs, err := filepath.Abs(vault)
	if err != nil {
		return "", err
	}
	real, err := filepath.EvalSymlinks(abs)
	if err != nil {
		real = abs
	}
	sum := sha256.Sum256([]byte(real))
	root := os.Getenv("XDG_CACHE_HOME")
	if root == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		root = filepath.Join(home, ".cache")
	}
	return filepath.Join(root, "agentm", "locks", hex.EncodeToString(sum[:]), "lock"), nil
}

// SingletonLockDir is the pass's own lock, under the engine state dir: one
// dreaming pass per machine at a time.
func SingletonLockDir(engineStateDir string) string {
	return filepath.Join(engineStateDir, "dreaming", "lock")
}

// Acquire takes the lock at `dir`, waiting up to `wait` for a live holder to
// release it and taking over a holder whose heartbeat is older than `stale`.
// A holder still alive past the wait returns *ErrHeld.
func Acquire(dir string, stale, wait time.Duration) (*Lock, error) {
	if stale <= 0 {
		stale = 30 * time.Second
	}
	if err := os.MkdirAll(filepath.Dir(dir), 0o755); err != nil {
		return nil, err
	}
	deadline := time.Now().Add(wait)
	backoff := 25 * time.Millisecond
	for {
		err := os.Mkdir(dir, 0o755)
		if err == nil {
			l := &Lock{Dir: dir, stale: stale, stop: make(chan struct{}), done: make(chan struct{})}
			// The holder's pid, beside the protocol rather than in it: the
			// Python writers never read it, but a Go taker can ask the kernel
			// whether the holder is alive and take over a kill -9'd pass at
			// once instead of after the stale window. A reused pid only
			// delays the takeover to the heartbeat rule; it never steals a
			// live lock, because a live holder keeps its heartbeat fresh.
			_ = os.WriteFile(filepath.Join(dir, "pid"), []byte(strconv.Itoa(os.Getpid())), 0o644)
			go l.heartbeat()
			return l, nil
		}
		if !errors.Is(err, os.ErrExist) {
			return nil, err
		}
		age, ok := lockAge(dir)
		if !ok {
			continue // vanished between the mkdir and the stat: retry at once
		}
		if age > stale || holderDead(dir) {
			// A dead holder. Remove and retry; a concurrent taker loses the
			// mkdir race cleanly.
			_ = os.RemoveAll(dir)
			continue
		}
		if time.Now().After(deadline) {
			return nil, &ErrHeld{Dir: dir, Age: age}
		}
		time.Sleep(backoff)
		if backoff < 250*time.Millisecond {
			backoff *= 2
		}
	}
}

// holderDead reports whether the pid the lock names is gone. A lock with
// no pid file (a Python holder) or an unreadable one is not known dead.
func holderDead(dir string) bool {
	blob, err := os.ReadFile(filepath.Join(dir, "pid"))
	if err != nil {
		return false
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(blob)))
	if err != nil || pid <= 0 {
		return false
	}
	alive, known := processAlive(pid)
	return known && !alive
}

func lockAge(dir string) (time.Duration, bool) {
	st, err := os.Stat(dir)
	if err != nil {
		return 0, false
	}
	return time.Since(st.ModTime()), true
}

func (l *Lock) heartbeat() {
	defer close(l.done)
	t := time.NewTicker(l.stale / 2)
	defer t.Stop()
	for {
		select {
		case <-l.stop:
			return
		case <-t.C:
			now := time.Now()
			_ = os.Chtimes(l.Dir, now, now)
		}
	}
}

// Release stops the heartbeat and removes the lock directory. Idempotent.
func (l *Lock) Release() {
	l.once.Do(func() {
		close(l.stop)
		<-l.done
		_ = os.RemoveAll(l.Dir)
	})
}
