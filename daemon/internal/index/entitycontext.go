package index

import (
	"github.com/alexherrero/agentm/daemon/internal/extract"
	"github.com/alexherrero/agentm/daemon/internal/projectbind"
)

// What a note's place tells the extractor (task 179).
//
// A bare `#12` means an issue of the repository the note's project lists, when
// it lists exactly one: 2,145 of the 2,422 bare mentions in the live index sat
// in task files, which carry no `project:` label and are known by their place.
// The projects' repositories come from their `project.yaml`, read again only
// when one of those files changes. A change to one does not re-extract the
// notes already indexed, except a change of floor (task 187): the floors are
// part of the extractor's recorded version, so the next open re-derives every
// note's rows under the new one.

// repoContext is the projects' repositories and floors, and the signature they
// were read at.
type repoContext struct {
	sig      string
	projects map[string][]string
	known    map[string]string
	floors   map[string]int
}

// entityContextLocked is the extractor's context for one note. Callers hold x.mu.
func (x *Index) entityContextLocked(rel, label string) extract.Context {
	if sig := projectbind.Signature(x.vault); x.repos == nil || sig != x.repos.sig {
		projects := projectbind.Repositories(x.vault)
		x.repos = &repoContext{sig: sig, projects: projects, known: projectbind.ShortNames(projects),
			floors: projectbind.IssueFloors(x.vault)}
	}
	ctx := extract.Context{Known: x.repos.known, Floors: x.repos.floors}
	if repos := x.repos.projects[projectbind.NoteProject(rel, label, x.repos.projects)]; len(repos) == 1 {
		ctx.Repo = repos[0]
	}
	return ctx
}
