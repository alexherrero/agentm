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

	// An issue or pull request by its GitHub address. The link names its repo,
	// so it is qualified whatever the note is about (task 179).
	issueURLRe = regexp.MustCompile(`\bgithub\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/(?:issues|pull)/(\d+)\b`)

	// A release by its GitHub address: `…/releases/tag/v1.2.3`.
	releaseURLRe = regexp.MustCompile(`\bgithub\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/releases/tag/(v\d+\.\d+\.\d+)\b`)

	// A version tag in prose. Only the three-part `vX.Y.Z` form: a two-part
	// `v1.2` is as often a model or a document revision as a release.
	versionRe = regexp.MustCompile(`v\d+\.\d+\.\d+`)

	// An `owner/repo` written out in prose, the word before a version tag.
	ownerRepoRe = regexp.MustCompile(`^[a-z0-9_.-]+/[a-z0-9_.-]+$`)
)

// Context is what a note's place tells the extractor about the repository a
// bare reference means (task 179). The zero value is no context, which is how
// `Entities` behaves: a bare `#12` stays `issue:#12` and a version tag with no
// repository beside it is no release.
type Context struct {
	// Repo is the one repository the note's project lists, as `owner/repo` in
	// lower case, or "" when the note belongs to no project or its project
	// lists none or several. A bare `#12` in the note is `issue:<Repo>#12`.
	Repo string
	// Known maps a repository's short name, in lower case, to its `owner/repo`
	// for every repository a project lists, so "crickets v4.0.0" in any note
	// names crickets' release. A short name two repositories share is left out.
	Known map[string]string
}

// releaseWords are the words that may stand right before a version tag that
// belongs to the note's own repository: prepositions, articles and the words a
// release is spoken of with. Any other word names the thing the version belongs
// to — a tool, a plugin, a model — and a version after it is not the repo's.
// Drawn from the live vault on 2026-09-29: of the ~3,400 version tags in it,
// these and a repository's own name stand before nearly every one that is a
// release, and "agy", "desktop" or a plugin's hyphenated name before the rest.
var releaseWords = map[string]bool{
	"the": true, "in": true, "at": true, "as": true, "since": true, "to": true,
	"for": true, "of": true, "on": true, "after": true, "before": true,
	"from": true, "into": true, "through": true, "with": true, "and": true,
	"or": true, "a": true, "is": true, "was": true, "until": true, "by": true,
	"shipped": true, "ship": true, "ships": true, "released": true,
	"release": true, "releases": true, "tag": true, "tagged": true, "cut": true,
	"create": true, "view": true, "version": true, "changelog": true,
	"changelog.md": true, "pair": true, "paired": true, "bump": true,
	"bumped": true, "landed": true, "lands": true, "launched": true,
}

// Paged reports whether an entity may have a page of its own (task 179): a
// repository, an issue whose repository is known, and a release. A commit, a
// changelist and a bare `#12` are indexed for search and never paged — none is
// something anyone asks about by name, and a bare number is a different issue
// in every repository.
func Paged(uri EntityURI) bool {
	kind, rest, ok := strings.Cut(uri, ":")
	if !ok {
		return false
	}
	switch kind {
	case "repo", "release":
		return true
	case "issue":
		return !strings.HasPrefix(rest, "#")
	}
	return false
}

// Entities pulls every external reference out of a note, with no context.
func Entities(body string) []EntityURI { return EntitiesIn(body, Context{}) }

