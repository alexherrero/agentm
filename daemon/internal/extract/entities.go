package extract

import (
	"regexp"
	"sort"
	"strings"
)

// An entity reference is a mention of something that exists outside this note
// and is referred to by a stable identifier — an issue, a pull request, a
// repository, a commit.
//
// Indexing these is what makes an entity timeline possible *before* any `person`
// type exists: every note mentioning a given issue is one lookup away, and the
// rollup that eventually summarizes it is built from that set rather than from a
// directory scan. It is regex over text with no model involved, and it creates
// no new type, so the taxonomy's growth rule is untouched.

// EntityURI is a namespaced identifier, so two kinds of thing can never collide
// in the index: `issue:owner/repo#123` is not `repo:owner/repo`.
type EntityURI = string

var (
	// `owner/repo#123` — a fully-qualified issue or pull request.
	qualifiedIssueRe = regexp.MustCompile(`\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)\b`)

	// `#123` on its own. Deliberately requires a word boundary before the hash
	// and at least two digits, because `#1` is as often a list marker or a
	// heading fragment as a reference, and a tag like `#todo` is not a number.
	bareIssueRe = regexp.MustCompile(`(^|[\s(\[])#(\d{2,})\b`)

	// A GitHub-shaped repository path. Requires the host, because `a/b` on its
	// own is a path far more often than a repository.
	//
	// Matched greedily and trimmed afterwards rather than terminated by a
	// character class. The first version required an explicit terminator and so
	// missed `github.com/owner/repo,` — a comma was not in the class, and the
	// list of punctuation that can follow a URL in prose is longer than it looks.
	// Trimming what a repository name cannot end with is the smaller claim.
	repoURLRe = regexp.MustCompile(`\bgithub\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)`)

	// A full or abbreviated commit hash. Seven is the shortest git abbreviates
	// to; below that the false-positive rate against ordinary hex-looking words
	// stops being worth the recall.
	commitRe = regexp.MustCompile(`\b([0-9a-f]{7,40})\b`)

	// A changelist, the other system's identifier form.
	changelistRe = regexp.MustCompile(`\bcl/(\d+)\b`)
)

// Entities pulls every external reference out of a note.
//
// Returned sorted and deduped, because these become index rows and a derived
// row set that varied between runs would make every rebuild a diff.
//
// Fenced code is skipped for the same reason links skip it: a commit hash in a
// worked example is a sample, not a reference to something this note is about.
func Entities(body string) []EntityURI {
	seen := map[string]bool{}
	var out []EntityURI
	add := func(uri string) {
		if !seen[uri] {
			seen[uri] = true
			out = append(out, uri)
		}
	}

	inFence := false
	for _, line := range strings.Split(body, "\n") {
		if fenceRe.MatchString(line) {
			inFence = !inFence
			continue
		}
		if inFence {
			continue
		}

		// Qualified issues first, and their spans are remembered, so the bare-issue
		// pass does not also record `#123` out of `owner/repo#123` as if it were a
		// reference to a different, local issue.
		qualified := map[int]bool{}
		for _, m := range qualifiedIssueRe.FindAllStringSubmatchIndex(line, -1) {
			repo := line[m[2]:m[3]]
			num := line[m[4]:m[5]]
			add("issue:" + strings.ToLower(repo) + "#" + num)
			for i := m[0]; i < m[1]; i++ {
				qualified[i] = true
			}
		}

		for _, m := range bareIssueRe.FindAllStringSubmatchIndex(line, -1) {
			if qualified[m[0]] || qualified[m[1]-1] {
				continue
			}
			add("issue:#" + line[m[4]:m[5]])
		}

		for _, m := range repoURLRe.FindAllStringSubmatch(line, -1) {
			repo := strings.TrimSuffix(m[1], ".git")
			repo = strings.TrimRight(repo, "./-")
			if strings.Count(repo, "/") != 1 || strings.HasSuffix(repo, "/") {
				continue
			}
			add("repo:" + strings.ToLower(repo))
		}

		for _, m := range changelistRe.FindAllStringSubmatch(line, -1) {
			add("cl:" + m[1])
		}

		for _, m := range commitRe.FindAllStringSubmatch(line, -1) {
			h := m[1]
			// All-digit runs are dates, counts and issue numbers far more often
			// than commits. A hash worth recording has at least one hex letter.
			if !strings.ContainsAny(h, "abcdef") {
				continue
			}
			add("commit:" + h)
		}
	}

	sort.Strings(out)
	return out
}
