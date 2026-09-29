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
	raw, err := os.ReadFile(path)
	if err != nil {
		return "", nil
	}
	inPaths := false
	unquote := func(v string) string { return strings.Trim(strings.TrimSpace(v), `"'`) }
	for _, line := range strings.Split(strings.ReplaceAll(string(raw), "\r\n", "\n"), "\n") {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		if line[0] != ' ' && line[0] != '\t' && line[0] != '-' {
			key, value, _ := strings.Cut(line, ":")
			key, value = strings.TrimSpace(key), strings.TrimSpace(value)
			inPaths = key == "code_paths" && value == ""
			switch {
			case key == "slug":
				slug = unquote(value)
			case key == "code_paths" && strings.HasPrefix(value, "[") && strings.HasSuffix(value, "]"):
				for _, p := range strings.Split(value[1:len(value)-1], ",") {
					if p = unquote(p); p != "" {
						paths = append(paths, p)
					}
				}
			}
			continue
		}
		if inPaths && strings.HasPrefix(trimmed, "- ") {
			paths = append(paths, unquote(trimmed[2:]))
		}
	}
	return slug, paths
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
