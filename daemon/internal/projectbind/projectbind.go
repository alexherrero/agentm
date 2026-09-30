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

// yamlFields is what the line reader keeps of one project.yaml: its slug, and
// the two lists a reader asks for.
type yamlFields struct {
	slug  string
	lists map[string][]string
}

// listKeys are the list-valued keys the reader collects.
var listKeys = map[string]bool{"code_paths": true, "repositories": true}

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
	if vaultRoot == "" {
		return out
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
	for _, f := range files {
		fields := readFields(f)
		slug := fields.slug
		if slug == "" {
			slug = filepath.Base(filepath.Dir(f))
		}
		if _, seen := out[slug]; seen {
			continue
		}
		repos := []string{}
		for _, r := range fields.lists["repositories"] {
			r = strings.ToLower(strings.Trim(strings.TrimSpace(r), "/"))
			if strings.Count(r, "/") == 1 && !strings.HasPrefix(r, "/") {
				repos = append(repos, r)
			}
		}
		out[slug] = repos
	}
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
