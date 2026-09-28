// Package linkrewrite points the links into moved files at their new paths.
//
// It is a port of `rewrite_text` in `scripts/migrate/agentkv_layout.py`, the
// function every bulk move of the AgentKV layout convergence ran its links
// through, so the night's task mover (task 177) rewrites a link the way the
// hand-run moves did. The parity test runs the Python function over the same
// fixtures and compares the outputs; a change to one without the other fails
// it.
//
// Three shapes change:
//
//   - a path wikilink into a moved file, full or a trailing part of the path,
//     with or without `.md`: it points at the new path and keeps its anchor and
//     alias, or takes the old target as its alias when it had none, so the words
//     a reader sees do not change;
//   - a markdown link into a moved file, relative to the note or rooted at the
//     vault: recomputed from where the note sits after the move;
//   - a moved note's own relative markdown links out: recomputed from its new
//     folder, so a plan one level deeper still reaches the file it named.
//
// A basename wikilink (`[[plan]]`) is left alone, because it names a file by
// its name and the name did not change. A bare path in backticks is not a
// link and is left alone too; a link written inside a code span or block is
// rewritten like any other, as the Python does.
package linkrewrite

import (
	"path"
	"path/filepath"
	"regexp"
	"strings"
	"unicode/utf8"
)

// Move is one file's move, both paths relative to the vault root and
// slash-separated.
type Move struct {
	From string `json:"from"`
	To   string `json:"to"`
}

// Rewriter holds one run's moves.
type Rewriter struct {
	moves map[string]string
	// folded is every old path, and every old path without its `.md`, keyed in
	// lowercase and kept in the order the moves were given. The disk is
	// case-insensitive and so is Obsidian's resolution on it, so a link spelled
	// `ROADMAP.md` names `roadmap.md`. The order matters where a trailing part
	// of a path matches two moves: the first given wins, as in the Python.
	folded []foldedKey
	index  map[string]int
	// renamed maps a lowercased old stem to its old path, for a move that
	// changed a file's name and whose old name no other file carries.
	renamed map[string]string
}

type foldedKey struct {
	key, old string
}

// New builds a rewriter over `moves`, in the order given. `renamed` may be nil;
// the task mover never renames a file.
func New(moves []Move, renamed map[string]string) *Rewriter {
	r := &Rewriter{moves: map[string]string{}, index: map[string]int{}, renamed: renamed}
	for _, m := range moves {
		r.moves[m.From] = m.To
		for _, k := range []string{m.From, noext(m.From)} {
			k = strings.ToLower(k)
			if i, ok := r.index[k]; ok {
				r.folded[i].old = m.From
				continue
			}
			r.index[k] = len(r.folded)
			r.folded = append(r.folded, foldedKey{key: k, old: m.From})
		}
	}
	return r
}

var (
	wikiRe = regexp.MustCompile(`(!?)\[\[([^\]\|#\n]+)(#[^\]\|\n]*)?(\|[^\]\n]*)?\]\]`)
	// `\s` spelled as Python's: in a str pattern it is Unicode whitespace —
	// the no-break space, the vertical tab, the information separators — where
	// Go's is ASCII only, and a link the two read differently is a link one of
	// them breaks.
	mdLinkRe = regexp.MustCompile(`(!?\[[^\]\n]*\]\()(<[^>\n]+>|[^)` + pySpace + `]+)([` + pySpace + `]+"[^"\n]*")?\)`)
	schemeRe = regexp.MustCompile(`^[a-zA-Z][a-zA-Z0-9+.-]*:`)
)

// pySpace is the body of a character class matching what Python's `\s` does
// in a str pattern: `str.isspace()`.
const pySpace = `\s\x{0B}\x{1C}-\x{1F}\x{85}\p{Z}`

func noext(rel string) string {
	return strings.TrimSuffix(rel, ".md")
}

// pathMatch is the old path a path-shaped wikilink target names: a full path
// or a trailing part of one, with or without the `.md`. A target with no slash
// names a file by its basename and matches nothing here.
func (r *Rewriter) pathMatch(target string) (string, bool) {
	t := strings.ToLower(strings.Trim(strings.TrimSpace(target), "/"))
	if !strings.Contains(t, "/") {
		return "", false
	}
	for _, k := range []string{t, noext(t)} {
		if i, ok := r.index[k]; ok {
			return r.folded[i].old, true
		}
	}
	for _, f := range r.folded {
		if strings.HasSuffix(f.key, "/"+t) || strings.HasSuffix(f.key, "/"+noext(t)) {
			return f.old, true
		}
	}
	return "", false
}

