package enrich

import "strings"

// An unchanged judgment writes nothing (task 181 step 5; task 180's step 5,
// revived on the operator's ruling of 2026-10-01).
//
// A re-judgment that reaches the same answer used to rewrite the note anyway,
// because the render stamps the time, the date and the contract on every pass.
// Drive for Desktop mirrors the vault, so each such rewrite reached every
// synced device as a changed file: 159 of the 413 rewrites in the nine nights
// to 2026-09-30 changed nothing but those stamps (task 180's measurement).
//
// Three stamps are masked, and one is not:
//
//   - `updated` is the date the note's content last changed; a re-judgment
//     that changes nothing has not changed it.
//   - `enriched_at` and `rules_hash` name the judgment that last changed the
//     note. The ledger, which survives an index schema change since this task,
//     holds the latest judgment, so neither has to be rewritten to keep a note
//     from being judged again.
//   - `enriched_by` is not masked. PassDepth reads it to decide whether a note
//     is owed the deep pass, so a pass-version bump has to write it, or every
//     note would stay owed the deep pass and be bought it again every night.
//     A prompt change rewrites nearly every note anyway, so the mask would
//     save almost nothing there.

// UnrewrittenStamps are the stamp keys a re-judgment may leave as they are.
var UnrewrittenStamps = []string{"updated", "enriched_at", "rules_hash"}

// SameButStamps reports whether two renderings of a note differ only in the
// stamps a re-judgment may leave as they are: every other byte, the body and
// every other frontmatter line, the same.
func SameButStamps(onDisk, rendered string) bool {
	return maskStamps(onDisk) == maskStamps(rendered)
}

// maskStamps drops the top-level frontmatter lines that carry an unrewritten
// stamp. Only a line that opens with the key — a top-level key — is dropped,
// so a nested field or a body line that happens to start the same is kept.
func maskStamps(s string) string {
	lines := strings.SplitAfter(s, "\n")
	if len(lines) == 0 || strings.TrimRight(lines[0], "\r\n") != "---" {
		return s
	}
	var b strings.Builder
	b.WriteString(lines[0])
	i := 1
	for ; i < len(lines); i++ {
		line := lines[i]
		if strings.TrimRight(line, "\r\n") == "---" {
			break
		}
		if isUnrewrittenStamp(line) {
			continue
		}
		b.WriteString(line)
	}
	for ; i < len(lines); i++ {
		b.WriteString(lines[i])
	}
	return b.String()
}

func isUnrewrittenStamp(line string) bool {
	for _, k := range UnrewrittenStamps {
		if strings.HasPrefix(line, k+":") {
			return true
		}
	}
	return false
}
