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
	"deployed": true, "merged": true, "published": true, "promoted": true,
	"reopened": true, "closed": true, "fixed": true, "shipping": true,
	"via": true, "per": true,
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
			if reservedOwner(repo) {
				continue
			}
			add("issue:" + repo + "#" + m[2])
			linked[m[2]] = repo
		}

		for _, m := range bareIssueRe.FindAllStringSubmatchIndex(line, -1) {
			if qualified[m[0]] || qualified[m[1]-1] {
				continue
			}
			num := line[m[4]:m[5]]
			named := ctx.Known[possessive(strings.ToLower(wordBefore(line, m[3])))]
			switch {
			case linked[num] != "":
				add("issue:" + linked[num] + "#" + num)
			case named != "":
				// "Fixed in crickets #235": the word before names the repository.
				add("issue:" + named + "#" + num)
			case ctx.Repo != "":
				add("issue:" + ctx.Repo + "#" + num)
			default:
				add("issue:#" + num)
			}
		}

		for _, m := range repoURLRe.FindAllStringSubmatchIndex(line, -1) {
			if !onGitHubItself(line, m[0]) {
				continue
			}
			repo := strings.TrimSuffix(line[m[2]:m[3]], ".git")
			repo = strings.TrimRight(repo, "./-")
			if strings.Count(repo, "/") != 1 || strings.HasSuffix(repo, "/") || reservedOwner(repo) {
				continue
			}
			add("repo:" + strings.ToLower(repo))
			lineRepos[strings.ToLower(repo)] = true
		}

		// Releases: by their address, then by a version tag in the prose.
		for _, m := range releaseURLRe.FindAllStringSubmatchIndex(line, -1) {
			if !versionEndsHere(line, m[1]) {
				continue // `…/tag/v10.0.0-rc.1` is a pre-release, not v10.0.0
			}
			add("release:" + strings.ToLower(line[m[2]:m[3]]) + "@" + line[m[4]:m[5]])
		}
		foreign := foreignRepoOnLine(line, lineRepos, ctx)
		for _, m := range versionRe.FindAllStringIndex(line, -1) {
			if repo := releaseRepo(line, m[0], m[1], lineRepos, foreign, ctx); repo != "" {
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
// belongs to, or "" when it cannot tell. The two words before it are read in
// turn, past release words and other versions: a repository's own name or a
// known `owner/repo` names it; any other word names what the version belongs
// to — a tool, a plugin, `node` — and it is no release of a repository. With
// neither word deciding, the one repository the line names, or the note's own,
// takes it; a line naming one repository in a note of another, or naming a
// repository no project lists (`Bump actions/checkout from v3.5.2 to v4.0.0`),
// is ambiguous and gives none.
func releaseRepo(line string, start, end int, lineRepos map[string]bool, foreign bool, ctx Context) string {
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
	if !versionEndsHere(line, end) {
		return ""
	}
	at := start
	for i := 0; i < 2; i++ {
		raw, from := wordBeforeAt(line, at)
		at = from
		word := possessive(strings.ToLower(strings.Trim(raw, "-_")))
		switch {
		case word == "" || releaseWords[word] || allDigits(word) ||
			versionRe.MatchString(word) && len(versionRe.FindString(word)) == len(word):
			// A release word, another version, or a number (`#466 after v10.0.0`)
			// says nothing about whose version this is; read the word before.
			continue
		case ownerRepoRe.MatchString(word):
			if lineRepos[word] || knownRepo(ctx, word) {
				return word
			}
			return ""
		case ctx.Known[word] != "":
			return ctx.Known[word]
		default:
			return ""
		}
	}
	if foreign {
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

// versionEndsHere reports whether a version ending at line[end] stops there: it
// is not followed by a letter, a digit, `-`, `_` or `+` (a pre-release or a
// build), or by `.` and a digit (a longer version).
func versionEndsHere(line string, end int) bool {
	if end >= len(line) {
		return true
	}
	c := line[end]
	if c == '-' || c == '_' || c == '+' || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
		(c >= '0' && c <= '9') {
		return false
	}
	return !(c == '.' && end+1 < len(line) && line[end+1] >= '0' && line[end+1] <= '9')
}

// foreignRepoOnLine reports whether the line names an `owner/repo` no project
// lists and no link on the line points at — a dependency, most often — which
// makes a version on it nobody's release unless a word right before it says
// whose.
func foreignRepoOnLine(line string, lineRepos map[string]bool, ctx Context) bool {
	for _, w := range strings.FieldsFunc(line, func(r rune) bool {
		return !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' ||
			r == '-' || r == '_' || r == '.' || r == '/')
	}) {
		w = strings.ToLower(strings.Trim(w, "./-_"))
		if strings.Count(w, "/") != 1 || !ownerRepoRe.MatchString(w) || strings.Contains(w, ".md") {
			continue
		}
		if !lineRepos[w] && !knownRepo(ctx, w) {
			return true
		}
	}
	return false
}

// allDigits reports whether w is a number, an issue's or a count.
func allDigits(w string) bool {
	if w == "" {
		return false
	}
	for _, c := range w {
		if c < '0' || c > '9' {
			return false
		}
	}
	return true
}

// possessive drops a trailing "'s" or "’s": "agentm's v10.0.0" is agentm's.
func possessive(w string) string {
	for _, suffix := range []string{"'s", "’s"} {
		if strings.HasSuffix(w, suffix) {
			return strings.TrimSuffix(w, suffix)
		}
	}
	return w
}

// onGitHubItself reports whether the `github.com` at line[i:] is the host
// itself and not a subdomain of it: `docs.github.com/en/rest` is a manual page
// and `gist.github.com/karpathy/<id>` a gist, and neither names a repository.
func onGitHubItself(line string, i int) bool {
	return i == 0 || line[i-1] != '.'
}

// reservedFirstSegments are GitHub's own pages at the place a repository's
// owner would be: `github.com/users/alexherrero/projects/2` is a project board,
// `github.com/orgs/community/discussions` a forum.
var reservedFirstSegments = map[string]bool{
	"users": true, "orgs": true, "organizations": true, "settings": true,
	"sponsors": true, "marketplace": true, "apps": true, "topics": true,
	"features": true, "enterprise": true, "login": true, "about": true,
	"pricing": true, "collections": true, "trending": true, "notifications": true,
	"codespaces": true, "search": true, "explore": true, "site": true,
	"security": true, "customer-stories": true, "readme": true, "events": true,
	"new": true, "account": true, "pulls": true, "issues": true,
}

// reservedOwner reports whether an `owner/repo` is one of GitHub's own pages.
func reservedOwner(repo string) bool {
	owner, _, _ := strings.Cut(strings.ToLower(repo), "/")
	return reservedFirstSegments[owner]
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
	w, _ := wordBeforeAt(line, i)
	return w
}

// wordBeforeAt is wordBefore and the index the word starts at, so a caller can
// read the word before that one. An apostrophe inside the word is kept, so a
// possessive arrives whole.
func wordBeforeAt(line string, i int) (string, int) {
	j := i
	for j > 0 && strings.ContainsRune(" \t[(*`\"", rune(line[j-1])) {
		j--
	}
	k := j
	for k > 0 {
		c := line[k-1]
		if (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
			c == '-' || c == '_' || c == '.' || c == '/' || c == '\'' {
			k--
			continue
		}
		// The typographic apostrophe, three bytes in UTF-8.
		if k >= 3 && line[k-3:k] == "’" {
			k -= 3
			continue
		}
		break
	}
	return strings.Trim(line[k:j], "./'"), k
}
