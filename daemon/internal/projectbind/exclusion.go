package projectbind

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// The recall exclusion rule (agentm-vault § Projects and tasks, amended
// 2026-10-31): a project's `recall_excludes:` names the projects whose notes
// its sessions must not be served. It is a wall and not a demotion: a project
// built in a clean room must not see the work it may not derive from, however
// relevant a search finds it.
//
// Every reader in this repo either degrades to "unbound" or to a ranking that
// is a little worse. This one does neither. A rule that cannot be read returns
// an error, and the caller withholds rather than serves, because a session
// that silently loses its wall looks exactly like one that never had it. The
// Python side keeps the same rule in `recall_exclusion.py`.

// projectRule is what the rule needs of one project.yaml.
type projectRule struct {
	slug     string
	codes    []string
	excludes []string
	problem  string
}

func readRules(vaultRoot string) []projectRule {
	var out []projectRule
	seen := map[string]bool{}
	// Live projects first, so a finished copy never shadows the live one.
	for _, pattern := range []string{
		filepath.Join(vaultRoot, "projects", "*", "project.yaml"),
		filepath.Join(vaultRoot, "projects", "completed", "*", "project.yaml"),
	} {
		files, _ := filepath.Glob(pattern)
		sort.Strings(files)
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
			rule := projectRule{slug: slug, excludes: fields.lists["recall_excludes"], problem: fields.problem}
			for _, p := range fields.lists["code_paths"] {
				if code := realPath(p); code != "" {
					rule.codes = append(rule.codes, code)
				}
			}
			for _, name := range rule.excludes {
				if rule.problem == "" && !projectExists(vaultRoot, name) {
					rule.problem = fmt.Sprintf("`recall_excludes` names %q, and the vault has no such project", name)
				}
			}
			out = append(out, rule)
		}
	}
	return out
}

func projectExists(vaultRoot, slug string) bool {
	for _, dir := range []string{
		filepath.Join(vaultRoot, "projects", slug),
		filepath.Join(vaultRoot, "projects", "completed", slug),
	} {
		if st, err := os.Stat(dir); err == nil && st.IsDir() {
			return true
		}
	}
	return false
}

// mainCheckoutPath is dir's twin in the main checkout when dir sits in a linked
// git worktree, else "". A worktree can live anywhere, so its path says nothing
// about the project; its `.git` file does. A submodule's `.git` file points at
// `.git/modules/`, not `.git/worktrees/`, and is left alone.
func mainCheckoutPath(dir string) string {
	for top := dir; ; top = filepath.Dir(top) {
		marker := filepath.Join(top, ".git")
		if st, err := os.Stat(marker); err == nil {
			if st.IsDir() {
				return ""
			}
			raw, err := os.ReadFile(marker)
			if err != nil {
				return ""
			}
			gitdir, ok := strings.CutPrefix(strings.TrimSpace(string(raw)), "gitdir:")
			if !ok {
				return ""
			}
			gitdir = filepath.FromSlash(strings.TrimSpace(gitdir))
			if !filepath.IsAbs(gitdir) {
				gitdir = filepath.Join(top, gitdir)
			}
			sep := string(filepath.Separator)
			at := strings.LastIndex(gitdir, sep+".git"+sep+"worktrees"+sep)
			if at < 0 {
				return ""
			}
			rel, err := filepath.Rel(top, dir)
			if err != nil {
				return ""
			}
			return filepath.Join(gitdir[:at], rel)
		}
		if parent := filepath.Dir(top); parent == top {
			return ""
		}
	}
}

