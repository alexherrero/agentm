// The vault-level half of the door: which top-level spaces the agent may
// write into at all, before the per-file-class judgment in door.go says
// anything about a path inside one.
//
// The promotion-door doctrine this replaces had one write path into the
// operator's spaces. Filing v2's ruling is finer: each space carries an
// explicit authority level, and the only level that changes within a session
// is Projects/ — the operator grants management of one project for one
// session by saying so, and the grant is session state, never configuration.
//
// # Which direction this fails in
//
// Deny-by-default, same as the per-file-class half: a top-level segment the
// table does not name answers Alignment. The operator ruling that created
// this table named five spaces and the root files; anything else in the
// vault's top level is either new (and gets its level decided in
// conversation, not invented here) or a mistake worth surfacing.
package door

import (
	"fmt"
	"strings"
)

// Level is the write authority a vault space carries.
type Level string

const (
	// AgentOwned — the agent's own half; it writes freely.
	AgentOwned Level = "agent-owned"
	// Shared — agent-maintained, operator co-writes; no grant needed.
	Shared Level = "shared"
	// GrantRequired — agent-managed only under a session grant naming the
	// project. No grant, no write.
	GrantRequired Level = "grant-required"
	// PerTask — the operator's space; a write takes an explicit per-task
	// instruction in conversation, never standing management. To this door,
	// which answers for machinery rather than for conversations, that is
	// always Alignment.
	PerTask Level = "per-task"
	// OperatorOwned — the operator's surface; the agent proposes edits and
	// applies them only on instruction.
	OperatorOwned Level = "operator-owned"
)

// Grants is the set of project slugs this session may manage. The zero value
// grants nothing, which is every session's starting state.
type Grants map[string]bool

// NewGrants normalizes slugs the way the table matches them.
func NewGrants(slugs ...string) Grants {
	g := Grants{}
	for _, s := range slugs {
		s = strings.ToLower(strings.TrimSpace(s))
		if s != "" {
			g[s] = true
		}
	}
	return g
}

// Has reports whether the session holds a grant for the project.
func (g Grants) Has(slug string) bool {
	return g[strings.ToLower(strings.TrimSpace(slug))]
}

// Authority is the vault-level table — space name (top-level directory,
// matched case-insensitively for the same macOS reason InSpace matches that
// way) to the level it carries.
type Authority struct {
	Spaces map[string]Level
	// ProjectRoots is the Roots the per-file-class judgment runs under for a
	// granted project write — the grant opens the space; the face rule inside
	// it still holds.
	ProjectRoots Roots
}

// DefaultAuthority is the five-space table the filing-v2 design locked.
func DefaultAuthority() Authority {
	return Authority{
		Spaces: map[string]Level{
			"agent":     AgentOwned,
			"calendar":  Shared,
			"projects":  GrantRequired,
			"personal":  PerTask,
			"standards": OperatorOwned,
		},
		ProjectRoots: Roots{Projects: "Projects"},
	}
}

// JudgeSpace decides whether the agent may write at a vault-relative path,
// given the grants this session holds.
//
// For a granted Projects write it composes with the per-file-class judgment:
// the grant admits the agent to the project, and door.go's own rule then
// distinguishes maintaining the working bulk (standing) from changing a
// document at the project's visible face (alignment). A grant is management
// authority, not a license to rewrite the face without a word.
func (a Authority) JudgeSpace(rel string, exists bool, grants Grants) Decision {
	clean := strings.Trim(strings.ReplaceAll(rel, "\\", "/"), "/")
	d := Decision{Path: clean}

	if clean == "" || strings.Contains(clean, "..") {
		d.Permission = Alignment
		d.Why = "the path does not resolve to a place inside the vault, and an " +
			"unreadable path is not one to write to without asking"
		return d
	}

	first, rest, _ := strings.Cut(clean, "/")
	level, known := a.Spaces[strings.ToLower(first)]
	if !known {
		// Root files and undeclared spaces alike: the operator's.
		d.Permission = Alignment
		d.Why = fmt.Sprintf("%s is not in the declared address space — the vault "+
			"root and its files are the operator's, and a new space gets its level "+
			"decided in conversation, not invented by a writer", clean)
		return d
	}
	d.Space = first
	d.Level = level

	switch level {
	case AgentOwned:
		d.Permission = Standing
		d.Why = fmt.Sprintf("%s is the agent's own half", first)
	case Shared:
		d.Permission = Standing
		d.Why = fmt.Sprintf("%s is a shared surface the agent maintains", first)
	case GrantRequired:
		slug, _, _ := strings.Cut(rest, "/")
		if slug == "" {
			d.Permission = Alignment
			d.Why = "the projects space itself is the operator's — only the " +
				"operator creates a project"
			return d
		}
		d.Project = slug
		if !grants.Has(slug) {
			d.Permission = Alignment
			d.Why = fmt.Sprintf("no session grant for project %s — the operator "+
				"grants one by saying \"open the files for project %s\", and the "+
				"grant lasts the session", slug, slug)
			return d
		}
		// Granted: the per-file-class rule takes over, judged against the
		// vault-level projects root.
		inner := a.ProjectRoots.Judge(clean, exists)
		inner.Space = first
		inner.Level = level
		inner.Why = fmt.Sprintf("session grant for %s held; %s", slug, inner.Why)
		return inner
	case PerTask:
		d.Permission = Alignment
		d.Why = fmt.Sprintf("%s is the operator's space — a write there takes an "+
			"explicit per-task instruction, never standing management", first)
	case OperatorOwned:
		d.Permission = Alignment
		d.Why = fmt.Sprintf("%s is operator-owned — the agent proposes edits in "+
			"conversation and applies them only on instruction", first)
	default:
		d.Permission = Alignment
		d.Why = fmt.Sprintf("space %s carries unknown level %q, and unknown asks", first, level)
	}
	return d
}
