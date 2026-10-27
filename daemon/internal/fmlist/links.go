package fmlist

import (
	"path"
	"regexp"
	"strings"
)

// Link-valued keys (task 193 LD-5, agentm-vault). `supersedes`,
// `superseded_by`, `related` and `derived_from` name other notes, and their
// values come in every shape the writers have used: a bare memory-root path
// (`memory/semantic/x.md`), a bare name (`x`), a quoted wikilink (`"[[x]]"`,
// `"[[agent/memory/semantic/x|label]]"`), or a flow or block list of any of
// those. The writers are moving to the quoted wikilink, the form Obsidian draws
// as a link, and every reader must read both before any writer switches. This
// is the one parser the Go readers share; the Python side is
// `harness/skills/memory/scripts/note_links.py`, held to the same cases.

// Link is one note a link-valued key names.
type Link struct {
	// Target is the path or name as written, without quotes, brackets, label
	// or anchor.
	Target string
	// Source marks a `<source id> at <version>` value from the sources layer:
	// a source version, not a note.
	Source bool
}

var (
	wikilinkRe      = regexp.MustCompile(`\[\[([^\[\]]+?)\]\]`)
	sourceVersionRe = regexp.MustCompile(`^\S+ at \S+$`)
)

// Stem is the file name the link names, without `.md`.
func (l Link) Stem() string {
	return strings.TrimSuffix(path.Base(strings.TrimRight(l.Target, "/")), ".md")
}

// MemoryRel is the target as a memory-root path with `.md`. prefix is the
// memory root's own path in the vault (`agent`, or "" for a flat vault): a
// wikilink names a note from the vault root and a bare path from the memory
// root, and this reads both the same way.
func (l Link) MemoryRel(prefix string) string {
	t := strings.TrimLeft(l.Target, "/")
	if p := strings.Trim(prefix, "/"); p != "" && strings.HasPrefix(t, p+"/") {
		t = t[len(p)+1:]
	}
	if !strings.HasSuffix(t, ".md") {
		t += ".md"
	}
	return t
}

func linksOne(item string) []Link {
	s := Unquote(strings.TrimSpace(item))
	if s == "" {
		return nil
	}
	if found := wikilinkRe.FindAllStringSubmatch(s, -1); len(found) > 0 {
		var out []Link
		for _, m := range found {
			target := m[1]
			if i := strings.IndexAny(target, "|#"); i >= 0 {
				target = target[:i]
			}
			if target = strings.TrimSpace(target); target != "" {
				out = append(out, Link{Target: target})
			}
		}
		return out
	}
	s = Unquote(strings.TrimSpace(strings.Trim(s, "[]")))
	if s == "" {
		return nil
	}
	if sourceVersionRe.MatchString(s) && !strings.Contains(s, "/") {
		return []Link{{Target: s, Source: true}}
	}
	return []Link{{Target: s}}
}

// Links reads a value as a frontmatter reader hands it back, the text after
// `key:`: a scalar, a wikilink, or a flow list of either. Nil for an empty
// value.
func Links(value string) []Link {
	s := strings.TrimSpace(value)
	if s == "" {
		return nil
	}
	if strings.HasPrefix(s, "[") && strings.HasSuffix(s, "]") && !isOneWikilink(s) {
		var out []Link
		for _, item := range SplitFlow(s[1 : len(s)-1]) {
			out = append(out, linksOne(item)...)
		}
		return out
	}
	return linksOne(s)
}

// isOneWikilink says whether s is exactly one `[[…]]`.
func isOneWikilink(s string) bool {
	m := wikilinkRe.FindStringIndex(s)
	return m != nil && m[0] == 0 && m[1] == len(s)
}

// LinksOf reads a link-valued key from a note's frontmatter, whether it is a
// scalar, a flow list or a block list. Nil when the key is absent or empty.
func LinksOf(text, key string) []Link {
	var out []Link
	for _, item := range Items(text, key) {
		out = append(out, linksOne(item)...)
	}
	return out
}

// First is the first note links name, skipping a source version, and false
// when there is none.
func First(links []Link) (Link, bool) {
	for _, l := range links {
		if !l.Source {
			return l, true
		}
	}
	return Link{}, false
}
