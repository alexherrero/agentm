package enrich

import (
	"fmt"
	"strings"
	"time"
)

// Project records (agentm-vault § Projects and tasks, "Dreaming enriches a card
// beyond what the session had"). Inside the projects space the deep pass reads
// everything as input, and it writes the same additive section and frontmatter
// it writes on a card into a project's charter and into its decisions/,
// designs/ and research/ notes. It never writes a tracker, a plan or a progress
// log: those are the session's, and a nightly writer would race the session
// that owns them.
//
// A record is not a card. Its frontmatter belongs to whatever wrote it — a
// research bundle's source_url and fingerprint, a charter's kind — so a pass
// merges into it rather than rendering a card over it. Every field the record
// carries stays; the fields a pass writes that a record can hold are set or
// added to; and no filing verdict is written, because a record is not filed.

// ProjectsSpace is the vault-root space that holds projects, compared without case.
const ProjectsSpace = "projects"

// recordDirs are the directories under a project whose notes are records.
var recordDirs = map[string]bool{"decisions": true, "designs": true, "research": true}

// charterNames are a project's charter: `_index.md` today, `charter.md` once the
// migration renames it.
var charterNames = map[string]bool{"_index.md": true, "charter.md": true}

// sessionFiles are never written by a pass, wherever they sit.
var sessionFiles = map[string]bool{"tracker.md": true, "plan.md": true, "progress.md": true}

func relParts(rel string) []string {
	return strings.Split(strings.ReplaceAll(rel, "\\", "/"), "/")
}

// InProjectsSpace reports whether `rel` sits inside a project: under
// `Projects/<slug>/`, where the slug is not a retired or hidden folder.
func InProjectsSpace(rel string) bool {
	parts := relParts(rel)
	return len(parts) >= 3 && strings.EqualFold(parts[0], ProjectsSpace) && parts[1] != "" &&
		!strings.HasPrefix(parts[1], "_") && !strings.HasPrefix(parts[1], ".")
}

// ProjectOf is the slug of the project `rel` sits in, or "".
func ProjectOf(rel string) string {
	if !InProjectsSpace(rel) {
		return ""
	}
	return relParts(rel)[1]
}

// IsProjectRecord reports whether `rel` is a record a pass may write: a
// project's charter at its root, or a note under its decisions/, designs/ or
// research/ folder. Never a tracker, a plan or a progress log, and nothing in
// a hidden folder.
func IsProjectRecord(rel string) bool {
	if !InProjectsSpace(rel) || !strings.HasSuffix(rel, ".md") {
		return false
	}
	parts := relParts(rel)
	name := parts[len(parts)-1]
	if sessionFiles[name] {
		return false
	}
	for _, seg := range parts[2:] {
		if strings.HasPrefix(seg, ".") {
			return false
		}
	}
	if len(parts) == 3 {
		return charterNames[name]
	}
	return recordDirs[strings.ToLower(parts[2])]
}

// ComposeRecord builds the record a judgment writes from the record as it stood.
//
// The deep pass adds its dated section below the record's text, in place of any
// section an earlier deep pass added; the light pass leaves the body as it was.
// The frontmatter is merged: `summary` and `related` are set, the record's own
// `tags` and `aliases` are kept with the pass's added after them, a deep pass's
// `importance_proposed` is set, and `updated` and the three stamps say when and
// under what the pass ran. `importance` stays the operator's, and no `title`,
// `type`, `status`, `filing_confidence`, `confidence` or `lifecycle` is written.
//
// It refuses rather than writes when the record's text would not survive byte
// for byte — the guard Compose keeps for a card.
func ComposeRecord(previous string, r Response, s Stamp, depth Depth, offered []Neighbour) (string, error) {
	front, prevBody := splitNote(previous)
	if front == "" {
		return "", fmt.Errorf("enrich: refusing to write a record that has no frontmatter block")
	}
	captured, oldSection, after := splitDreaming(prevBody)

	when := s.At
	if when.IsZero() {
		when = time.Now()
	}
	section := oldSection
	if depth == DepthDeep {
		section = ""
		if add := addition(r.Body); add != "" {
			section = fmt.Sprintf("%s (%s)\n\n%s\n", DreamingHeading, when.UTC().Format("2006-01-02"), add)
		}
	}

	blocks := blockListKeys(front)
	fields := map[string]string{}
	var order []string
	set := func(key, value string) {
		if strings.TrimSpace(value) == "" {
			return
		}
		if _, ok := fields[key]; !ok {
			order = append(order, key)
		}
		fields[key] = value
	}
	set("summary", yamlScalarOrEmpty(r.Summary))
	if ids := relatedIDs(r.Related, offered); len(ids) > 0 {
		links := make([]string, 0, len(ids))
		for _, id := range ids {
			links = append(links, `"[[`+id+`]]"`)
		}
		set("related", "["+strings.Join(links, ", ")+"]")
	}
	for _, key := range []string{"tags", "aliases"} {
		if blocks[key] {
			continue // a block list is the record's own shape, left exactly as it is
		}
		extra := r.Tags
		if key == "aliases" {
			extra = r.Aliases
		}
		if merged, ok := unionFlowList(rawFrontmatterValue(previous, key), extra); ok {
			set(key, merged)
		}
	}
	if depth == DepthDeep && r.ImportanceProposed > 0 {
		set("importance_proposed", fmt.Sprintf("%d", r.ImportanceProposed))
	}
	set("updated", when.UTC().Format("2006-01-02"))
	version := s.Version
	if version == "" {
		version = PassVersion
	}
	set("enriched_by", yamlScalar(version))
	set("rules_hash", yamlScalarOrEmpty(s.RulesHash))
	if !s.At.IsZero() {
		set("enriched_at", yamlScalar(s.At.UTC().Format(StampFormat)))
	}

	next := mergeFrontmatter(front, fields, order) + joinBody(captured, section, after)
	if _, nextBody := splitNote(next); !strings.HasPrefix(nextBody, captured) {
		return "", fmt.Errorf("enrich: refusing to write — composing the record would change its text")
	}
	return next, nil
}

