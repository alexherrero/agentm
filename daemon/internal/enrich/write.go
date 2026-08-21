package enrich

import (
	"context"
	"fmt"
	"strings"
	"time"
)

// Where enrichment may write, what it may rename, and the journal that makes
// every write undoable.
//
// These three belong together because they are the same question asked three
// ways: what is this pass allowed to change about the corpus, and how does
// somebody undo it when the answer turns out to be wrong.

// --- class membership -------------------------------------------------------

// DerivedClasses are produced by other passes out of notes enrichment has
// already touched. Writing into them would feed one pass's output back into its
// own input, which is how a corpus starts agreeing with itself.
var DerivedClasses = map[string]bool{
	"entities": true, "crystallized": true, "mocs": true,
}

// Membership refuses a write that would land outside the observational classes.
//
// Distinct from the eligibility pre-gate, which refuses to *read* a derived
// class. This one refuses to *write* into one, and the two are different
// failures: reading a MOC would waste a call, while writing one would corrupt a
// derived artifact with a guess about its source.
type Membership struct {
	// Derived is the set of directory names enrichment may not write into.
	Derived map[string]bool
}

// DefaultMembership is the shipped rule.
func DefaultMembership() *Membership { return &Membership{Derived: DerivedClasses} }

func (g *Membership) Name() string { return "class-membership" }

// Allows reports whether enrichment may write to a path.
func (g *Membership) Allows(rel string) error {
	for _, seg := range strings.Split(strings.ReplaceAll(rel, "\\", "/"), "/") {
		if g.Derived[strings.ToLower(seg)] {
			return fmt.Errorf("%w: %s is derived from notes enrichment already "+
				"touched; writing there feeds one pass's output into its own input",
				ErrNotEligible, seg)
		}
	}
	return nil
}

func (g *Membership) Check(_ context.Context, req Request, _ string) error {
	return g.Allows(req.Rel)
}

// --- the slug rule ----------------------------------------------------------

// Linked answers whether anything points at a note yet.
//
// Supplied as a function so this package does not import the index. The answer
// has to come from the real link graph rather than from a guess: the whole rule
// turns on it, and a rename that guessed wrong breaks every reference at once.
type Linked func(rel string) (bool, error)

// SlugRule enforces the while-unlinked rename.
//
// One rename, before anything links, never after. The asymmetry is the point: a
// slug is wrong for as long as the note exists, so correcting it early is worth
// a lot and correcting it late costs every inbound reference. There is no
// redirect mechanism here and there deliberately is not one — a vault of
// redirects is a vault where no path means what it says.
type SlugRule struct {
	// Linked is consulted before any rename. A nil Linked refuses every rename,
	// which is the safe direction: the cost of not renaming is a slightly wrong
	// filename, and the cost of renaming wrongly is a broken graph.
	Linked Linked
}

func (g *SlugRule) Name() string { return "slug-rule" }

// MayRename reports whether `rel` can take a new slug.
func (g *SlugRule) MayRename(rel, newSlug string) error {
	if newSlug == "" {
		return nil
	}
	if current := slugOf(rel); current == newSlug {
		return nil
	}
	if g.Linked == nil {
		return fmt.Errorf("%w: no link graph available, so a rename cannot be "+
			"proved safe", ErrNotEligible)
	}
	linked, err := g.Linked(rel)
	if err != nil {
		return fmt.Errorf("checking inbound links for %s: %w", rel, err)
	}
	if linked {
		return fmt.Errorf("%w: %s already has inbound links; the rename window "+
			"closed when the first one was written", ErrNotEligible, rel)
	}
	return nil
}

// SlugRule is deliberately not a Gate.
//
// A refused rename is not a refused enrichment: the body, type, tags and
// everything else are still good, and only the filename stays as it was. Failing
// the whole note over a slug would throw away a good rewrite to avoid a cosmetic
// imperfection. So the rule is consulted by the write path, which can act on
// "rename or do not rename", rather than by the gate chain, which can only
// accept or reject the whole response.
//
// The first draft did make it a Gate, with a Check that returned nil on every
// path — a gate in name only, which is worse than none because a reader counting
// gates counts it.