// sessionRule finds the rule of the project a session in dir works on: the
// longest code_paths match, else the project whose vault folder holds it, for
// the checkout dir is in and then for the main checkout of a linked worktree.
// A vault folder with no project.yaml is a project with no rule, so it is
// reported as found.
func sessionRule(rules []projectRule, vaultRoot, dir string) (projectRule, bool) {
	here := realPath(dir)
	if here == "" {
		return projectRule{}, false
	}
	vault := realPath(vaultRoot)
	for _, candidate := range []string{here, mainCheckoutPath(here)} {
		if candidate == "" {
			continue
		}
		best, bestLen := -1, -1
		for i, r := range rules {
			for _, code := range r.codes {
				if (candidate == code || strings.HasPrefix(candidate, code+string(filepath.Separator))) && len(code) > bestLen {
					best, bestLen = i, len(code)
				}
			}
		}
		if best >= 0 {
			return rules[best], true
		}
		if slug := vaultFolderProject(vault, candidate); slug != "" {
			for _, r := range rules {
				if r.slug == slug {
					return r, true
				}
			}
			return projectRule{slug: slug}, true
		}
	}
	return projectRule{}, false
}

// vaultFolderProject is the project whose folder under the vault holds dir, "".
func vaultFolderProject(vault, dir string) string {
	if vault == "" {
		return ""
	}
	rel, err := filepath.Rel(vault, dir)
	if err != nil {
		return ""
	}
	parts := strings.Split(filepath.ToSlash(rel), "/")
	if len(parts) >= 3 && parts[0] == "projects" && parts[1] == "completed" {
		return parts[2]
	}
	if len(parts) >= 2 && parts[0] == "projects" && parts[1] != "completed" {
		return parts[1]
	}
	return ""
}

// Exclusions returns the projects a session in dir must not be served. An error
// means the rule that would say so could not be read, and the session may be
// one it covers: the caller serves nothing rather than everything. A folder no
// readable project claims is unknown only while some project.yaml cannot be
// read, because that file may be the one that claims it.
func Exclusions(vaultRoot, dir string) ([]string, error) {
	if vaultRoot == "" || strings.TrimSpace(dir) == "" {
		return nil, nil
	}
	rules := readRules(vaultRoot)
	if rule, ok := sessionRule(rules, vaultRoot, dir); ok {
		if rule.problem != "" {
			return nil, fmt.Errorf("recall exclusions for project %q cannot be read: %s", rule.slug, rule.problem)
		}
		return rule.excludes, nil
	}
	for _, r := range rules {
		if strings.HasPrefix(r.problem, "could not read") {
			return nil, fmt.Errorf("recall exclusions cannot be read for this folder: project %q: %s", r.slug, r.problem)
		}
	}
	return nil, nil
}

// EveryExclusion returns the projects any project's rule excludes, for a caller
// that cannot say where it is working. It may be a session the rule covers, so
// it is treated as one.
func EveryExclusion(vaultRoot string) ([]string, error) {
	if vaultRoot == "" {
		return nil, nil
	}
	seen := map[string]bool{}
	var out []string
	for _, r := range readRules(vaultRoot) {
		if r.problem != "" {
			return nil, fmt.Errorf("recall exclusions for project %q cannot be read: %s", r.slug, r.problem)
		}
		for _, name := range r.excludes {
			if !seen[name] {
				seen[name] = true
				out = append(out, name)
			}
		}
	}
	sort.Strings(out)
	return out, nil
}

// NoteExcluded reports whether a note belongs to one of `slugs`: it sits under
// `projects/<slug>/` or `projects/completed/<slug>/`, at any depth so a memory
// root's prefix does not matter, or its `project:` label names one. The label
// is what keeps a project's cards out when they are filed outside its folder.
func NoteExcluded(rel, label string, slugs []string) bool {
	if len(slugs) == 0 {
		return false
	}
	want := map[string]bool{}
	for _, s := range slugs {
		want[strings.ToLower(s)] = true
	}
	if want[strings.ToLower(strings.Trim(strings.TrimSpace(label), `"'`))] {
		return true
	}
	parts := strings.Split(strings.ToLower(strings.ReplaceAll(rel, "\\", "/")), "/")
	for i := 0; i+2 < len(parts); i++ {
		if parts[i] != "projects" {
			continue
		}
		if want[parts[i+1]] {
			return true
		}
		if parts[i+1] == "completed" && i+3 < len(parts) && want[parts[i+2]] {
			return true
		}
	}
	return false
}
