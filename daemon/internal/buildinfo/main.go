package buildinfo

import (
	"strconv"
	"strings"
)

// Main is how a clone's HEAD stands against its local origin/main (#815).
//
// Every scheduled job runs from the primary clone, so a clone missing merged
// commits runs the bugs they fixed and reports healthy. The operator's ruling
// of 2026-10-23: the jobs run anyway, and the gap is said loudly, here, in the
// runner's cycle record, the doctor's install-head row and the morning note.
// The same reading as harness/skills/memory/scripts/clone_drift.py, which the
// Python surfaces share; this is the Go copy for `agentmd status`.
//
// Local only: the origin/main the last fetch left, never a fetch.
type Main struct {
	// Branch is the branch HEAD is on, empty when detached.
	Branch string
	// Behind counts merged commits HEAD lacks; Ahead the commits it has that
	// origin/main does not.
	Behind, Ahead int
	// NewestMissing and NewestMissingSubject name the newest commit on
	// origin/main that HEAD lacks.
	NewestMissing, NewestMissingSubject string
	// Err says why the comparison could not be made.
	Err string
}

const remoteMain = "refs/remotes/origin/main"

// CompareMain reads dir's HEAD against its local origin/main.
func CompareMain(dir string) Main {
	var out Main
	if _, err := git(dir, "rev-parse", "HEAD"); err != nil {
		out.Err = "not a readable git clone"
		return out
	}
	out.Branch, _ = git(dir, "symbolic-ref", "--quiet", "--short", "HEAD")
	if _, err := git(dir, "rev-parse", "--verify", "--quiet", remoteMain); err != nil {
		out.Err = "no origin/main ref to compare with"
		return out
	}
	counts, err := git(dir, "rev-list", "--left-right", "--count", remoteMain+"...HEAD")
	fields := strings.Fields(counts)
	if err != nil || len(fields) != 2 {
		out.Err = "git could not count the commits between HEAD and origin/main"
		return out
	}
	out.Behind, _ = strconv.Atoi(fields[0])
	out.Ahead, _ = strconv.Atoi(fields[1])
	if out.Behind > 0 {
		if line, err := git(dir, "log", "-1", "--format=%H%x09%s", "HEAD.."+remoteMain); err == nil {
			out.NewestMissing, out.NewestMissingSubject, _ = strings.Cut(line, "\t")
		}
	}
	return out
}

// Sentence is the loud line for a clone behind main, empty when it is not.
func (m Main) Sentence() string {
	if m.Behind == 0 {
		return ""
	}
	s := "the primary clone is " + plural(m.Behind, "commit") + " behind main"
	if m.NewestMissing != "" {
		s += "; the newest it lacks is " + short(m.NewestMissing)
		if m.NewestMissingSubject != "" {
			s += ` "` + m.NewestMissingSubject + `"`
		}
	}
	return s
}

// Note is the quiet line for a clone on a branch or ahead of main: reported,
// never an error, because a session working mid-branch is ordinary.
func (m Main) Note() string {
	var bits []string
	if m.Branch != "" && m.Branch != "main" {
		bits = append(bits, "on branch `"+m.Branch+"`")
	}
	if m.Ahead > 0 {
		bits = append(bits, plural(m.Ahead, "commit")+" ahead of main")
	}
	if len(bits) == 0 {
		return ""
	}
	return "the primary clone is " + strings.Join(bits, " and ")
}

func plural(n int, noun string) string {
	if n == 1 {
		return "1 " + noun
	}
	return strconv.Itoa(n) + " " + noun + "s"
}
