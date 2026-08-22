// Package door decides where the agent may write inside a project without
// asking, and where it may not.
//
// Only the operator creates a project. That is what makes the door meaningful:
// the folder's existence is the declaration that the project is real, and the
// agent can then recognize which project a piece of work belongs to and file it
// there. Nothing here creates a project.
//
// Inside a declared one the permission is per file class rather than per write,
// which is the whole point — a door that asked about every write would be a
// stream of approvals nobody reads, and a door that asked about none would not
// be a door.
//
//	the project root      a new document is standing; changing an existing
//	                      one takes explicit alignment
//	any subfolder         standing — the agent adds and maintains freely
//
// The root is the project's visible face. Documents that earn top-level
// placement live there in whatever number the project genuinely needs, and the
// working bulk goes into subfolders so the face stays legible.
//
// # Which direction this fails in
//
// Every unknown answers Alignment. A path that does not parse, a shape nobody
// anticipated, a project slug that looks wrong — all of them ask. Answering
// Standing wrongly means the agent quietly rewrites a document the operator
// meant to own; answering Alignment wrongly means it asks a question it did not
// need to. One of those is a conversation and the other is a surprise.
package door

import (
	"fmt"
	"strings"
)

// Permission is what the agent may do at a path.
type Permission string

const (
	// Standing means the agent maintains this without asking.
	Standing Permission = "standing"
	// Alignment means the write needs explicit agreement first.
	Alignment Permission = "alignment"
	// Outside means the path is not inside a declared project or task, so this
	// door has nothing to say about it. Other rules govern; this one abstains
	// rather than inventing an opinion.
	Outside Permission = "outside"
)

// Decision is one path's answer, with the reason attached.
type Decision struct {
	Path string `json:"path"`
	// Project is the slug the path belongs to, when it belongs to one.
	Project string `json:"project,omitempty"`
	// Task is the workbench slug, for a path under `tasks/`.
	Task       string     `json:"task,omitempty"`
	Permission Permission `json:"permission"`
	// Why says how the answer was reached, in the words somebody about to be
	// asked for alignment needs. A door that says no without saying why is one
	// people learn to route around.
	Why string `json:"why"`
}

// MayWrite is the short answer, for a caller that only needs the branch.
func (d Decision) MayWrite() bool { return d.Permission == Standing }

// Roots names where projects and tasks live, relative to the memory root.
//
// Supplied rather than hardcoded because the layout has moved once already —
// `projects/` became `desk/projects/` in the four-space migration — and a
// literal here would have gone quietly wrong that day.
type Roots struct {
	// Projects is the directory holding one folder per declared project.
	Projects string
	// Tasks is the workbench for everything that is not a project.
	Tasks string
}

// DefaultRoots is the layout as it stands.
func DefaultRoots() Roots {
	return Roots{Projects: "desk/projects", Tasks: "desk/tasks"}
}

// Judge decides what the agent may do at `rel`.
//
// `exists` distinguishes creating a root document from changing one, and that
// distinction is the design's own: a project may have as many root documents as
// it needs, with no cap, and it is *modifying or replacing* one that takes
// alignment. A door that asked about creation would make the no-cap rule
// meaningless.
func (r Roots) Judge(rel string, exists bool) Decision {
	clean := strings.Trim(strings.ReplaceAll(rel, "\\", "/"), "/")
	d := Decision{Path: clean}

	if clean == "" || strings.Contains(clean, "..") {
		// A path that will not parse is not a path this door can reason about,
		// and the safe answer to "I do not understand this" is to ask.
		d.Permission = Alignment
		d.Why = "the path does not resolve to a place inside a project, and an " +
			"unreadable path is not one to write to without asking"
		return d
	}

	if slug, rest, ok := under(clean, r.Tasks); ok {
		d.Task = slug
		if rest == "" {
			// The task directory itself. Creating and maintaining a workbench is
			// the agent's own job — the difference between a task and a project
			// is authorship of the container.
			d.Permission = Standing
			d.Why = fmt.Sprintf("the %s workbench is the agent's own container", slug)
			return d
		}
		d.Permission = Standing
		d.Why = fmt.Sprintf("inside the %s workbench, which the agent maintains", slug)
		return d
	}

	slug, rest, ok := under(clean, r.Projects)
	if !ok {
		d.Permission = Outside
		d.Why = "not inside a declared project or task, so the project door has " +
			"nothing to say about it"
		return d
	}
	d.Project = slug

	if rest == "" {
		// The project directory itself. Only the operator creates a project, and
		// that is what makes the door mean anything.
		d.Permission = Alignment
		d.Why = fmt.Sprintf("%s is a project directory, and only the operator "+
			"creates or replaces one", slug)
		return d
	}

	if strings.Contains(rest, "/") {
		d.Permission = Standing
		d.Why = fmt.Sprintf("below %s's root, where the agent adds and maintains "+
			"freely", slug)
		return d
	}

	// A document directly at the project root: the project's visible face.
	if exists {
		d.Permission = Alignment
		d.Why = fmt.Sprintf("%s is a document at %s's root, and changing one takes "+
			"explicit alignment — the root is the project's visible face", rest, slug)
		return d
	}
	d.Permission = Standing
	d.Why = fmt.Sprintf("a new document at %s's root, which the project may have "+
		"as many of as it needs; it is changing an existing one that takes "+
		"alignment", slug)
	return d
}

// under splits `clean` into the slug directly beneath `root` and whatever
// follows, or reports that the path is not beneath it at all.
func under(clean, root string) (slug, rest string, ok bool) {
	root = strings.Trim(root, "/")
	if root == "" {
		return "", "", false
	}
	prefix := root + "/"
	if !strings.HasPrefix(clean, prefix) {
		return "", "", false
	}
	tail := clean[len(prefix):]
	if tail == "" {
		return "", "", false
	}
	slug, rest, found := strings.Cut(tail, "/")
	if slug == "" {
		return "", "", false
	}
	if !found {
		return slug, "", true
	}
	return slug, rest, true
}
