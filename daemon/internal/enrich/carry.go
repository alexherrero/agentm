package enrich

import (
	"fmt"
	"strings"
)

// The fields an enrichment response cannot know and must not lose: how the
// note arrived and what the operator said at capture time (filing v2, the
// write path). `RenderNote` writes the judgment; this carries the provenance
// across it. `filing_confidence` is deliberately absent — the pass re-judges
// it, which is how an unfiled capture clears the needs-review reading.
var carriedFields = []string{
	"source", "lifecycle", "captured", "via", "source_url", "source_fetched",
	"surface", "instructions", "review_flags", "related",
}

// CarryProvenance copies, from the note as it stood before enrichment, every
// carried field the rendered note does not already set. A note with no
// lifecycle of its own starts `active`, the contract's default: an enriched
// note is an auto-filed note, and the design has every one of those carry the
// aging axis. Values are copied as written — a JSON-quoted instruction, a
// flow-list of flags — so the round trip changes nothing.
func CarryProvenance(previous, next string) string {
	if !strings.HasPrefix(next, "---\n") {
		return next
	}
	end := strings.Index(next[4:], "\n---")
	if end < 0 {
		return next
	}
	head, tail := next[:4+end], next[4+end:]
	var add strings.Builder
	for _, key := range carriedFields {
		if rawFrontmatterValue(next, key) != "" {
			continue
		}
		value := rawFrontmatterValue(previous, key)
		if value == "" {
			if key != "lifecycle" {
				continue
			}
			value = "active"
		}
		fmt.Fprintf(&add, "\n%s: %s", key, value)
	}
	return head + add.String() + tail
}

// rawFrontmatterValue is frontmatterValue without the quote stripping: the
// value exactly as the line carries it, so what was quoted stays quoted.
func rawFrontmatterValue(raw, key string) string {
	if !strings.HasPrefix(raw, "---") {
		return ""
	}
	rest := raw[3:]
	i := strings.Index(rest, "\n---")
	if i < 0 {
		return ""
	}
	for _, line := range strings.Split(rest[:i], "\n") {
		k, v, ok := strings.Cut(line, ":")
		if ok && strings.EqualFold(strings.TrimSpace(k), key) {
			return strings.TrimSpace(v)
		}
	}
	return ""
}
