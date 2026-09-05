package dreaming

import (
	"crypto/sha256"
	"encoding/hex"
	"regexp"
	"strings"
)

// The Python layer reads and edits frontmatter line by line, never through a
// YAML round-trip, and so does this port: the same first-wins field read
// (`filing_engine._frontmatter`), the same in-place patch that keeps every
// other line byte for byte (`dream._patch_frontmatter`), the same body
// fingerprint (`fingerprint.compute_fingerprint`). Parity fixtures assert
// the Python outputs, so these are ports, not reinterpretations.

// ParseFrontmatter is `filing_engine._frontmatter`: (fields, body). Only a
// line starting a top-level `key: value` counts; the first occurrence of a
// key wins; surrounding quotes are stripped. A note without a fenced block
// has no fields and is its own body.
func ParseFrontmatter(text string) (map[string]string, string) {
	fields := map[string]string{}
	if !strings.HasPrefix(text, "---\n") {
		return fields, text
	}
	lines := strings.Split(text, "\n")
	end := -1
	for i := 1; i < len(lines); i++ {
		if strings.TrimSpace(lines[i]) == "---" {
			end = i
			break
		}
	}
	if end < 0 {
		return fields, text
	}
	for _, raw := range lines[1:end] {
		if raw == "" || strings.ContainsRune(" \t#-", rune(raw[0])) {
			continue
		}
		key, value, ok := strings.Cut(raw, ":")
		if !ok {
			continue
		}
		k := strings.TrimSpace(key)
		if _, seen := fields[k]; !seen {
			v := strings.TrimSpace(value)
			v = strings.Trim(v, `"`)
			v = strings.Trim(v, `'`)
			fields[k] = v
		}
	}
	return fields, strings.Join(lines[end+1:], "\n")
}

// ListField reads a `[a, b, c]` value as its items; a bare scalar is a
// one-item list; empty is empty.
func ListField(value string) []string {
	v := strings.TrimSpace(value)
	if v == "" || v == "[]" {
		return nil
	}
	if strings.HasPrefix(v, "[") && strings.HasSuffix(v, "]") {
		v = v[1 : len(v)-1]
	}
	var out []string
	for _, part := range strings.Split(v, ",") {
		p := strings.TrimSpace(part)
		p = strings.Trim(p, `"`)
		p = strings.Trim(p, `'`)
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}

// Update is one `key: value` a patch sets, in order.
type Update struct{ Key, Value string }

// PatchFrontmatter is `dream._patch_frontmatter`, byte for byte: a key
// already in the block is rewritten in place (`key: value`, the line
// re-rendered), a key not in it is appended at the end of the block, and a
// note without a block gets one prepended. Everything else is untouched.
func PatchFrontmatter(content string, updates []Update) string {
	if !strings.HasPrefix(content, "---\n") {
		lines := []string{"---"}
		for _, u := range updates {
			lines = append(lines, u.Key+": "+u.Value)
		}
		lines = append(lines, "---")
		return strings.Join(lines, "\n") + "\n" + content
	}
	end := strings.Index(content[4:], "\n---\n")
	if end < 0 {
		return content
	}
	end += 4
	fmText := content[4:end]
	body := content[end+5:]
	remaining := make([]Update, len(updates))
	copy(remaining, updates)
	var out []string
	for _, line := range strings.Split(fmText, "\n") {
		if strings.Contains(line, ":") {
			key := strings.TrimSpace(strings.SplitN(line, ":", 2)[0])
			hit := -1
			for i, u := range remaining {
				if u.Key == key {
					hit = i
					break
				}
			}
			if hit >= 0 {
				out = append(out, key+": "+remaining[hit].Value)
				remaining = append(remaining[:hit], remaining[hit+1:]...)
				continue
			}
		}
		out = append(out, line)
	}
	for _, u := range remaining {
		out = append(out, u.Key+": "+u.Value)
	}
	return "---\n" + strings.Join(out, "\n") + "\n---\n" + body
}

// DropFrontmatterKeys removes top-level keys from the block, line by line,
// leaving everything else byte for byte. Used to clear a stale review flag.
func DropFrontmatterKeys(content string, keys ...string) string {
	if !strings.HasPrefix(content, "---\n") {
		return content
	}
	end := strings.Index(content[4:], "\n---\n")
	if end < 0 {
		return content
	}
	end += 4
	drop := map[string]bool{}
	for _, k := range keys {
		drop[k] = true
	}
	var out []string
	for _, line := range strings.Split(content[4:end], "\n") {
		if line != "" && !strings.ContainsRune(" \t#-", rune(line[0])) {
			if key, _, ok := strings.Cut(line, ":"); ok && drop[strings.TrimSpace(key)] {
				continue
			}
		}
		out = append(out, line)
	}
	return "---\n" + strings.Join(out, "\n") + "\n---\n" + content[end+5:]
}

var whitespaceRun = regexp.MustCompile(`[ \t\f\v]+`)

// NormalizeBody is `fingerprint.normalize_body`: CRLF and CR to LF, each
// line stripped and its whitespace runs collapsed, blank lines dropped, the
// whole thing case-folded (Go's ToLower stands in for casefold — identical
// on the ASCII and Latin text the corpus carries; a fixture pins it).
func NormalizeBody(body string) string {
	body = strings.ReplaceAll(body, "\r\n", "\n")
	body = strings.ReplaceAll(body, "\r", "\n")
	var lines []string
	for _, line := range strings.Split(body, "\n") {
		collapsed := whitespaceRun.ReplaceAllString(strings.TrimSpace(line), " ")
		if collapsed != "" {
			lines = append(lines, collapsed)
		}
	}
	return strings.ToLower(strings.Join(lines, "\n"))
}

// Fingerprint is `fingerprint.compute_fingerprint`: sha256 over the
// normalized body, hex.
func Fingerprint(body string) string {
	sum := sha256.Sum256([]byte(NormalizeBody(body)))
	return hex.EncodeToString(sum[:])
}

// LiveFingerprint is `dedup_guard.live_content_fingerprint`: the fingerprint
// of the note's current body with the frontmatter block stripped the way
// `_frontmatter_span` strips it (`---\n` … `\n---\n`).
func LiveFingerprint(content string) string {
	body := content
	if strings.HasPrefix(content, "---\n") {
		if end := strings.Index(content[4:], "\n---\n"); end >= 0 {
			body = content[end+4+5:]
		}
	}
	return Fingerprint(body)
}
