package vcs

import (
	"bufio"
	"bytes"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// The vault backup's standing, read from what the push leaves behind (#818).
//
// The backup is a job outside the daemon: `vault-backup-push`, run every four
// hours by launchd, fast-forwards the backup remote (Gitea on the NAS) to the
// vault's `main`. The daemon commits continuously and never pushes, so nothing
// here knew whether the push still happened. With one live backup leg a dead
// push is zero redundancy, and a stale remote looks just like a current one.
//
// No record of the push's own is needed. A push moves the remote-tracking ref
// and writes an `update by push` line to that ref's reflog, so two readings
// come from the repository: when the newest push was, and how old the oldest
// commit on `main` that the remote lacks is. The second is the one health
// grades. A working job pushes everything it sees, so a commit older than two
// runs was missed twice; and a vault with nothing new is backed up however
// long ago the last push was.
//
// Local only. The reading is what the last push or fetch left, never a call
// to the remote.

// BackupBranch is the branch the backup push sends.
const BackupBranch = "main"

// Backup is one reading of the backup remote's standing.
type Backup struct {
	Remote string
	// Configured says the repository has a remote of that name.
	Configured bool
	// Tracked says the remote-tracking ref exists: something has pushed or
	// fetched at least once.
	Tracked bool
	// LastPush is the newest `update by push` in the ref's reflog; zero when
	// there is none.
	LastPush time.Time
	// Pending counts the commits on main the remote-tracking ref lacks, and
	// OldestPending is the commit time of the oldest of them.
	Pending       int
	OldestPending time.Time
	// Err says why the reading is incomplete.
	Err string
}

// Backup reads the backup remote's standing. A repository the daemon could
// not open, or no remote named, reads as not configured.
func (r *Repo) Backup(remote string) Backup {
	out := Backup{Remote: remote}
	if r == nil || r.gitDir == "" || remote == "" {
		return out
	}
	if msg, err := r.gitCLI("config", "--get", "remote."+remote+".url"); err != nil {
		// Exit 1 is git's "no such key": the remote is not there. Anything
		// else is git failing to read the config, which must read as
		// unknown, never as a vault with no backup to watch.
		var exit *exec.ExitError
		if errors.As(err, &exit) && exit.ExitCode() == 1 {
			return out
		}
		out.Configured = true
		out.Err = "git could not read the repository's config: " + firstLine(string(msg))
		return out
	}
	out.Configured = true

	ref := "refs/remotes/" + remote + "/" + BackupBranch
	span := "refs/heads/" + BackupBranch
	if _, err := r.gitCLI("rev-parse", "--verify", "--quiet", ref); err == nil {
		out.Tracked = true
		span = ref + ".." + span
	}
	out.LastPush = lastPush(filepath.Join(r.gitDir, "logs", filepath.FromSlash(ref)))

	times, err := r.gitCLI("log", "--format=%ct", span)
	if err != nil {
		out.Err = "git could not list the commits the backup lacks: " + firstLine(string(times))
		return out
	}
	lines := strings.Fields(string(times))
	out.Pending = len(lines)
	// The earliest commit time, not the last line: git log's order is the
	// graph's, and commit dates need not follow it.
	for _, l := range lines {
		if sec, err := strconv.ParseInt(l, 10, 64); err == nil {
			if t := time.Unix(sec, 0); out.OldestPending.IsZero() || t.Before(out.OldestPending) {
				out.OldestPending = t
			}
		}
	}
	return out
}

// lastPush is the time of the newest `update by push` line in a reflog, or
// zero. A reflog line is `<old> <new> <name> <email> <unix> <tz>\t<message>`.
func lastPush(reflog string) time.Time {
	blob, err := os.ReadFile(reflog)
	if err != nil {
		return time.Time{}
	}
	var newest time.Time
	sc := bufio.NewScanner(bytes.NewReader(blob))
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		head, msg, ok := strings.Cut(sc.Text(), "\t")
		if !ok || !strings.HasPrefix(msg, "update by push") {
			continue
		}
		f := strings.Fields(head)
		if len(f) < 2 {
			continue
		}
		sec, err := strconv.ParseInt(f[len(f)-2], 10, 64)
		if err != nil {
			continue
		}
		if t := time.Unix(sec, 0); t.After(newest) {
			newest = t
		}
	}
	return newest
}

func firstLine(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		return s[:i]
	}
	return s
}
