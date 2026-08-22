package sources

import (
	"context"
	"fmt"
	"strings"
	"time"
)

// Re-ingesting a source, without duplicating what it already produced.
//
// A source is re-mined when the pass improves — a better model, a changed filing
// contract — and the second reading of the same email is not new material. If it
// simply produced fresh memories, the corpus would end up carrying two
// distillations of one source, both plausible, with nothing saying which is
// current. The registry is what makes the better answer replace the earlier one
// instead of sitting beside it.
//
// # Why supersede rather than delete
//
// Nothing is deleted before the five-year archive horizon, and a superseded
// memory is still the record of what the system believed and why. It carries a
// pointer forward, keeps its body, and drops out of ranking through the same
// penalty every other superseded note gets. A reader who finds it learns what
// happened; a reader who finds nothing learns that something went missing.

// Finder answers "which memories did this source produce?".
//
// Supplied rather than imported so this package does not depend on the index —
// the same seam the coverage ledger uses for its lookup, and for the same
// reason: a caller with the notes already in hand should not have to construct
// an index to supersede them.
type Finder func(ctx context.Context, source string) ([]string, error)

// Rewriter reads one note and writes it back. It is handed the current body and
// returns the replacement; returning the body unchanged is a no-op the caller
// may skip.
type Rewriter func(ctx context.Context, rel string, rewrite func(string) string) error

// SupersedeReport is what one re-ingest superseded.
type SupersedeReport struct {
	Source ID `json:"source"`
	// Superseded names the memories the earlier pass produced. Named rather than
	// counted, because "seven memories were replaced" is not something anybody
	// can check and seven paths are.
	Superseded []string `json:"superseded"`
	// Version is the version the new distillation was produced under.
	Version string `json:"version"`
}

// Supersede marks every memory a source previously produced as replaced.
//
// Scoped by source id and nothing else. The obvious looser scopes — everything
// captured that day, everything that looks similar — would each reach memories
// this re-ingest never read, and a memory superseded by a pass that did not
// consider it is one the corpus has silently lost.
//
// It supersedes before the new memories are written, not after. A crash between
// the two leaves the old memories marked as replaced by a distillation that does
// not exist, which is visible and repairable; the other order leaves two live
// distillations of one source with nothing to distinguish them, which is not.
func Supersede(ctx context.Context, id ID, version string, at time.Time,
	find Finder, rewrite Rewriter) (SupersedeReport, error) {
	rep := SupersedeReport{Source: id, Version: version}
	if find == nil {
		return rep, fmt.Errorf("sources: superseding %s needs a way to find what "+
			"it produced; without one a re-ingest silently duplicates", id)
	}

	found, err := find(ctx, id.String())
	if err != nil {
		return rep, fmt.Errorf("sources: finding %s's memories: %w", id, err)
	}
	if len(found) == 0 {
		// Nothing to supersede is an ordinary outcome — a source being mined for
		// the first time, or one whose earlier pass yielded nothing.
		return rep, nil
	}
	if rewrite == nil {
		return rep, fmt.Errorf("sources: %s produced %d memories and there is no "+
			"writer to supersede them; proceeding would duplicate every one",
			id, len(found))
	}

	stamp := at.UTC().Format(stampFormat)
	for _, rel := range found {
		if err := rewrite(ctx, rel, func(body string) string {
			return markSuperseded(body, id, version, stamp)
		}); err != nil {
			return rep, fmt.Errorf("sources: superseding %s: %w", rel, err)
		}
		rep.Superseded = append(rep.Superseded, rel)
	}
	return rep, nil
}

// markSuperseded rewrites a note's frontmatter to record that a re-ingest
// replaced it.
//
// The body is left exactly as it was. What the system believed is the point of
// keeping a superseded memory at all, and a supersession that edited the text
// would leave nothing to compare the new distillation against.
func markSuperseded(body string, id ID, version, at string) string {
	fields := [][2]string{
		{"status", "superseded"},
		{"superseded_by", id.String() + " at " + version},
		{"superseded_at", at},
	}

	if !strings.HasPrefix(body, "---") {
		// No frontmatter at all: give it one rather than skipping the note. A
		// memory with no frontmatter is still a memory this source produced, and
		// leaving it live would be the duplication this exists to prevent.
		var b strings.Builder
		b.WriteString("---\n")
		for _, f := range fields {
			fmt.Fprintf(&b, "%s: %s\n", f[0], quoteIfNeeded(f[1]))
		}
		b.WriteString("---\n\n")
		b.WriteString(body)
		return b.String()
	}

	rest := body[3:]
	end := strings.Index(rest, "\n---")
	if end < 0 {
		return body
	}
	head, tail := rest[:end], rest[end:]

	var kept []string
	for _, line := range strings.Split(head, "\n") {
		key, _, ok := strings.Cut(line, ":")
		if ok && isSupersessionField(strings.TrimSpace(key)) {
			// Dropped and rewritten below, so a second supersession replaces the
			// first rather than stacking a duplicate key that no YAML reader
			// agrees about.
			continue
		}
		kept = append(kept, line)
	}
	var b strings.Builder
	b.WriteString("---")
	b.WriteString(strings.Join(kept, "\n"))
	if len(kept) > 0 && kept[len(kept)-1] != "" {
		b.WriteString("\n")
	}
	for _, f := range fields {
		fmt.Fprintf(&b, "%s: %s\n", f[0], quoteIfNeeded(f[1]))
	}
	b.WriteString(strings.TrimPrefix(tail, "\n"))
	return b.String()
}

func isSupersessionField(key string) bool {
	switch strings.ToLower(key) {
	case "status", "superseded_by", "superseded_at":
		return true
	}
	return false
}

// quoteIfNeeded quotes a value that would otherwise stop being a string. A
// source id contains a colon by construction, which is a mapping in YAML.
func quoteIfNeeded(v string) string {
	if strings.ContainsAny(v, ":#[]{}&*!|>%@`\"'\n") {
		return `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`, "\n", " ").Replace(v) + `"`
	}
	return v
}
