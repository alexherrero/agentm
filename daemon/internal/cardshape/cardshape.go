// Package cardshape holds the card's field order, the one definition every Go
// writer of a class card shares.
//
// agentm-vault § The card (session 1, decided) fixes the order Obsidian's
// properties panel shows a memory's fields in: what you read first, what the
// machinery reads last. The Python half keeps the same two lists in
// harness/skills/memory/scripts/card_shape.py, and scripts/test_card_shape.py
// fails when the two part. The card gates read the Python half.
package cardshape

import (
	"sort"
	"strings"
)

// ReadOrder is what you read, in panel order. `type` and `kind` share a slot;
// `lifecycle_since` sits beside `lifecycle`, where every writer puts it.
var ReadOrder = []string{
	"title", "type", "kind", "summary", "why", "importance",
	"status", "lifecycle", "lifecycle_since", "filing_confidence",
	"source", "source_url", "source_id", "source_fetched", "trust",
	"created", "updated", "tags",
	"related", "supersedes", "superseded_by",
	"project", "task",
}

// MachineOrder is what the machinery reads, last. The design names the first
// seven; the rest are the machine fields the corpus carries, in a fixed order.
var MachineOrder = []string{
	"slug", "confidence", "enriched_by", "enriched_at", "rules_hash",
	"fingerprint", "importance_proposed",
	"aliases", "occurrences", "derived_from", "source_hash", "source_version",
	"via", "surface", "instructions", "review_flags",
	"promoted_at", "promoted_to", "probe", "backfilled",
}

var (
	readIndex    = indexOf(ReadOrder)
	machineIndex = indexOf(MachineOrder)
)

func indexOf(keys []string) map[string]int {
	m := make(map[string]int, len(keys))
	for i, k := range keys {
		m[k] = i
	}
	return m
}

// rank places a key: the read block, then keys neither block names, then the
// machine block.
func rank(key string) (int, int) {
	if key == "" {
		return -1, 0
	}
	if i, ok := readIndex[key]; ok {
		return 0, i
	}
	if i, ok := machineIndex[key]; ok {
		return 2, i
	}
	return 1, 0
}

type entry struct {
	key   string
	lines []string
}

// topKey returns the key a frontmatter line opens, or "" for a continuation,
// a blank, a comment or a list item.
func topKey(line string) string {
	if line == "" {
		return ""
	}
	c := line[0]
	if !(c == '_' || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')) {
		return ""
	}
	for i := 1; i < len(line); i++ {
		switch ch := line[i]; {
		case ch == ':':
			return line[:i]
		case ch == ' ' || ch == '\t':
			if rest := strings.TrimLeft(line[i:], " \t"); strings.HasPrefix(rest, ":") {
				return line[:i]
			}
			return ""
		case ch == '_' || ch == '-' || (ch >= 'a' && ch <= 'z') ||
			(ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9'):
		default:
			return ""
		}
	}
	return ""
}

// Reorder returns content with its frontmatter's top-level keys in the card's
// order: the read block, then any key neither block names in its original
// relative order, then the machine block. Every line stays with its key and the
// body is byte for byte what it was. A note already in order, or one without a
// frontmatter block, comes back as the same string.
func Reorder(content string) string {
	if !strings.HasPrefix(content, "---\n") {
		return content
	}
	i := strings.Index(content[3:], "\n---\n")
	if i <= 0 {
		return content
	}
	end := 3 + i
	block, rest := content[4:end], content[end+1:]
	var entries []entry
	for _, line := range strings.Split(block, "\n") {
		if k := topKey(line); k != "" {
			entries = append(entries, entry{key: k, lines: []string{line}})
		} else if len(entries) > 0 {
			entries[len(entries)-1].lines = append(entries[len(entries)-1].lines, line)
		} else {
			entries = append(entries, entry{lines: []string{line}})
		}
	}
	ordered := make([]entry, len(entries))
	copy(ordered, entries)
	sort.SliceStable(ordered, func(a, b int) bool {
		ga, ia := rank(ordered[a].key)
		gb, ib := rank(ordered[b].key)
		if ga != gb {
			return ga < gb
		}
		return ia < ib
	})
	same := true
	for n := range entries {
		if entries[n].key != ordered[n].key || len(entries[n].lines) != len(ordered[n].lines) ||
			entries[n].lines[0] != ordered[n].lines[0] {
			same = false
			break
		}
	}
	if same {
		return content
	}
	var lines []string
	for _, e := range ordered {
		lines = append(lines, e.lines...)
	}
	return "---\n" + strings.Join(lines, "\n") + "\n" + rest
}

// CollisionStopwords are the words a colliding name never grows by, because
// they say nothing about the note. The Python writers read the same list
// (card_shape.COLLISION_STOPWORDS), and scripts/test_card_shape.py fails when
// the two part.
var CollisionStopwords = []string{
	"the", "and", "for", "with", "that", "this", "from", "into",
	"are", "was", "but", "not", "its",
}

// Meaningful reports whether a colliding name may grow by w: three characters
// or more, not only digits, and not a stopword (agentm-vault § The card: the
// collision rule produces a meaningful word, never a counter).
func Meaningful(w string) bool {
	if len(w) < 3 || strings.Trim(w, "0123456789") == "" {
		return false
	}
	for _, s := range CollisionStopwords {
		if w == s {
			return false
		}
	}
	return true
}
