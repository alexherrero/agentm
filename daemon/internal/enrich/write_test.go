package enrich

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"testing"
)

// recorder is a Journal that keeps what it was told, and can be made to fail.
type recorder struct {
	mu      sync.Mutex
	entries []JournalEntry
	err     error
}

func (r *recorder) Record(_ context.Context, e JournalEntry) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.err != nil {
		return r.err
	}
	r.entries = append(r.entries, e)
	return nil
}

func (r *recorder) all() []JournalEntry {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]JournalEntry(nil), r.entries...)
}

// vault is a fake filesystem recording puts and moves in order.
type vault struct {
	mu     sync.Mutex
	files  map[string]string
	ops    []string
	putErr error
}

func newVault() *vault { return &vault{files: map[string]string{}} }

func (v *vault) put(_ context.Context, rel, body string) error {
	v.mu.Lock()
	defer v.mu.Unlock()
	if v.putErr != nil {
		return v.putErr
	}
	v.files[rel] = body
	v.ops = append(v.ops, "put:"+rel)
	return nil
}

func (v *vault) move(_ context.Context, from, to string) error {
	v.mu.Lock()
	defer v.mu.Unlock()
	v.files[to] = v.files[from]
	delete(v.files, from)
	v.ops = append(v.ops, "move:"+from+"->"+to)
	return nil
}

func applier(t *testing.T, v *vault, j Journal, linked Linked) *Applier {
	t.Helper()
	return &Applier{
		Membership: DefaultMembership(),
		Slug:       &SlugRule{Linked: linked},
		Journal:    j,
		Put:        v.put,
		Move:       v.move,
	}
}

// --- class membership -------------------------------------------------------

// Writing into a derived class feeds one pass's output into its own input, which
// is how a corpus starts agreeing with itself.
func TestEnrichmentCannotWriteIntoADerivedClass(t *testing.T) {
	v := newVault()
	a := applier(t, v, &recorder{}, func(string) (bool, error) { return false, nil })

	for _, rel := range []string{
		"Agent/memory/entities/person-alex.md",
		"Agent/memory/crystallized/lesson.md",
		"Agent/memory/mocs/index.md",
	} {
		if _, err := a.Apply(context.Background(), WriteRequest{
			Rel: rel, Next: "new body",
		}); err == nil {
			t.Errorf("enrichment wrote into %s", rel)
		}
	}
	if len(v.ops) != 0 {
		t.Errorf("a refused write still touched the vault: %v", v.ops)
	}
}

// A rename must not smuggle a note into a derived class either. The destination
// is checked as well as the source, because checking only the source would let
// a slug of "entities/x" through.
func TestARenameCannotLandInADerivedClass(t *testing.T) {
	v := newVault()
	a := applier(t, v, &recorder{}, func(string) (bool, error) { return false, nil })

	_, err := a.Apply(context.Background(), WriteRequest{
		Rel: "Agent/memory/semantic/note.md", Next: "body", NewSlug: "x",
	})
	if err != nil {
		t.Fatalf("an ordinary rename was refused: %v", err)
	}
	// Now one whose destination directory is derived, reached by path rather
	// than by slug. This one is caught by the *source* check.
	if _, err := a.Apply(context.Background(), WriteRequest{
		Rel: "Agent/memory/mocs/note.md", Next: "body", NewSlug: "y",
	}); err == nil {
		t.Error("a write into a derived class was allowed via rename")
	}

	// And the case the destination check exists for. `Apply` is a separate entry
	// point from the gate chain, so the schema gate's slug-shape rule does not
	// run here and a caller can hand it a traversal. The source is allowed, so
	// only the destination check can stop this one — which is how the negative
	// pass found that the check was untested.
	if _, err := a.Apply(context.Background(), WriteRequest{
		Rel: "Agent/memory/semantic/note.md", Next: "body", NewSlug: "../mocs/x",
	}); err == nil {
		t.Error("a traversal slug walked the note into a derived class")
	}
}

// --- the slug rule ----------------------------------------------------------

func TestASlugIsRenamedOnlyWhileNothingLinksToIt(t *testing.T) {
	v := newVault()
	a := applier(t, v, &recorder{}, func(string) (bool, error) { return false, nil })

	dest, err := a.Apply(context.Background(), WriteRequest{
		Rel: "Agent/memory/semantic/badly-named.md", Next: "body",
		NewSlug: "well-named",
	})
	if err != nil {
		t.Fatalf("Apply: %v", err)
	}
	if dest != "Agent/memory/semantic/well-named.md" {
		t.Errorf("the rename did not happen: %s", dest)
	}
	if _, ok := v.files["Agent/memory/semantic/badly-named.md"]; ok {
		t.Error("the old path survived the rename")
	}
}

// The window closes with the first inbound link. There is no redirect
// mechanism, and deliberately not: a vault of redirects is a vault where no path
// means what it says.
func TestARenameIsRefusedOnceSomethingLinksToIt(t *testing.T) {
	v := newVault()
	a := applier(t, v, &recorder{}, func(string) (bool, error) { return true, nil })

	dest, err := a.Apply(context.Background(), WriteRequest{
		Rel: "Agent/memory/semantic/badly-named.md", Next: "body",
		NewSlug: "well-named",
	})
	if err != nil {
		t.Fatalf("the enrichment was refused over a slug: %v", err)
	}
	if dest != "Agent/memory/semantic/badly-named.md" {
		t.Errorf("a linked note was renamed to %s, breaking every reference", dest)
	}
	// And the enrichment still landed. A refused rename is not a refused
	// enrichment.
	if v.files[dest] != "body" {
		t.Error("the body was not written when the rename was refused")
	}
}

