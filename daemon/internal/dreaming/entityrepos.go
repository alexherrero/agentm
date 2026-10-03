package dreaming

import (
	"os"
	"os/exec"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

// What a repository's own clone says a page may be about (task 186).
//
// The vault growth audit of 2026-10-03 found the builder paging numbers and
// versions the repository never had: a bare `#28272` in the home research is a
// pest report's number, `#191614` in the blog's brand notes is a colour, and
// "(World) (v1.0.1)" in the games lists is a ROM dump's version, each read as
// the project's repository's issue or release. The extractor cannot tell from a
// line; the repository can. An issue or pull request number is never above the
// highest `#N` its commit subjects cite, on any ref — a squash merge ends its
// subject with `(#N)` — and a release is one of its tags. An issue opened after
// the newest merged pull request waits for the next merge to get its page. A repository with no clone on this
// machine keeps the old reading, because no clone is no evidence.

// repoFacts is one clone's evidence.
type repoFacts struct {
	// maxRef is the highest `#N` any commit subject cites; 0 when none does,
	// so a repository that has never merged a pull request has no issue pages.
	maxRef int
	// tags is every tag in the clone.
	tags map[string]bool
}

// subjectRefRe is a `#N` a commit subject cites: `(#851)`, "closes #12".
var subjectRefRe = regexp.MustCompile(`(?:^|[\s(\[])#(\d+)\b`)

// readRepoFacts reads each clone once. A clone git cannot read — no commits
// yet, a broken checkout — is left out, as if it were not on this machine.
func readRepoFacts(clones map[string]string) map[string]repoFacts {
	out := map[string]repoFacts{}
	for repo, dir := range clones {
		// Every ref, not the checkout's branch: the primary clone sits at the
		// last deployed commit, and a remote-tracking or worktree branch cites
		// the newer numbers.
		subjects, err := gitOutput(dir, "log", "--all", "--format=%s")
		if err != nil {
			continue
		}
		tagList, err := gitOutput(dir, "tag", "--list")
		if err != nil {
			continue
		}
		facts := repoFacts{tags: map[string]bool{}}
		for _, m := range subjectRefRe.FindAllStringSubmatch(subjects, -1) {
			if n, err := strconv.Atoi(m[1]); err == nil && n > facts.maxRef {
				facts.maxRef = n
			}
		}
		for _, tag := range strings.Fields(tagList) {
			facts.tags[tag] = true
		}
		out[repo] = facts
	}
	return out
}

// gitOutput runs a read-only git command in dir. The machine's system config
// is not read, and git never prompts.
func gitOutput(dir string, args ...string) (string, error) {
	cmd := exec.Command("git", append([]string{"-C", dir}, args...)...)
	cmd.Env = append(os.Environ(), "GIT_CONFIG_NOSYSTEM=1", "GIT_TERMINAL_PROMPT=0")
	out, err := cmd.Output()
	return string(out), err
}

// clonePermits reports whether the repository's clone allows a page for uri:
// an issue whose number its history reaches, a release that is one of its
// tags. A repository and an entity of a repository with no clone pass.
func clonePermits(uri string, facts map[string]repoFacts) bool {
	kind, id, _ := strings.Cut(uri, ":")
	switch kind {
	case "issue":
		repo, num, _ := strings.Cut(id, "#")
		if f, ok := facts[repo]; ok {
			n, err := strconv.Atoi(num)
			return err == nil && n <= f.maxRef
		}
	case "release":
		repo, version, _ := strings.Cut(id, "@")
		if f, ok := facts[repo]; ok {
			return f.tags[version]
		}
	}
	return true
}

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
