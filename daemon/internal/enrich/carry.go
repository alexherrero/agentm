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
// The list was audited against the journal after the first full-corpus run
// (2026-09-11), by asking which frontmatter keys a rewrite actually lost: of
// 133 cards, it dropped `slug` 104 times, `group` and `always_load` 40 each,
// the `mining_*` trio 19, `derived_from` 18, `fingerprint` 16, `occurrences`
// 7, `source_id` 4, `excerpt_edges_unverified` 3, and `superseded_by`,
// `promoted_at` and `promoted_to` twice each. Every one of them is a fact from
// before the pass, which is exactly what this list is for, so every one is
// carried now.
//
// `lifecycle` in particular does not travel alone. The fields beside it say
// when the aging axis last moved and, for a superseded note, which note
// replaced it — and the vault's own contract requires the pair:
// `lifecycle: superseded` without `superseded_by:` is a memory that has lost
// its lineage, which `check-vault-frontmatter` fails on.
//
// Two dropped keys stay dropped, deliberately. `altitude` is retired by the
// design (the deep pass drops it), and `aliases` is the pass's own answer
// under the alias-vocabulary gate — carrying it would make an alias permanent
// the first time any pass proposed one.
var carriedFields = []string{
	"source", "source_id", "source_url", "source_fetched",
	"lifecycle", "lifecycle_since", "superseded_by", "supersedes",
	"promoted_at", "promoted_to", "derived_from",
	"captured", "created", "via", "surface", "instructions", "review_flags",
	"excerpt_edges_unverified", "related", "trust", "why", "project", "task",
	"importance", "importance_proposed",
	"slug", "group", "always_load", "fingerprint", "occurrences",
	"mining_rationale", "mining_confidence", "mining_occurrences",
}

// EvidenceHeading opens the block quoting the excerpt a note came from. It is
// the operator's or the room's material, not the pass's, so a rewrite carries
// it rather than regenerating it.
const EvidenceHeading = "## Evidence"

// CarryProvenance copies, from the note as it stood before enrichment, every
// carried field the rendered note does not already set, restores an
// `importance` the operator set, and puts back the Evidence block. A note with
// no lifecycle of its own starts `active`, the contract's default: an enriched
// note is an auto-filed note, and the design has every one of those carry the
// aging axis. Values are copied as written — a JSON-quoted instruction, a
// flow-list of flags — so the round trip changes nothing.
//
// `why` rides in `carriedFields` and is therefore never lost, and never
// written either: a `why` is what was happening when the note was kept, and no
// pass was there. A reason guessed from a note reads exactly like a real one,
// which is why the field is only worth having if it is always the room's.
func CarryProvenance(previous, next string) string {
	if !strings.HasPrefix(next, "---\n") {
		return next
	}
	next = carryImportance(previous, next)
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
	return carryEvidence(previous, head+add.String()+tail)
}

// carryImportance keeps an `importance` the operator set.
//
// The rule is a comparison, not a flag: capture and enrichment both write
// `importance` and `importance_proposed` equal, so a note where they *differ*
// is a note somebody edited, and that value is theirs from then on. A pass may
// still propose — the proposal lands in `importance_proposed`, which is the
// machine's half — but the number the operator reads does not move.
//
// When the two agree, the previous value is the last proposal and a new one
// replaces it. When the pass proposes nothing, `carriedFields` carries both
// across unchanged.
func carryImportance(previous, next string) string {
	was := rawFrontmatterValue(previous, "importance")
	proposed := rawFrontmatterValue(previous, "importance_proposed")
	if was == "" || was == proposed {
		return next
	}
	if rawFrontmatterValue(next, "importance") == "" {
		// Nothing proposed; the ordinary carry below picks it up.
		return next
	}
	return replaceFrontmatterValue(next, "importance", was)
}

// carryEvidence puts back the `## Evidence` block.
//
// The block quotes the excerpt the note was distilled from — source material,
// not prose the pass is entitled to rewrite. A rewrite that dropped it would
// take the note's own evidence for itself with it, and one that paraphrased it
// would leave a quotation that is no longer a quotation.
func carryEvidence(previous, next string) string {
	block := evidenceBlock(previous)
	if block == "" || evidenceBlock(next) != "" {
		return next
	}
	return strings.TrimRight(next, "\n") + "\n\n" + block
}

// evidenceBlock returns the note's Evidence section verbatim, heading
// included, or "" when it has none. The section runs to the next `## ` heading
// or to the end of the note.
func evidenceBlock(raw string) string {
	rest := ""
	if strings.HasPrefix(raw, EvidenceHeading) {
		rest = raw
	} else if i := strings.Index(raw, "\n"+EvidenceHeading); i >= 0 {
		rest = raw[i+1:]
	} else {
		return ""
	}
	if j := strings.Index(rest[len(EvidenceHeading):], "\n## "); j >= 0 {
		rest = rest[:len(EvidenceHeading)+j]
	}
	return strings.TrimRight(rest, "\n") + "\n"
}

// replaceFrontmatterValue rewrites one frontmatter line in place, leaving the
// rest of the note byte-identical.
func replaceFrontmatterValue(raw, key, value string) string {
	if !strings.HasPrefix(raw, "---") {
		return raw
	}
	end := strings.Index(raw[3:], "\n---")
	if end < 0 {
		return raw
	}
	head, tail := raw[:3+end], raw[3+end:]
	lines := strings.Split(head, "\n")
	for i, line := range lines {
		k, _, ok := strings.Cut(line, ":")
		if ok && strings.EqualFold(strings.TrimSpace(k), key) {
			lines[i] = key + ": " + value
			return strings.Join(lines, "\n") + tail
		}
	}
	return raw
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