// slugOf is the filename stem of a vault-relative path.
func slugOf(rel string) string {
	if i := strings.LastIndexByte(rel, '/'); i >= 0 {
		rel = rel[i+1:]
	}
	return strings.TrimSuffix(rel, ".md")
}

// --- the journal ------------------------------------------------------------

// JournalEntry records one write, in enough detail to undo it.
//
// It carries the previous bytes rather than a diff. A diff is smaller and is the
// wrong trade here: undoing from a diff requires the diff to still apply, which
// requires nothing else to have touched the note, which is exactly the situation
// where somebody wants to undo.
type JournalEntry struct {
	At       time.Time `json:"at"`
	Rel      string    `json:"rel"`
	Trigger  string    `json:"trigger"`
	Version  string    `json:"version"`
	Previous string    `json:"previous"`
	Next     string    `json:"next"`
	// Renamed carries the old path when the write also moved the note.
	Renamed string `json:"renamed,omitempty"`
}

// Journal records writes so they can be reverted.
type Journal interface {
	Record(ctx context.Context, e JournalEntry) error
}

// WriteRequest is one enrichment landing in the vault.
type WriteRequest struct {
	Rel      string
	Previous string
	Next     string
	NewSlug  string
	Trigger  Trigger
	Version  string
}

// Applier applies an enrichment: it checks membership, decides the rename, and
// journals before it writes.
//
// Journals *before*, not after. A crash between the write and the journal leaves
// a change nobody can undo, which is the one ordering that loses information;
// a crash between the journal and the write leaves a journal entry for a write
// that did not happen, which is noise.
type Applier struct {
	Membership *Membership
	Slug       *SlugRule
	Journal    Journal
	// Put writes the bytes. Supplied so this package does not own the vault.
	Put func(ctx context.Context, rel, body string) error
	// Move renames. Optional: without it, a rename is simply not performed and
	// the note keeps its path.
	Move func(ctx context.Context, from, to string) error
}

// Apply performs one enrichment write.
func (w *Applier) Apply(ctx context.Context, req WriteRequest) (string, error) {
	if w.Membership != nil {
		if err := w.Membership.Allows(req.Rel); err != nil {
			return "", err
		}
	}

	dest := req.Rel
	renamed := ""
	if req.NewSlug != "" && w.Slug != nil && w.Move != nil {
		if err := w.Slug.MayRename(req.Rel, req.NewSlug); err == nil {
			candidate := renameTo(req.Rel, req.NewSlug)
			if candidate != req.Rel {
				if err := w.Membership.Allows(candidate); err != nil {
					return "", err
				}
				renamed, dest = req.Rel, candidate
			}
		}
	}

	if w.Journal != nil {
		if err := w.Journal.Record(ctx, JournalEntry{
			At: time.Now().UTC(), Rel: dest, Trigger: req.Trigger.String(),
			Version: req.Version, Previous: req.Previous, Next: req.Next,
			Renamed: renamed,
		}); err != nil {
			// An unrecordable write is not performed. The alternative is a change
			// with no way back, and this pass rewrites prose the operator did not
			// read first.
			return "", fmt.Errorf("enrich: refusing to write %s unrecorded: %w",
				dest, err)
		}
	}

	if renamed != "" {
		if err := w.Move(ctx, renamed, dest); err != nil {
			return "", fmt.Errorf("enrich: renaming %s: %w", renamed, err)
		}
	}
	if err := w.Put(ctx, dest, req.Next); err != nil {
		return "", fmt.Errorf("enrich: writing %s: %w", dest, err)
	}
	return dest, nil
}

// renameTo swaps a path's stem.
func renameTo(rel, slug string) string {
	dir := ""
	if i := strings.LastIndexByte(rel, '/'); i >= 0 {
		dir = rel[:i+1]
	}
	return dir + slug + ".md"
}
