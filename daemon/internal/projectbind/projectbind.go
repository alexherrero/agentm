// Package projectbind finds the vault project a working folder belongs to.
//
// agentm-vault § Projects and tasks, amended 2026-09-28: a memory is stamped
// with the project whose `projects/<slug>/project.yaml` lists, under
// `code_paths`, the folder its session ran in. The longest listed path wins,
// so a worktree inside a repo's clone binds to the repo's project and a project
// whose code sits inside another's clone binds to itself. A folder no project
// lists binds to none. The Python side keeps the same rule in
// `session_binding.project_for_directory`.
package projectbind

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

// GlobalTypes are the memory types that carry no project unless their writer
// sets one: a rule applies in every project, and a label would age it with one
// project's activity.
var GlobalTypes = map[string]bool{"convention": true, "preference": true}

// readYAML returns a project.yaml's slug and code_paths. The file's schema is
// flat and held by `check-project-yaml`, so a line reader is enough.
func readYAML(path string) (slug string, paths []string) {
	f := readFields(path)
	return f.slug, f.lists["code_paths"]
}

// yamlFields is what the line reader keeps of one project.yaml: its slug, the
// lists a reader asks for, and the bare-issue floor.
type yamlFields struct {
	slug  string
	lists map[string][]string
	// floor is `bare_issue_floor`, 0 when the file sets none or sets something
	// that is not a positive whole number (task 187). The gate refuses the
	// second; the reader just ignores it.
	floor int
}

// listKeys are the list-valued keys the reader collects.
var listKeys = map[string]bool{"code_paths": true, "repositories": true, "former_names": true}

func readFields(path string) yamlFields {
	out := yamlFields{lists: map[string][]string{}}
	raw, err := os.ReadFile(path)
	if err != nil {
		return out
	}
	inList := ""
	unquote := func(v string) string { return strings.Trim(strings.TrimSpace(v), `"'`) }
	for _, line := range strings.Split(strings.ReplaceAll(string(raw), "\r\n", "\n"), "\n") {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		if line[0] != ' ' && line[0] != '\t' && line[0] != '-' {
			key, value, _ := strings.Cut(line, ":")
			key, value = strings.TrimSpace(key), strings.TrimSpace(value)
			inList = ""
			if listKeys[key] && value == "" {
				inList = key
			}
			switch {
			case key == "slug":
				out.slug = unquote(value)
			case key == "bare_issue_floor":
				// YAML allows a comment after the value: `48  # the roadmap's last item + 1`.
				if i := strings.Index(value, " #"); i >= 0 {
					value = value[:i]
				}
				if n, err := strconv.Atoi(unquote(value)); err == nil && n > 0 {
					out.floor = n
				}
			case listKeys[key] && strings.HasPrefix(value, "[") && strings.HasSuffix(value, "]"):
				for _, p := range strings.Split(value[1:len(value)-1], ",") {
					if p = unquote(p); p != "" {
						out.lists[key] = append(out.lists[key], p)
					}
				}
			}
			continue
		}
		if inList != "" && strings.HasPrefix(trimmed, "- ") {
			out.lists[inList] = append(out.lists[inList], unquote(trimmed[2:]))
		}
	}
	return out
}

// eachProject calls fn once for every project.yaml, active projects and the
// finished ones under `projects/completed/`, in path order. The first file to
// claim a slug wins, so a finished copy never shadows the live project.
func eachProject(vaultRoot string, fn func(slug string, fields yamlFields)) {
	if vaultRoot == "" {
		return
	}
	var files []string
	for _, pattern := range []string{
		filepath.Join(vaultRoot, "projects", "*", "project.yaml"),
		filepath.Join(vaultRoot, "projects", "completed", "*", "project.yaml"),
	} {
		found, _ := filepath.Glob(pattern)
		files = append(files, found...)
	}
	sort.Strings(files)
	seen := map[string]bool{}
	for _, f := range files {
		fields := readFields(f)
		slug := fields.slug
		if slug == "" {
			slug = filepath.Base(filepath.Dir(f))
		}
		if seen[slug] {
			continue
		}
		seen[slug] = true
		fn(slug, fields)
	}
}

