package capture

// The same outside source updates its note (agentm-vault § Capture, amended
// 2026-09-28; the operator's ruling 6b of 2026-09-24).
//
// A capture of something already in the vault — the same page clipped twice,
// the same article ingested again, the same registry unit captured once more —
// used to reserve a new name beside the old note. It now updates that note in
// place: the path, the links to it and its slug stay, the frontmatter merges
// (the new capture's fields win, the old note's other fields are kept, and the
// operator's `importance` and the note's `created` are never overwritten), the
// body is replaced, and git keeps the old wording.
//
// "The same" is the same source and the same title. One unit of source material
// can yield several distinct memories — the facts an article's ingest draws
// out share its `source_url`, and the memories mined from one message share its
// `source_id` — and each of those is its own note. Keying on the source alone
// would overwrite one fact with the next.

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

// registryIdentity is a source id's shape: `<namespace>:<ref>`. A bare word
// (crystallize stamps `source_id: crystallize` on every lesson) is not one, and
// a session id is never one — every card a session writes would share it.
var registryIdentity = regexp.MustCompile(`^[a-z][a-z0-9_.-]*:\S+$`)

// sourceKey is the field and value a capture's outside source is found by:
// its registry identity when it names one, else the address of its page.
func sourceKey(id, url string) (field, value string) {
	id = strings.TrimSpace(id)
	if registryIdentity.MatchString(id) && !strings.HasPrefix(strings.ToLower(id), "session:") {
		return "source_id", id
	}
	if u := strings.TrimSpace(url); u != "" {
		return "source_url", u
	}
	return "", ""
}

// frontBlocks splits a note into its frontmatter's top-level blocks (a key's
// line and its continuation lines, in order) and everything after the block.
// ok is false for a note with no fenced block.
func frontBlocks(raw string) (keys []string, blocks map[string][]string, rest string, ok bool) {
	if !strings.HasPrefix(raw, "---\n") {
		return nil, nil, raw, false
	}
	end := strings.Index(raw[4:], "\n---\n")
	if end < 0 {
		return nil, nil, raw, false
	}
	end += 4
	blocks = map[string][]string{}
	current := ""
	for _, line := range strings.Split(raw[4:end], "\n") {
		if line != "" && !strings.ContainsRune(" \t#-", rune(line[0])) {
			if k, _, found := strings.Cut(line, ":"); found {
				current = strings.TrimSpace(k)
				if _, seen := blocks[current]; !seen {
					keys = append(keys, current)
				}
				blocks[current] = []string{line}
				continue
			}
		}
		if current != "" {
			blocks[current] = append(blocks[current], line)
		}
	}
	return keys, blocks, raw[end+5:], true
}

func frontValue(blocks map[string][]string, key string) string {
	lines := blocks[key]
	if len(lines) == 0 {
		return ""
	}
	_, v, _ := strings.Cut(lines[0], ":")
	v = strings.TrimSpace(v)
	if len(v) >= 2 && (v[0] == '"' && v[len(v)-1] == '"' || v[0] == '\'' && v[len(v)-1] == '\'') {
		v = v[1 : len(v)-1]
	}
	return v
}

// sameSource finds the active card in `dirs` that this capture is again: the
// same source, the same title. It returns the vault-relative path and the note
// as it is on disk, or "" when there is none. When the vault holds more than
// one — a pair this rule would have prevented — the first by path is updated.
func (c *Capturer) sameSource(dirs []string, field, value, title string) (string, string) {
	if field == "" || value == "" || slugify(title) == "" {
		return "", ""
	}
	var matches []string
	raws := map[string]string{}
	for _, dir := range dirs {
		entries, err := os.ReadDir(filepath.Join(c.cfg.VaultPath, filepath.FromSlash(dir)))
		if err != nil {
			continue
		}
		for _, e := range entries {
			if e.IsDir() || !strings.HasSuffix(e.Name(), ".md") {
				continue
			}
			rel := filepath.ToSlash(filepath.Join(dir, e.Name()))
			rawBytes, err := os.ReadFile(filepath.Join(c.cfg.VaultPath, filepath.FromSlash(rel)))
			if err != nil {
				continue
			}
			raw := string(rawBytes)
			_, blocks, _, ok := frontBlocks(raw)
			if !ok || frontValue(blocks, "type") == "" || frontValue(blocks, "kind") != "" {
				continue // a record is never the note a capture updates
			}
			if frontValue(blocks, field) != value || slugify(frontValue(blocks, "title")) != slugify(title) {
				continue
			}
			switch strings.ToLower(frontValue(blocks, "lifecycle")) {
			case "superseded", "archived":
				continue
			}
			if frontValue(blocks, "superseded_by") != "" || strings.ToLower(frontValue(blocks, "status")) == "superseded" {
				continue
			}
			matches = append(matches, rel)
			raws[rel] = raw
		}
	}
	if len(matches) == 0 {
		return "", ""
	}
	sort.Strings(matches)
	return matches[0], raws[matches[0]]
}

// mergeInPlace is the note an update in place writes: the fresh render's
// frontmatter, with every field of the old note it does not carry kept, and
// the old note's `created` and `importance` in place of the render's — the day
// the memory came into existence, and the operator's reading, which no writer
// overwrites. The body is the fresh render's.
func mergeInPlace(old, fresh string) string {
	_, oldBlocks, _, okOld := frontBlocks(old)
	freshKeys, freshBlocks, body, okFresh := frontBlocks(fresh)
	if !okOld || !okFresh {
		return fresh
	}
	oldKeys, _, _, _ := frontBlocks(old)
	var out []string
	for _, k := range freshKeys {
		lines := freshBlocks[k]
		if (k == "created" || k == "importance") && len(oldBlocks[k]) > 0 {
			lines = oldBlocks[k]
		}
		out = append(out, lines...)
	}
	for _, k := range oldKeys {
		if _, carried := freshBlocks[k]; !carried {
			out = append(out, oldBlocks[k]...)
		}
	}
	return "---\n" + strings.Join(out, "\n") + "\n---\n" + body
}