// No link graph means no rename. The cost of not renaming is a slightly wrong
// filename; the cost of renaming wrongly is a broken graph.
func TestWithoutALinkGraphNothingIsRenamed(t *testing.T) {
	v := newVault()
	a := &Applier{
		Membership: DefaultMembership(),
		Slug:       &SlugRule{}, // no Linked
		Journal:    &recorder{},
		Put:        v.put, Move: v.move,
	}
	dest, err := a.Apply(context.Background(), WriteRequest{
		Rel: "Agent/memory/semantic/a.md", Next: "body", NewSlug: "b",
	})
	if err != nil {
		t.Fatalf("Apply: %v", err)
	}
	if dest != "Agent/memory/semantic/a.md" {
		t.Errorf("a rename happened with no way to prove it safe: %s", dest)
	}
}

func TestARenameToTheSameSlugIsNotARename(t *testing.T) {
	v := newVault()
	a := applier(t, v, &recorder{}, func(string) (bool, error) { return true, nil })
	dest, err := a.Apply(context.Background(), WriteRequest{
		Rel: "Agent/memory/semantic/same.md", Next: "body", NewSlug: "same",
	})
	if err != nil {
		t.Fatalf("Apply: %v", err)
	}
	if dest != "Agent/memory/semantic/same.md" {
		t.Errorf("dest = %s", dest)
	}
	for _, op := range v.ops {
		if strings.HasPrefix(op, "move:") {
			t.Errorf("a no-op rename issued a move: %v", v.ops)
		}
	}
}

// --- the journal ------------------------------------------------------------

// Every write is recorded, with the previous bytes rather than a diff — undoing
// from a diff requires the diff to still apply, which requires nothing else to
// have touched the note, which is exactly when somebody wants to undo.
func TestEveryWriteIsJournalledWithWhatItReplaced(t *testing.T) {
	v := newVault()
	j := &recorder{}
	a := applier(t, v, j, func(string) (bool, error) { return false, nil })

	if _, err := a.Apply(context.Background(), WriteRequest{
		Rel: "Agent/memory/semantic/n.md", Previous: "the old body",
		Next: "the new body", Trigger: TriggerBatch, Version: "v1",
	}); err != nil {
		t.Fatal(err)
	}
	got := j.all()
	if len(got) != 1 {
		t.Fatalf("%d journal entries for one write", len(got))
	}
	e := got[0]
	if e.Previous != "the old body" {
		t.Errorf("the entry does not carry what was replaced: %q", e.Previous)
	}
	if e.Next != "the new body" || e.Rel != "Agent/memory/semantic/n.md" {
		t.Errorf("entry = %+v", e)
	}
	if e.Trigger != "batch" || e.Version != "v1" {
		t.Errorf("the entry does not say which pass wrote it: %+v", e)
	}
	if e.At.IsZero() {
		t.Error("the entry has no timestamp")
	}
}

// A rename is recorded as one, or the undo puts the body back at a path that no
// longer exists.
func TestARenameIsRecordedWithBothPaths(t *testing.T) {
	v := newVault()
	j := &recorder{}
	a := applier(t, v, j, func(string) (bool, error) { return false, nil })

	if _, err := a.Apply(context.Background(), WriteRequest{
		Rel: "Agent/memory/semantic/old.md", Next: "body", NewSlug: "new",
	}); err != nil {
		t.Fatal(err)
	}
	e := j.all()[0]
	if e.Renamed != "Agent/memory/semantic/old.md" {
		t.Errorf("the entry does not record where the note came from: %+v", e)
	}
	if e.Rel != "Agent/memory/semantic/new.md" {
		t.Errorf("the entry does not record where it went: %+v", e)
	}
}

// The ordering that matters. A crash between the write and the journal leaves a
// change nobody can undo; a crash between the journal and the write leaves an
// entry for a write that did not happen, which is noise.
func TestAnUnrecordableWriteIsNotPerformed(t *testing.T) {
	v := newVault()
	j := &recorder{err: errors.New("journal is read-only")}
	a := applier(t, v, j, func(string) (bool, error) { return false, nil })

	_, err := a.Apply(context.Background(), WriteRequest{
		Rel: "Agent/memory/semantic/n.md", Previous: "old", Next: "new",
	})
	if err == nil {
		t.Fatal("a write proceeded with no way to record it")
	}
	if !strings.Contains(err.Error(), "unrecorded") {
		t.Errorf("the error does not say why: %v", err)
	}
	if len(v.ops) != 0 {
		t.Errorf("an unrecordable write still touched the vault: %v", v.ops)
	}
}

// The journal entry is written before the bytes are, so the order is checkable
// rather than asserted in a comment.
func TestTheJournalIsWrittenBeforeTheBytes(t *testing.T) {
	v := newVault()
	var order []string
	var mu sync.Mutex
	j := journalFunc(func(JournalEntry) error {
		mu.Lock()
		order = append(order, "journal")
		mu.Unlock()
		return nil
	})
	a := &Applier{
		Membership: DefaultMembership(),
		Slug:       &SlugRule{Linked: func(string) (bool, error) { return false, nil }},
		Journal:    j,
		Put: func(ctx context.Context, rel, body string) error {
			mu.Lock()
			order = append(order, "put")
			mu.Unlock()
			return v.put(ctx, rel, body)
		},
		Move: v.move,
	}
	if _, err := a.Apply(context.Background(), WriteRequest{
		Rel: "Agent/memory/semantic/n.md", Next: "body",
	}); err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(order) != "[journal put]" {
		t.Errorf("order = %v, want the journal first — a crash the other way round "+
			"leaves a change nobody can undo", order)
	}
}

type journalFunc func(JournalEntry) error

func (f journalFunc) Record(_ context.Context, e JournalEntry) error { return f(e) }