// repoNames is a list's `owner/repo` entries in lower case, the malformed ones
// left out.
func repoNames(values []string) []string {
	repos := []string{}
	for _, r := range values {
		r = strings.ToLower(strings.Trim(strings.TrimSpace(r), "/"))
		if strings.Count(r, "/") == 1 && !strings.HasPrefix(r, "/") {
			repos = append(repos, r)
		}
	}
	return repos
}

// Repositories maps every project's slug to the repositories its project.yaml
// lists, as `owner/repo` in lower case. A finished project under
// `projects/completed/` is read too: its notes still name its repo. A project
// that lists none maps to an empty list, so a caller can tell "no repository"
// from "no such project".
//
// This is how a bare `#12` in a note learns which repo it means (task 179): a
// note in a project that lists exactly one repository means that repository.
func Repositories(vaultRoot string) map[string][]string {
	out := map[string][]string{}
	eachProject(vaultRoot, func(slug string, fields yamlFields) {
		out[slug] = repoNames(fields.lists["repositories"])
	})
	return out
}

// FormerNames maps a repository's former `owner/repo` to its current one, both
// in lower case, from the `former_names` of every project that lists exactly
// one repository (task 186). GitHub redirects a renamed repository, so an old
// note's `alexherrero/agentic-harness` is today's `alexherrero/agentm`. A former
// name that is some project's current repository is left out: it is not former.
func FormerNames(vaultRoot string) map[string]string {
	out := map[string]string{}
	current := map[string]bool{}
	eachProject(vaultRoot, func(slug string, fields yamlFields) {
		repos := repoNames(fields.lists["repositories"])
		for _, r := range repos {
			current[r] = true
		}
		if len(repos) != 1 {
			return
		}
		for _, old := range repoNames(fields.lists["former_names"]) {
			if old != repos[0] {
				out[old] = repos[0]
			}
		}
	})
	for old := range out {
		if current[old] {
			delete(out, old)
		}
	}
	return out
}

// IssueFloors maps each repository, as `owner/repo` in lower case, to the
// `bare_issue_floor` of the project that lists it (task 187). Below its floor a
// bare `#NN`, or one after the repository's short name, is not read as one of
// its issues: the project's notes used the same numbers for its roadmap's
// items. A repository two projects list takes the higher floor. A repository
// with no floor is absent.
func IssueFloors(vaultRoot string) map[string]int {
	out := map[string]int{}
	eachProject(vaultRoot, func(slug string, fields yamlFields) {
		if fields.floor <= 0 {
			return
		}
		for _, r := range repoNames(fields.lists["repositories"]) {
			if fields.floor > out[r] {
				out[r] = fields.floor
			}
		}
	})
	return out
}

// Clones maps each repository, as `owner/repo` in lower case, to the folder of
// its clone on this machine, from the projects' `code_paths` (task 186). A
// project that lists one repository gives it the first of its paths that is a
// git checkout; a project that lists several matches a path to the repository
// whose name is the path's folder name. A repository with no checkout here is
// absent, and a caller treats it as unknown rather than empty.
func Clones(vaultRoot string) map[string]string {
	out := map[string]string{}
	eachProject(vaultRoot, func(slug string, fields yamlFields) {
		repos := repoNames(fields.lists["repositories"])
		for _, p := range fields.lists["code_paths"] {
			dir := realPath(p)
			if dir == "" {
				continue
			}
			if _, err := os.Stat(filepath.Join(dir, ".git")); err != nil {
				continue
			}
			for _, r := range repos {
				_, name, _ := strings.Cut(r, "/")
				if _, taken := out[r]; taken {
					continue
				}
				if len(repos) == 1 || strings.EqualFold(filepath.Base(dir), name) {
					out[r] = dir
				}
			}
		}
	})
	return out
}