// Text is `text` with every link into a moved file pointed at the new path,
// and the number of links it changed.
//
// `srcOld` and `srcNew` are this note's own paths before and after the run —
// the same when the note did not move — so a relative markdown link inside a
// moved note is recomputed from where the note now sits. `exists` answers for
// the vault after the run.
func (r *Rewriter) Text(text, srcOld, srcNew string, exists func(string) bool) (string, int) {
	count := 0
	text = replaceSubmatches(wikiRe, text, func(g []string, has []bool) string {
		bang, target, anchor := g[1], g[2], g[3]
		alias := g[4]
		withAlias := func(stem string) string {
			if has[4] {
				return bang + "[[" + stem + anchor + alias + "]]"
			}
			return bang + "[[" + stem + anchor + "|" + strings.TrimSpace(target) + "]]"
		}
		old, ok := r.pathMatch(target)
		if !ok && len(r.renamed) > 0 && !strings.Contains(target, "/") {
			if o, found := r.renamed[strings.ToLower(noext(strings.TrimSpace(target)))]; found {
				count++
				return withAlias(noext(r.moves[o]))
			}
		}
		if !ok {
			return g[0]
		}
		shown := r.moves[old]
		if !strings.HasSuffix(strings.TrimSpace(target), ".md") && strings.HasSuffix(old, ".md") {
			shown = noext(shown)
		}
		count++
		return withAlias(shown)
	})
	text = replaceSubmatches(mdLinkRe, text, func(g []string, _ []bool) string {
		head, raw, title := g[1], g[2], g[3]
		bare := raw
		if strings.HasPrefix(raw, "<") {
			bare = raw[1 : len(raw)-1]
		}
		if schemeRe.MatchString(bare) || strings.HasPrefix(bare, "#") {
			return g[0]
		}
		p, frag, _ := strings.Cut(bare, "#")
		dec := unquote(p)
		if dec == "" {
			return g[0]
		}
		rooted := strings.HasPrefix(dec, "/")
		var resolved string
		if rooted {
			resolved = normpath(strings.TrimLeft(dec, "/"))
		} else {
			resolved = normpath(joinDir(srcOld, dec))
		}
		var targetNew string
		if i, ok := r.index[strings.ToLower(resolved)]; ok {
			targetNew = r.moves[r.folded[i].old]
		} else {
			if srcOld == srcNew || rooted {
				return g[0]
			}
			// This note moved and the link is relative to where it was: keep
			// it pointing at the same file from the note's new folder.
			if !exists(resolved) {
				return g[0]
			}
			targetNew = resolved
		}
		var newPath string
		if rooted {
			newPath = "/" + targetNew
		} else {
			newPath = relpath(targetNew, path.Dir(srcNew))
		}
		suffix := ""
		if frag != "" {
			suffix = "#" + frag
		}
		var out string
		switch {
		case strings.HasPrefix(raw, "<"):
			out = "<" + newPath + suffix + ">"
		case strings.Contains(p, "%"):
			out = quote(newPath) + suffix
		default:
			out = strings.ReplaceAll(newPath, " ", "%20") + suffix
		}
		if out == raw {
			return g[0]
		}
		count++
		return head + out + title + ")"
	})
	return text, count
}

// replaceSubmatches is regexp's ReplaceAllStringFunc with the groups, and
// whether each group took part in the match: an alias that is absent and an
// alias that is empty are different links.
func replaceSubmatches(re *regexp.Regexp, s string, f func(groups []string, has []bool) string) string {
	var b strings.Builder
	last := 0
	for _, loc := range re.FindAllStringSubmatchIndex(s, -1) {
		n := len(loc) / 2
		groups := make([]string, n)
		has := make([]bool, n)
		for i := 0; i < n; i++ {
			if loc[2*i] >= 0 {
				groups[i] = s[loc[2*i]:loc[2*i+1]]
				has[i] = true
			}
		}
		b.WriteString(s[last:loc[0]])
		b.WriteString(f(groups, has))
		last = loc[1]
	}
	b.WriteString(s[last:])
	return b.String()
}

// joinDir is Python's `os.path.join(os.path.dirname(src), rel)`.
func joinDir(src, rel string) string {
	dir := ""
	if i := strings.LastIndex(src, "/"); i >= 0 {
		dir = src[:i]
	}
	if dir == "" {
		return rel
	}
	return dir + "/" + rel
}

// normpath is Python's `os.path.normpath` on a relative POSIX path.
func normpath(p string) string {
	return path.Clean(p)
}

// relpath is Python's `os.path.relpath(target, start)` for two paths relative
// to the same root.
func relpath(target, start string) string {
	rel, err := filepath.Rel(filepath.FromSlash(start), filepath.FromSlash(target))
	if err != nil {
		return target
	}
	return filepath.ToSlash(rel)
}

// unquote is Python's `urllib.parse.unquote`: `%XX` decoded, anything else
// left as it is, and bytes that are not UTF-8 replaced.
func unquote(s string) string {
	if !strings.Contains(s, "%") {
		return s
	}
	var b []byte
	for i := 0; i < len(s); i++ {
		if s[i] == '%' && i+2 < len(s) && isHex(s[i+1]) && isHex(s[i+2]) {
			b = append(b, unhex(s[i+1])<<4|unhex(s[i+2]))
			i += 2
			continue
		}
		b = append(b, s[i])
	}
	if utf8.Valid(b) {
		return string(b)
	}
	return strings.ToValidUTF8(string(b), "�")
}

func isHex(c byte) bool {
	return ('0' <= c && c <= '9') || ('a' <= c && c <= 'f') || ('A' <= c && c <= 'F')
}

func unhex(c byte) byte {
	switch {
	case '0' <= c && c <= '9':
		return c - '0'
	case 'a' <= c && c <= 'f':
		return c - 'a' + 10
	default:
		return c - 'A' + 10
	}
}

// quote is Python's `urllib.parse.quote(s, safe="/.-_~")`.
func quote(s string) string {
	const hexdigits = "0123456789ABCDEF"
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		c := s[i]
		if ('a' <= c && c <= 'z') || ('A' <= c && c <= 'Z') || ('0' <= c && c <= '9') ||
			strings.IndexByte("_.-~/", c) >= 0 {
			b.WriteByte(c)
			continue
		}
		b.WriteByte('%')
		b.WriteByte(hexdigits[c>>4])
		b.WriteByte(hexdigits[c&15])
	}
	return b.String()
}
