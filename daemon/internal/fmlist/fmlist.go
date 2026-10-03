// Package fmlist reads and rewrites one top-level key of a note's frontmatter,
// list-valued or not, the way a YAML reader would see it.
//
// Several writers parsed frontmatter lists with a regex over the whole note and a
// split on every comma, and the release review of task 182 found what that
// costs. A quoted alias holding a comma came back as two aliases. One holding a
// quote gained a backslash on every rewrite. A stamp written as a block list
// (`key:` then `  - item` lines, the shape Obsidian's property editor writes)
// read as empty, and stamping it left the old items under a new value, which
// YAML refuses. And a stamp line quoted in a note's body counted as a stamp.
// Everything here looks only inside the frontmatter fences, splits a flow list
// only on commas outside quotes, and treats a key's continuation lines as part
// of its value.
package fmlist

import (
	"encoding/json"
	"strings"
)

// bounds is the frontmatter block's line span: the index of the first line after
// the opening fence and of the closing fence. ok is false for a note with no
// closed frontmatter.
func bounds(lines []string) (start, end int, ok bool) {
	if len(lines) == 0 || strings.TrimRight(lines[0], "\r") != "---" {
		return 0, 0, false
	}
	for i := 1; i < len(lines); i++ {
		if strings.TrimRight(lines[i], "\r") == "---" {
			return 1, i, true
		}
	}
	return 0, 0, false
}

// span is the line range [from, to) a top-level key occupies inside the
// frontmatter: its own line and every continuation (indented, list item, blank
// or comment) line up to the next top-level key or the closing fence.
func span(lines []string, key string) (from, to int, ok bool) {
	start, end, ok := bounds(lines)
	if !ok {
		return 0, 0, false
	}
	for i := start; i < end; i++ {
		if keyOf(lines[i]) != key {
			continue
		}
		j := i + 1
		for j < end && keyOf(lines[j]) == "" {
			j++
		}
		return i, j, true
	}
	return 0, 0, false
}

// keyOf is a line's top-level key, or "" for a continuation, blank or comment.
func keyOf(line string) string {
	if line == "" || line[0] == ' ' || line[0] == '\t' || line[0] == '-' || line[0] == '#' {
		return ""
	}
	k, _, ok := strings.Cut(line, ":")
	if !ok {
		return ""
	}
	return strings.TrimSpace(k)
}

// Has reports whether the frontmatter carries the key with a non-empty value
// (a scalar, a flow list or at least one block-list item).
func Has(text, key string) bool {
	return len(Items(text, key)) > 0
}

// Items is the key's value as items, unquoted: a flow list's elements (split on
// commas outside quotes), a block list's `- item` lines, or a lone scalar as a
// one-item list. Absent or empty is nil.
func Items(text, key string) []string {
	lines := strings.Split(text, "\n")
	from, to, ok := span(lines, key)
	if !ok {
		return nil
	}
	_, value, _ := strings.Cut(lines[from], ":")
	value = strings.TrimSpace(strings.TrimRight(value, "\r"))
	var out []string
	switch {
	case value == "":
		for _, l := range lines[from+1 : to] {
			t := strings.TrimSpace(strings.TrimRight(l, "\r"))
			if strings.HasPrefix(t, "- ") || t == "-" {
				if v := Unquote(strings.TrimSpace(strings.TrimPrefix(t, "-"))); v != "" {
					out = append(out, v)
				}
			}
		}
	case strings.HasPrefix(value, "[") && strings.HasSuffix(value, "]"):
		for _, item := range SplitFlow(value[1 : len(value)-1]) {
			if v := Unquote(item); v != "" {
				out = append(out, v)
			}
		}
	default:
		if v := Unquote(value); v != "" {
			out = append(out, v)
		}
	}
	return out
}

// Replace puts `line` (a whole `key: value` line, or several lines) where the
// key and its continuation lines were, and reports whether the key was there.
// Nothing outside the frontmatter is touched.
func Replace(text, key, line string) (string, bool) {
	lines := strings.Split(text, "\n")
	from, to, ok := span(lines, key)
	if !ok {
		return text, false
	}
	out := append(append(append([]string{}, lines[:from]...), line), lines[to:]...)
	return strings.Join(out, "\n"), true
}

// Remove drops the key and its continuation lines, and reports whether it was
// there.
func Remove(text, key string) (string, bool) {
	lines := strings.Split(text, "\n")
	from, to, ok := span(lines, key)
	if !ok {
		return text, false
	}
	out := append(append([]string{}, lines[:from]...), lines[to:]...)
	return strings.Join(out, "\n"), true
}

// Block is the key's lines as written — its own line and its continuations —
// or "" when the key is absent. A carry that copies a field copies this, so a
// block list arrives whole.
func Block(text, key string) string {
	lines := strings.Split(text, "\n")
	from, to, ok := span(lines, key)
	if !ok {
		return ""
	}
	// Trailing blank continuation lines belong to the gap, not the value.
	for to > from+1 && strings.TrimSpace(lines[to-1]) == "" {
		to--
	}
	return strings.Join(lines[from:to], "\n")
}

// SplitFlow splits a flow list's inside on the commas that are not inside a
// quoted item. Items keep their quotes; Unquote takes them off.
func SplitFlow(inner string) []string {
	var out []string
	var cur strings.Builder
	quote := byte(0)
	for i := 0; i < len(inner); i++ {
		c := inner[i]
		switch {
		case quote == '"' && c == '\\' && i+1 < len(inner):
			cur.WriteByte(c)
			i++
			cur.WriteByte(inner[i])
			continue
		case quote != 0 && c == quote:
			if quote == '\'' && i+1 < len(inner) && inner[i+1] == '\'' {
				cur.WriteString("''")
				i++
				continue
			}
			quote = 0
		case quote == 0 && (c == '"' || c == '\''):
			quote = c
		case quote == 0 && c == ',':
			if s := strings.TrimSpace(cur.String()); s != "" {
				out = append(out, s)
			}
			cur.Reset()
			continue
		}
		cur.WriteByte(c)
	}
	if s := strings.TrimSpace(cur.String()); s != "" {
		out = append(out, s)
	}
	return out
}

// Unquote is a scalar without its YAML quoting: a double-quoted value with its
// escapes resolved, a single-quoted one with ” as ', a plain one trimmed.
func Unquote(s string) string {
	s = strings.TrimSpace(s)
	if len(s) >= 2 && s[0] == '"' && s[len(s)-1] == '"' {
		var v string
		if err := json.Unmarshal([]byte(s), &v); err == nil {
			return v
		}
		return s[1 : len(s)-1]
	}
	if len(s) >= 2 && s[0] == '\'' && s[len(s)-1] == '\'' {
		return strings.ReplaceAll(s[1:len(s)-1], "''", "'")
	}
	return s
}

// Quote is a value written for a flow list: double-quoted, with its quotes and
// backslashes escaped, whenever leaving it plain could change what a reader sees
// — a comma would split it, and a YAML indicator would change its type.
func Quote(s string) string {
	s = strings.TrimSpace(s)
	if s != "" && !strings.ContainsAny(s, ",:#[]{}&*!|>%@`\"'\n\\") &&
		!strings.HasPrefix(s, "-") && !strings.HasPrefix(s, "?") {
		return s
	}
	b, _ := json.Marshal(s)
	return string(b)
}