func yamlScalarOrEmpty(v string) string {
	if strings.TrimSpace(v) == "" {
		return ""
	}
	return yamlScalar(v)
}

// unionFlowList adds `extra` after the values of a one-line flow list, keeping
// the list's own values and order. It reports false when nothing new is added
// or when the existing value is not a one-line list it can extend safely.
func unionFlowList(existing string, extra []string) (string, bool) {
	existing = strings.TrimSpace(existing)
	var values []string
	seen := map[string]bool{}
	if existing != "" {
		if !strings.HasPrefix(existing, "[") || !strings.HasSuffix(existing, "]") {
			return "", false
		}
		for _, v := range strings.Split(existing[1:len(existing)-1], ",") {
			if v = strings.TrimSpace(v); v != "" {
				values = append(values, v)
				seen[strings.ToLower(strings.Trim(v, `"'`))] = true
			}
		}
	}
	added := false
	for _, v := range extra {
		v = strings.TrimSpace(v)
		if v == "" || seen[strings.ToLower(v)] {
			continue
		}
		seen[strings.ToLower(v)] = true
		values = append(values, yamlScalar(v))
		added = true
	}
	if !added {
		return "", false
	}
	return "[" + strings.Join(values, ", ") + "]", true
}

// topKey is a frontmatter line's top-level key, or "" for a continuation, a
// comment or a line that is not a key.
func topKey(line string) string {
	if line == "" || line[0] == ' ' || line[0] == '\t' || line[0] == '-' || line[0] == '#' {
		return ""
	}
	k, _, ok := strings.Cut(line, ":")
	if !ok {
		return ""
	}
	return strings.TrimSpace(k)
}

func isContinuation(line string) bool {
	return line != "" && (line[0] == ' ' || line[0] == '\t' || line[0] == '-')
}

// blockListKeys are the keys whose value is a block under the key line.
func blockListKeys(front string) map[string]bool {
	out := map[string]bool{}
	lines := strings.Split(front, "\n")
	for i, line := range lines {
		key := topKey(line)
		if key == "" || i+1 >= len(lines) {
			continue
		}
		_, value, _ := strings.Cut(line, ":")
		if strings.TrimSpace(value) == "" && isContinuation(lines[i+1]) {
			out[key] = true
		}
	}
	return out
}

// mergeFrontmatter replaces the named keys' lines, continuations with them, and
// appends the keys the block lacks before its closing fence. Every other line
// stays byte for byte, in its place.
func mergeFrontmatter(front string, fields map[string]string, order []string) string {
	end := strings.Index(front[4:], "\n---")
	if end < 0 {
		return front
	}
	block, tail := front[4:4+end], front[4+end:]
	lines := strings.Split(block, "\n")
	out := make([]string, 0, len(lines)+len(order))
	done := map[string]bool{}
	for i := 0; i < len(lines); i++ {
		key := topKey(lines[i])
		value, ok := fields[key]
		if key == "" || !ok || done[key] {
			out = append(out, lines[i])
			continue
		}
		out = append(out, key+": "+value)
		done[key] = true
		for i+1 < len(lines) && isContinuation(lines[i+1]) {
			i++
		}
	}
	for _, key := range order {
		if !done[key] {
			out = append(out, key+": "+fields[key])
		}
	}
	return "---\n" + strings.Join(out, "\n") + tail
}
