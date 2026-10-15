package projectbind

import (
	"os"
	"os/exec"
	"regexp"
	"strconv"
)

// What a repository's own history says its numbers reach (task 186's reading,
// shared with the extractor in task 191).
//
// An issue or pull request number is never above the highest `#N` the
// repository's commit subjects cite, on any ref: a squash merge ends its
// subject with `(#N)`. The entity builder holds a page to that; the extractor
// moves a reference to another repository only within it.

// subjectRefRe is a `#N` a commit subject cites: `(#851)`, "closes #12".
var subjectRefRe = regexp.MustCompile(`(?:^|[\s(\[])#(\d+)\b`)

// MaxCitedRef is the highest `#N` any commit subject cites on any ref of the
// clone at dir; 0 when none does. Every ref, not the checkout's branch: the
// primary clone sits at the last deployed commit, and a remote-tracking or
// worktree branch cites the newer numbers.
func MaxCitedRef(dir string) (int, error) {
	subjects, err := GitOutput(dir, "log", "--all", "--format=%s")
	if err != nil {
		return 0, err
	}
	max := 0
	for _, m := range subjectRefRe.FindAllStringSubmatch(subjects, -1) {
		if n, err := strconv.Atoi(m[1]); err == nil && n > max {
			max = n
		}
	}
	return max, nil
}

// RefCeilings is MaxCitedRef for every repository with a clone on this machine
// (Clones). A clone git cannot read is left out, as if it were not here.
func RefCeilings(vaultRoot string) map[string]int {
	out := map[string]int{}
	for repo, dir := range Clones(vaultRoot) {
		if n, err := MaxCitedRef(dir); err == nil {
			out[repo] = n
		}
	}
	return out
}

// GitOutput runs a read-only git command in dir. The machine's system config
// is not read, and git never prompts.
func GitOutput(dir string, args ...string) (string, error) {
	cmd := exec.Command("git", append([]string{"-C", dir}, args...)...)
	cmd.Env = append(os.Environ(), "GIT_CONFIG_NOSYSTEM=1", "GIT_TERMINAL_PROMPT=0")
	out, err := cmd.Output()
	return string(out), err
}
