package dreaming

import (
	"sort"
	"strings"
)

// foldFormerName rewrites an entity of a renamed repository to the repository
// it is now: `release:alexherrero/agentic-harness@v2.0.0` is agentm's.
func foldFormerName(uri string, former map[string]string) string {
	if len(former) == 0 {
		return uri
	}
	kind, id, ok := strings.Cut(uri, ":")
	if !ok {
		return uri
	}
	repo, rest, sep := id, "", ""
	switch kind {
	case "issue":
		repo, rest, _ = strings.Cut(id, "#")
		sep = "#"
	case "release":
		repo, rest, _ = strings.Cut(id, "@")
		sep = "@"
	case "repo":
	default:
		return uri
	}
	now, renamed := former[repo]
	if !renamed {
		return uri
	}
	return kind + ":" + now + sep + rest
}

// formerAliases is a repository page's former names, full and short, so a
// question that names the old repository still finds the page.
func formerAliases(repo string, former map[string]string) []string {
	var out []string
	for old, now := range former {
		if now != repo {
			continue
		}
		out = append(out, old)
		if _, name, ok := strings.Cut(old, "/"); ok {
			out = append(out, name)
		}
	}
	sort.Strings(out)
	return out
}

// sourceOf is the source a mention counts as toward a page's bar: a task's
// folder, open or completed, counts once however many of its notes name the
// entity, because a task's progress log restates its plan's status line. Any
// other note is its own source.
func sourceOf(rel string) string {
	parts := strings.Split(rel, "/")
	for i := 0; i+2 < len(parts); i++ {
		if strings.EqualFold(parts[i], "tasks") {
			return strings.Join(parts[:i+2], "/")
		}
	}
	return rel
}

// distinctSources counts a page's mentions by source.
func distinctSources(ms map[string]mention) int {
	seen := map[string]bool{}
	for rel := range ms {
		seen[sourceOf(rel)] = true
	}
	return len(seen)
}
