// Package buildinfo says which source a daemon binary was built from, and how
// that source stands against a clone on this machine.
//
// `agentmd status` printed `0.1.0-dev` whatever was built, so a daemon behind
// `main` read exactly like a current one (#819). A stale daemon is a daemon
// still carrying the bugs its fixes addressed, and the only way to tell was to
// remember to look.
//
// There are two stamps, and the build scripts' wins. Go records
// `vcs.revision` on its own, but it looks for a `.git` directory, so a build
// from a git worktree walks past the worktree's `.git` file to the clone that
// encloses it and stamps that clone's HEAD and dirty state. The build scripts
// know which checkout they are building, so they say it, through
// scripts/daemon-ldflags.sh. A plain `go build` still gets Go's stamp, and the
// Stamp field says which one a reader is looking at.
package buildinfo

import (
	"fmt"
	"os/exec"
	"runtime/debug"
	"strconv"
	"strings"
)

// Set with -X by scripts/daemon-ldflags.sh. Empty in any other build.
var (
	commit   string
	modified string
)

// Where Info.Commit came from.
const (
	// StampScript is the build script's own stamp: the checkout it built.
	StampScript = "build-script"
	// StampGo is Go's vcs.revision. From a worktree it names the enclosing
	// clone's HEAD, not the worktree's.
	StampGo = "go-vcs"
	// StampNone means nothing recorded a commit: a build outside any clone.
	StampNone = "none"
)

// Info is one binary's account of its source.
type Info struct {
	// Commit is the full commit the binary was built from, or empty.
	Commit string `json:"commit,omitempty"`
	// Modified says the build carried uncommitted changes. From the build
	// script it means changes under daemon/, the only tree compiled in. Go's own
	// vcs.modified is repository-wide, so a stray file anywhere sets it.
	Modified bool `json:"modified"`
	// Stamp is StampScript, StampGo or StampNone.
	Stamp string `json:"stamp"`
}

// Read is this binary's own Info.
func Read() Info {
	if commit != "" {
		return Info{Commit: commit, Modified: modified == "true", Stamp: StampScript}
	}
	bi, ok := debug.ReadBuildInfo()
	if !ok {
		return Info{Stamp: StampNone}
	}
	return fromSettings(bi.Settings)
}

func fromSettings(settings []debug.BuildSetting) Info {
	out := Info{Stamp: StampNone}
	for _, s := range settings {
		switch s.Key {
		case "vcs.revision":
			out.Commit, out.Stamp = s.Value, StampGo
		case "vcs.modified":
			out.Modified = s.Value == "true"
		}
	}
	if out.Commit == "" {
		out.Modified = false
	}
	return out
}

// Short is the commit cut to eight characters, or "unknown".
func (i Info) Short() string {
	if i.Commit == "" {
		return "unknown"
	}
	s := short(i.Commit)
	if i.Modified {
		s += "+modified"
	}
	return s
}

func short(sha string) string {
	if len(sha) > 8 {
		return sha[:8]
	}
	return sha
}

// Clone is how a clone's HEAD stands against a build's commit.
type Clone struct {
	Dir  string
	Head string
	// Ahead counts commits HEAD has that the build lacks; Behind the reverse.
	Ahead, Behind int
	// DaemonAhead counts the Ahead commits that touch daemon/: the ones a
	// rebuild would compile in. The rest change nothing in the binary.
	DaemonAhead int
	// Err says why the comparison could not be made. The other fields are then
	// whatever was read before it failed.
	Err string
}

// CompareClone reads dir's HEAD and counts the commits between it and commit.
// Local only: it reads what the clone already has and never fetches.
func CompareClone(dir, commit string) Clone {
	out := Clone{Dir: dir}
	head, err := git(dir, "rev-parse", "HEAD")
	if err != nil {
		out.Err = "not a readable git clone"
		return out
	}
	out.Head = head
	if commit == "" {
		out.Err = "the build recorded no commit"
		return out
	}
	if _, err := git(dir, "cat-file", "-e", commit+"^{commit}"); err != nil {
		out.Err = "the build's commit " + short(commit) + " is not in this clone"
		return out
	}
	counts, err := git(dir, "rev-list", "--left-right", "--count", commit+"...HEAD")
	if err != nil {
		out.Err = "git could not count the commits between them"
		return out
	}
	fields := strings.Fields(counts)
	if len(fields) != 2 {
		out.Err = "git could not count the commits between them"
		return out
	}
	out.Behind, _ = strconv.Atoi(fields[0])
	out.Ahead, _ = strconv.Atoi(fields[1])
	if out.Ahead > 0 {
		n, err := git(dir, "rev-list", "--count", commit+"..HEAD", "--", "daemon")
		if err != nil {
			out.Err = "git could not count the daemon/ commits"
			return out
		}
		out.DaemonAhead, _ = strconv.Atoi(n)
	}
	return out
}

func git(dir string, args ...string) (string, error) {
	cmd := exec.Command("git", append([]string{"-C", dir}, args...)...)
	out, err := cmd.Output()
	if err != nil {
		return "", fmt.Errorf("git %s: %w", strings.Join(args, " "), err)
	}
	return strings.TrimSpace(string(out)), nil
}
