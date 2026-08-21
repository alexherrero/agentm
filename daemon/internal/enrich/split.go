package enrich

import (
	"context"
	"fmt"
	"strings"
)

// Splits are additive, and "additive" is the whole safety argument.
//
// A capture is often several memories wearing one filename — a session's notes
// covering three unrelated things, a paste that turned out to be a list. Filing
// that as one note means every one of its ideas ranks against the other two's
// words, and none of them is findable by its own.
//
// So a blob becomes N notes. What it does not become is *gone*: the original
// stays where it was, flipped to `superseded`, with its raw text unchanged in
// git at the capture commit. Every fragment records where it came from, so the
// relationship is walkable in both directions.
//
// # Why not delete the original
//
// Because the split is a judgment and judgments are wrong sometimes. A split
// that cut a single argument into three halves leaves three notes that each say
// less than the whole did, and the only way back is the text nobody kept. Here
// the way back is `git show <capture-commit>:<path>`, and the note itself says
// it was superseded rather than vanishing from a search.
//
// # Why the fragments do not link to each other
//
// N fragments cross-linking is N² edges that say nothing more than "these came
// from the same paste". The `derived_from` edge to the original carries the same
// information once, and the original is the thing a reader actually wants when
// they are wondering what the fragment was torn out of.

// SplitFragment is one memory carved out of a blob.
type SplitFragment struct {
	Response
	// Slug is required for a fragment, because a fragment has no path of its
	// own until one is chosen. The parent's path cannot be reused: N notes
	// cannot share it.
	Slug string `json:"slug"`
}

// SplitPlan is what the model proposes when a note is several memories.
type SplitPlan struct {
	// Fragments are the memories the blob becomes. Fewer than two is not a
	// split, and a caller proposing one is proposing a rewrite.
	Fragments []SplitFragment `json:"fragments"`
	// Reason says why the note was several things. Recorded on the superseded
	// original, so somebody reading it later learns what happened rather than
	// finding a dead end.
	Reason string `json:"reason"`
}

// MaxFragments bounds one split.
//
// Not a technical limit. A note the model wants to cut twelve ways is a note it
// has misread as a list, and the failure mode of an unbounded split is a corpus
// of one-sentence fragments that individually answer nothing.
const MaxFragments = 6

// ValidateSplit checks a proposed split before anything is written.
func ValidateSplit(p SplitPlan, schema *Schema) error {
	if len(p.Fragments) < 2 {
		return fmt.Errorf("%w: %d fragment(s) is not a split; a single memory is a "+
			"rewrite", ErrNotEligible, len(p.Fragments))
	}
	if len(p.Fragments) > MaxFragments {
		return fmt.Errorf("%w: %d fragments, over the cap of %d — a note anyone "+
			"wants to cut that many ways has been read as a list", ErrNotEligible,
			len(p.Fragments), MaxFragments)
	}
	seen := map[string]bool{}
	for i, f := range p.Fragments {
		if strings.TrimSpace(f.Slug) == "" {
			return fmt.Errorf("%w: fragment %d has no slug, and N notes cannot "+
				"share the parent's path", ErrNotEligible, i)
		}
		if !slugRe.MatchString(f.Slug) {
			return fmt.Errorf("%w: fragment %d's slug %q is not a lower-case "+
				"hyphenated stem", ErrNotEligible, i, f.Slug)
		}
		if seen[f.Slug] {
			return fmt.Errorf("%w: two fragments claim the slug %q, so one would "+
				"overwrite the other", ErrNotEligible, f.Slug)
		}
		seen[f.Slug] = true
		if schema != nil {
			if err := schema.Validate(f.Response); err != nil {
				return fmt.Errorf("fragment %d: %w", i, err)
			}
		}
	}
	return nil
}

// SupersededNote rewrites the original to point at what replaced it.
//
// The body is kept rather than replaced with a pointer. A superseded note whose
// text was thrown away is a note that cannot answer the question "was the split
// right", which is the only question anyone asks of one.
func SupersededNote(previous string, plan SplitPlan, fragmentPaths []string) string {
	body := sourceBody(previous)
	var b strings.Builder
	b.WriteString("---\n")
	writeScalar(&b, "title", frontmatterValue(previous, "title"))
	writeScalar(&b, "status", "superseded")
	writeScalar(&b, "superseded_by", strings.Join(fragmentPaths, ", "))
	if plan.Reason != "" {
		writeScalar(&b, "superseded_reason", plan.Reason)
	}
	writeScalar(&b, "enriched_by", PassVersion)
	b.WriteString("---\n\n")
	b.WriteString(strings.TrimRight(body, "\n"))
	b.WriteString("\n")
	return b.String()
}

// RenderFragment writes one fragment, carrying the edge back to its parent.
func RenderFragment(f SplitFragment, parentRel string) string {
	out := RenderNote(f.Response)
	// Inserted into the existing frontmatter rather than appended after it: a
	// second `---` block would make the note parse as prose containing YAML.
	marker := "enriched_by:"
	i := strings.Index(out, marker)
	if i < 0 {
		return out
	}
	return out[:i] + "derived_from: " + yamlScalar(parentRel) + "\n" + out[i:]
}

// ApplySplit writes the fragments and supersedes the original.
//
// Fragments first, original last. The ordering matters for the same reason the
// journal comes before the write: a crash after superseding but before writing
// the fragments would leave a note marked as replaced by files that do not
// exist, which is worse than the reverse — an un-superseded original beside its
// fragments is merely duplicated, and the reconcile pass will index both.
func (w *Applier) ApplySplit(ctx context.Context, parentRel, previous string,
	plan SplitPlan, trigger Trigger) ([]string, error) {
	if w.Membership != nil {
		if err := w.Membership.Allows(parentRel); err != nil {
			return nil, err
		}
	}

	dir := ""
	if i := strings.LastIndexByte(parentRel, '/'); i >= 0 {
		dir = parentRel[:i+1]
	}

	var written []string
	for _, f := range plan.Fragments {
		dest := dir + f.Slug + ".md"
		if dest == parentRel {
			return written, fmt.Errorf("%w: fragment slug %q collides with the "+
				"note it came from", ErrNotEligible, f.Slug)
		}
		if w.Membership != nil {
			if err := w.Membership.Allows(dest); err != nil {
				return written, err
			}
		}
		body := RenderFragment(f, parentRel)
		if _, err := w.Apply(ctx, WriteRequest{
			Rel: dest, Previous: "", Next: body, Trigger: trigger,
			Version: PassVersion,
		}); err != nil {
			return written, fmt.Errorf("writing fragment %s: %w", dest, err)
		}
		written = append(written, dest)
	}

	if _, err := w.Apply(ctx, WriteRequest{
		Rel: parentRel, Previous: previous,
		Next:    SupersededNote(previous, plan, written),
		Trigger: trigger, Version: PassVersion,
	}); err != nil {
		return written, fmt.Errorf("superseding %s: %w", parentRel, err)
	}
	return written, nil
}
