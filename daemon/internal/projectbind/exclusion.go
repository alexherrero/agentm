package projectbind

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"syscall"
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

// readFailure opens the problem of a project.yaml that would not read, the one
// kind that may be hiding a claim on a folder.
const readFailure = "could not read it"

// slugShape is what `check-project-yaml` holds a rule's entries to. It is
// checked here too because an entry such as `completed/port` names a folder that
// exists, so the rule would read as sound and match no note.
var slugShape = regexp.MustCompile(`^[a-z0-9][a-z0-9-]*$`)

// projectRule is what the rule needs of one project.yaml.
type projectRule struct {
	slug     string
	codes    []string
	excludes []string
	problem  string
}

// readRules lists the folders itself rather than globbing, for two reasons: a
// bracket in the vault's path would be read as a pattern, and a glob drops a
// folder it cannot open without saying so, which here would lose a rule.
func readRules(vaultRoot string) []projectRule {
	var out []projectRule
	seen := map[string]bool{}
	// Live projects first, so a finished copy never shadows the live one.
	for _, parent := range []string{
		filepath.Join(vaultRoot, "projects"),
		filepath.Join(vaultRoot, "projects", "completed"),
	} {
		entries, err := os.ReadDir(parent)
		if errors.Is(err, fs.ErrNotExist) || errors.Is(err, syscall.ENOTDIR) {
			continue
		}
		if err != nil {
			out = append(out, projectRule{slug: filepath.Base(parent), problem: readFailure + ": " + err.Error()})
			continue
		}
		for _, e := range entries {
			if st, err := os.Stat(filepath.Join(parent, e.Name())); err != nil || !st.IsDir() {
				continue
			}
			file := filepath.Join(parent, e.Name(), "project.yaml")
			if _, err := os.Stat(file); errors.Is(err, fs.ErrNotExist) {
				continue
			}
			fields := readFields(file)
			slug := fields.slug
			if slug == "" {
				slug = e.Name()
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
				if rule.problem != "" {
					break
				}
				if !slugShape.MatchString(name) {
					rule.problem = fmt.Sprintf("`recall_excludes` entry %q is not a project slug", name)
				} else if !projectExists(vaultRoot, name) {
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
// about the project; its `.git` file does, as `<common>/worktrees/<name>`. The
// common directory is `<clone>/.git`, or the repository itself when it is bare.
// A submodule's `.git` file points elsewhere and is left alone.
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
			gitdir = filepath.Clean(gitdir)
			if filepath.Base(filepath.Dir(gitdir)) != "worktrees" {
				return ""
			}
			main := filepath.Dir(filepath.Dir(gitdir))
			if filepath.Base(main) == ".git" {
				main = filepath.Dir(main)
			}
			rel, err := filepath.Rel(top, dir)
			if err != nil {
				return ""
			}
			return filepath.Join(main, rel)
		}
		if parent := filepath.Dir(top); parent == top {
			return ""
		}
	}
}

// claimingRules is the rule of every project a session in dir works on, for the
// checkout dir is in and for the main checkout of a linked worktree. Every claim
// counts, unlike a card's stamp, which wants one project: a worktree inside one
// project's folder or a path two projects list must not lose a rule to whichever
// sorted first. A vault folder with no project.yaml is a project with no rule, so
// it is still a claim. Case is ignored because this filesystem ignores it, and a
// shell can stand in `~/code/Clean` for `~/code/clean`.
func claimingRules(rules []projectRule, vaultRoot, dir string) []projectRule {
	here := realPath(dir)
	if here == "" {
		return nil
	}
	sep := string(filepath.Separator)
	vault := strings.ToLower(realPath(vaultRoot))
	var out []projectRule
	seen := map[string]bool{}
	claim := func(r projectRule) {
		if !seen[r.slug] {
			seen[r.slug] = true
			out = append(out, r)
		}
	}
	for _, candidate := range []string{here, mainCheckoutPath(here)} {
		if candidate == "" {
			continue
		}
		candidate = strings.ToLower(candidate)
		for _, r := range rules {
			for _, code := range r.codes {
				code = strings.ToLower(code)
				if candidate == code || strings.HasPrefix(candidate, code+sep) {
					claim(r)
				}
			}
		}
		if slug := vaultFolderProject(vault, candidate); slug != "" {
			found := false
			for _, r := range rules {
				if strings.EqualFold(r.slug, slug) {
					claim(r)
					found = true
				}
			}
			if !found {
				claim(projectRule{slug: slug})
			}
		}
	}
	return out
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

// Exclusions returns the projects a session in dir must not be served: what
// every project claiming dir excludes, less those projects themselves, since a
// session is never walled out of its own notes. An error means a rule that would
// say so could not be read, and the caller serves nothing rather than everything.
//
// A folder no readable project claims is unknown while some project.yaml will
// not read, because that file may be the one that claims it. A folder a readable
// project claims is not held up by another project's unreadable file: that file
// could only matter by adding a claim inside this one, and stopping every
// session's recall for one bad file costs more than that rarity is worth.
func Exclusions(vaultRoot, dir string) ([]string, error) {
	if vaultRoot == "" || strings.TrimSpace(dir) == "" {
		return nil, nil
	}
	rules := readRules(vaultRoot)
	claims := claimingRules(rules, vaultRoot, dir)
	if len(claims) == 0 {
		for _, r := range rules {
			if strings.HasPrefix(r.problem, readFailure) {
				return nil, fmt.Errorf("recall exclusions cannot be read for this folder: project %q: %s", r.slug, r.problem)
			}
		}
		return nil, nil
	}
	own := map[string]bool{}
	for _, r := range claims {
		own[strings.ToLower(r.slug)] = true
	}
	var out []string
	seen := map[string]bool{}
	for _, r := range claims {
		if r.problem != "" {
			return nil, fmt.Errorf("recall exclusions for project %q cannot be read: %s", r.slug, r.problem)
		}
		for _, name := range r.excludes {
			if !own[name] && !seen[name] {
				seen[name] = true
				out = append(out, name)
			}
		}
	}
	return out, nil
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