// EntitiesIn pulls every external reference out of a note, qualifying what the
// note's context allows.
//
// Returned sorted and deduped, because these become index rows and a derived
// row set that varied between runs would make every rebuild a diff.
//
// Fenced code is skipped for the same reason links skip it: a commit hash in a
// worked example is a sample, not a reference to something this note is about.
func EntitiesIn(body string, ctx Context) []EntityURI {
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
		// The repositories this line names, for a version tag on it.
		lineRepos := map[string]bool{}
		for _, m := range qualifiedIssueRe.FindAllStringSubmatchIndex(line, -1) {
			repo := strings.ToLower(line[m[2]:m[3]])
			num := line[m[4]:m[5]]
			add("issue:" + repo + "#" + num)
			lineRepos[repo] = true
			for i := m[0]; i < m[1]; i++ {
				qualified[i] = true
			}
		}

		// An issue by its address. Its number, written bare beside the link —
		// `[#690](https://github.com/o/r/pull/690)` — is the same issue, so the
		// bare pass takes the link's repository for it.
		linked := map[string]string{}
		for _, m := range issueURLRe.FindAllStringSubmatch(line, -1) {
			repo := strings.ToLower(m[1])
			add("issue:" + repo + "#" + m[2])
			linked[m[2]] = repo
		}

		for _, m := range bareIssueRe.FindAllStringSubmatchIndex(line, -1) {
			if qualified[m[0]] || qualified[m[1]-1] {
				continue
			}
			num := line[m[4]:m[5]]
			switch {
			case linked[num] != "":
				add("issue:" + linked[num] + "#" + num)
			case ctx.Repo != "":
				add("issue:" + ctx.Repo + "#" + num)
			default:
				add("issue:#" + num)
			}
		}

		for _, m := range repoURLRe.FindAllStringSubmatch(line, -1) {
			repo := strings.TrimSuffix(m[1], ".git")
			repo = strings.TrimRight(repo, "./-")
			if strings.Count(repo, "/") != 1 || strings.HasSuffix(repo, "/") {
				continue
			}
			add("repo:" + strings.ToLower(repo))
			lineRepos[strings.ToLower(repo)] = true
		}

		// Releases: by their address, then by a version tag in the prose.
		for _, m := range releaseURLRe.FindAllStringSubmatch(line, -1) {
			add("release:" + strings.ToLower(m[1]) + "@" + m[2])
		}
		for _, m := range versionRe.FindAllStringIndex(line, -1) {
			if repo := releaseRepo(line, m[0], m[1], lineRepos, ctx); repo != "" {
				add("release:" + repo + "@" + line[m[0]:m[1]])
			}
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

// releaseRepo decides which repository the version tag at line[start:end]
// belongs to, or "" when it cannot tell. In order: the word right before it,
// when that word is a repository's own name or `owner/repo`; then, when the
// word before is a release word or nothing, the one repository the line names
// or the note's own. A line naming one repository in a note of another is
// ambiguous — "paired with crickets; shipped v10.0.0" in an agentm note — and
// gives none. Any other word before the tag names what the version belongs
// to, and it is not a release of a repository.
func releaseRepo(line string, start, end int, lineRepos map[string]bool, ctx Context) string {
	// The tag must stand alone: not inside a word, a path or a URL (a release
	// URL is read by its own pattern), and not the head of a longer version or
	// a pre-release.
	if start > 0 {
		switch c := line[start-1]; {
		case c == '/' || c == '.' || c == '-' || c == '_' || c == '@' || c == '#' ||
			(c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9'):
			return ""
		}
	}
	if end < len(line) {
		c := line[end]
		if c == '-' || c == '_' || c == '+' || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
			(c >= '0' && c <= '9') {
			return ""
		}
		if c == '.' && end+1 < len(line) && line[end+1] >= '0' && line[end+1] <= '9' {
			return ""
		}
	}
	word := strings.ToLower(wordBefore(line, start))
	switch {
	case ownerRepoRe.MatchString(word):
		// A path reads the same as `owner/repo` — `wiki/docs v2.0.0`,
		// `tool/CHANGELOG.md v1.4.0` — so only a repository a project lists, or
		// one this line points at, is taken for the version's.
		if lineRepos[word] || knownRepo(ctx, word) {
			return word
		}
		return ""
	case ctx.Known[word] != "":
		return ctx.Known[word]
	case word != "" && !releaseWords[word]:
		return ""
	}
	if len(lineRepos) == 1 {
		for r := range lineRepos {
			if ctx.Repo == "" || ctx.Repo == r {
				return r
			}
		}
		return ""
	}
	if len(lineRepos) == 0 {
		return ctx.Repo
	}
	return ""
}

// knownRepo reports whether repo is one a project lists.
func knownRepo(ctx Context, repo string) bool {
	for _, r := range ctx.Known {
		if r == repo {
			return true
		}
	}
	return false
}

// wordBefore is the word right before position i, past spaces and the
// markdown around a link or an emphasis: `[`, `(`, `*`, `_`, a backtick and a
// quote. Empty when the line starts there or punctuation stands between.
func wordBefore(line string, i int) string {
	j := i
	for j > 0 && strings.ContainsRune(" \t[(*`\"'", rune(line[j-1])) {
		j--
	}
	k := j
	for k > 0 {
		c := line[k-1]
		if (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
			c == '-' || c == '_' || c == '.' || c == '/' {
			k--
			continue
		}
		break
	}
	return strings.Trim(line[k:j], "./")
}