// Signature is a cheap fingerprint of every project.yaml a reader of
// Repositories depends on: each file's path, size and modification time. A
// caller that caches Repositories re-reads when it changes.
func Signature(vaultRoot string) string {
	if vaultRoot == "" {
		return ""
	}
	var b strings.Builder
	for _, pattern := range []string{
		filepath.Join(vaultRoot, "projects", "*", "project.yaml"),
		filepath.Join(vaultRoot, "projects", "completed", "*", "project.yaml"),
	} {
		found, _ := filepath.Glob(pattern)
		sort.Strings(found)
		for _, f := range found {
			if st, err := os.Stat(f); err == nil {
				fmt.Fprintf(&b, "%s|%d|%d\n", f, st.Size(), st.ModTime().UnixNano())
			}
		}
	}
	return b.String()
}

func realPath(path string) string {
	if strings.HasPrefix(path, "~/") || path == "~" {
		home, err := os.UserHomeDir()
		if err != nil {
			return ""
		}
		path = filepath.Join(home, strings.TrimPrefix(path, "~"))
	}
	abs, err := filepath.Abs(path)
	if err != nil {
		return ""
	}
	if resolved, err := filepath.EvalSymlinks(abs); err == nil {
		abs = resolved
	}
	return filepath.Clean(abs)
}

// Resolve returns the slug of the project whose code_paths hold dir, or "".
func Resolve(vaultRoot, dir string) string {
	if vaultRoot == "" || strings.TrimSpace(dir) == "" {
		return ""
	}
	here := realPath(dir)
	if here == "" {
		return ""
	}
	files, _ := filepath.Glob(filepath.Join(vaultRoot, "projects", "*", "project.yaml"))
	sort.Strings(files)
	best, bestLen := "", -1
	for _, f := range files {
		slug, paths := readYAML(f)
		if slug == "" {
			slug = filepath.Base(filepath.Dir(f))
		}
		for _, p := range paths {
			code := realPath(p)
			if code == "" {
				continue
			}
			if here == code || strings.HasPrefix(here, code+string(filepath.Separator)) {
				if len(code) > bestLen {
					best, bestLen = slug, len(code)
				}
			}
		}
	}
	return best
}

// NoteProject is the project a note belongs to, among `projects` (slug to
// repositories, as Repositories returns): its place first — a note under
// `projects/<slug>/`, or under `projects/completed/<slug>/` once the project
// finished — and then its `project:` label. A task's plan, progress and
// tracker carry no label and are known by their place; a memory elsewhere in
// the vault carries the label Plan C stamps. A label that names no project,
// such as a legacy wikilink or a board address, gives "".
func NoteProject(rel, label string, projects map[string][]string) string {
	parts := strings.Split(strings.ReplaceAll(rel, "\\", "/"), "/")
	if len(parts) >= 3 && strings.EqualFold(parts[0], "projects") {
		slug := parts[1]
		if slug == "completed" && len(parts) >= 4 {
			slug = parts[2]
		}
		if _, ok := projects[slug]; ok {
			return slug
		}
	}
	label = strings.Trim(strings.TrimSpace(label), `"'`)
	if _, ok := projects[label]; ok {
		return label
	}
	return ""
}

// ShortNames maps each repository's short name, in lower case, to its
// `owner/repo`, across every project. A short name two repositories share is
// left out, because it no longer says which one is meant.
func ShortNames(projects map[string][]string) map[string]string {
	out, clash := map[string]string{}, map[string]bool{}
	for _, repos := range projects {
		for _, r := range repos {
			_, name, ok := strings.Cut(r, "/")
			if !ok || name == "" {
				continue
			}
			if prev, seen := out[name]; seen && prev != r {
				clash[name] = true
			}
			out[name] = r
		}
	}
	for name := range clash {
		delete(out, name)
	}
	return out
}
