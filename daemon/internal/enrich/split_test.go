package enrich

import (
	"context"
	"strings"
	"testing"
)

func frag(slug, title, body string) SplitFragment {
	return SplitFragment{
		Response: Response{
			Title: title, Type: "fact", Altitude: "artifact",
			Body: body, Confidence: 0.9, Slug: slug,
		},
		Slug: slug,
	}
}

func plan(frags ...SplitFragment) SplitPlan {
	return SplitPlan{Fragments: frags, Reason: "three unrelated things in one paste"}
}

// The property the whole design turns on: nothing is deleted. The original stays
// where it was, superseded, with its text intact — because a split is a judgment
// and judgments are wrong sometimes.
func TestASplitSupersedesRatherThanDeletes(t *testing.T) {
	v := newVault()
	v.files["Agent/memory/semantic/blob.md"] = "---\ntitle: A blob\n---\n\nthe original text\n"
	a := applier(t, v, &recorder{}, func(string) (bool, error) { return false, nil })

	written, err := a.ApplySplit(context.Background(),
		"Agent/memory/semantic/blob.md",
		v.files["Agent/memory/semantic/blob.md"],
		plan(frag("first", "First", "one"), frag("second", "Second", "two")),
		TriggerBatch)
	if err != nil {
		t.Fatalf("ApplySplit: %v", err)
	}
	if len(written) != 2 {
		t.Fatalf("wrote %d fragments, want 2: %v", len(written), written)
	}

	orig, ok := v.files["Agent/memory/semantic/blob.md"]
	if !ok {
		t.Fatal("the original was deleted; a split is additive")
	}
	if !strings.Contains(orig, "status: superseded") {
		t.Errorf("the original was not marked superseded:\n%s", orig)
	}
	// Its text survives, or it cannot answer "was the split right" — the only
	// question anyone asks of a superseded note.
	if !strings.Contains(orig, "the original text") {
		t.Errorf("the original's body was thrown away:\n%s", orig)
	}
	// And it says what replaced it.
	for _, f := range written {
		if !strings.Contains(orig, f) {
			t.Errorf("the original does not point at %s:\n%s", f, orig)
		}
	}
}

// Every fragment records where it came from, so the relationship is walkable.
func TestEveryFragmentCarriesDerivedFrom(t *testing.T) {
	v := newVault()
	parent := "Agent/memory/semantic/blob.md"
	v.files[parent] = "---\ntitle: A blob\n---\n\ntext\n"
	a := applier(t, v, &recorder{}, func(string) (bool, error) { return false, nil })

	written, err := a.ApplySplit(context.Background(), parent, v.files[parent],
		plan(frag("a", "A", "one"), frag("b", "B", "two"), frag("c", "C", "three")),
		TriggerBatch)
	if err != nil {
		t.Fatal(err)
	}
	for _, rel := range written {
		body := v.files[rel]
		if !strings.Contains(body, "derived_from: "+parent) {
			t.Errorf("%s does not record its parent:\n%s", rel, body)
		}
		// And it stays one frontmatter block — a second `---` would make the
		// note parse as prose containing YAML.
		if strings.Count(body, "\n---\n") != 1 {
			t.Errorf("%s has a malformed frontmatter block:\n%s", rel, body)
		}
	}
}

// Fragments do not cross-link. N² edges saying "these came from the same paste"
// is the same information the parent edge already carries, once.
func TestFragmentsDoNotCrossLink(t *testing.T) {
	v := newVault()
	parent := "Agent/memory/semantic/blob.md"
	v.files[parent] = "---\ntitle: A blob\n---\n\ntext\n"
	a := applier(t, v, &recorder{}, func(string) (bool, error) { return false, nil })

	written, err := a.ApplySplit(context.Background(), parent, v.files[parent],
		plan(frag("a", "A", "one"), frag("b", "B", "two")), TriggerBatch)
	if err != nil {
		t.Fatal(err)
	}
	for _, rel := range written {
		for _, other := range written {
			if rel == other {
				continue
			}
			if strings.Contains(v.files[rel], other) {
				t.Errorf("%s links to its sibling %s", rel, other)
			}
		}
	}
}

// Fragments are written before the original is superseded. A crash the other way
// round leaves a note claiming to be replaced by files that do not exist.
func TestFragmentsLandBeforeTheOriginalIsSuperseded(t *testing.T) {
	v := newVault()
	parent := "Agent/memory/semantic/blob.md"
	v.files[parent] = "---\ntitle: A blob\n---\n\ntext\n"
	a := applier(t, v, &recorder{}, func(string) (bool, error) { return false, nil })

	if _, err := a.ApplySplit(context.Background(), parent, v.files[parent],
		plan(frag("a", "A", "one"), frag("b", "B", "two")), TriggerBatch); err != nil {
		t.Fatal(err)
	}
	last := v.ops[len(v.ops)-1]
	if last != "put:"+parent {
		t.Errorf("the original was superseded before its fragments landed: %v", v.ops)
	}
}

// Every fragment write is journalled like any other write.
func TestASplitIsFullyJournalled(t *testing.T) {
	v := newVault()
	j := &recorder{}
	parent := "Agent/memory/semantic/blob.md"
	v.files[parent] = "---\ntitle: A blob\n---\n\nthe original text\n"
	a := applier(t, v, j, func(string) (bool, error) { return false, nil })

	if _, err := a.ApplySplit(context.Background(), parent, v.files[parent],
		plan(frag("a", "A", "one"), frag("b", "B", "two")), TriggerBatch); err != nil {
		t.Fatal(err)
	}
	entries := j.all()
	if len(entries) != 3 {
		t.Fatalf("%d journal entries for two fragments plus a supersede", len(entries))
	}
	// The supersede entry carries the original text, which is what an undo needs.
	last := entries[len(entries)-1]
	if !strings.Contains(last.Previous, "the original text") {
		t.Errorf("the supersede was journalled without what it replaced: %+v", last)
	}
}

// --- validation -------------------------------------------------------------

func TestASplitMustActuallySplit(t *testing.T) {
	err := ValidateSplit(plan(frag("a", "A", "one")), nil)
	if err == nil {
		t.Fatal("a one-fragment split was accepted")
	}
	if !strings.Contains(err.Error(), "rewrite") {
		t.Errorf("the rejection does not say what a single fragment is: %v", err)
	}
	if err := ValidateSplit(SplitPlan{}, nil); err == nil {
		t.Error("an empty split was accepted")
	}
}

// A note anyone wants to cut a dozen ways has been read as a list, and the
// failure mode is a corpus of one-sentence fragments that answer nothing.
func TestASplitIsCapped(t *testing.T) {
	var frags []SplitFragment
	for i := 0; i < MaxFragments+1; i++ {
		frags = append(frags, frag(string(rune('a'+i)), "T", "b"))
	}
	err := ValidateSplit(SplitPlan{Fragments: frags}, nil)
	if err == nil {
		t.Fatalf("a %d-fragment split was accepted", len(frags))
	}
	if !strings.Contains(err.Error(), "read as a list") {
		t.Errorf("the rejection does not say why the cap exists: %v", err)
	}
}

func TestFragmentSlugsMustBeDistinctAndWellFormed(t *testing.T) {
	if err := ValidateSplit(plan(frag("same", "A", "1"), frag("same", "B", "2")),
		nil); err == nil {
		t.Error("two fragments sharing a slug were accepted; one would overwrite " +
			"the other")
	}
	if err := ValidateSplit(plan(frag("", "A", "1"), frag("b", "B", "2")),
		nil); err == nil {
		t.Error("a fragment with no slug was accepted")
	}
	if err := ValidateSplit(plan(frag("Not A Slug", "A", "1"), frag("b", "B", "2")),
		nil); err == nil {
		t.Error("a malformed slug was accepted")
	}
}

// Each fragment is a real memory and gets the same schema check a rewrite does.
func TestEachFragmentIsSchemaChecked(t *testing.T) {
	bad := frag("a", "A", "one")
	bad.Type = "invented"
	err := ValidateSplit(plan(bad, frag("b", "B", "two")), testSchema())
	if err == nil {
		t.Fatal("a fragment with an unknown type was accepted")
	}
	if !strings.Contains(err.Error(), "fragment 0") {
		t.Errorf("the rejection does not say which fragment: %v", err)
	}
}

// A fragment cannot claim the path it came from — the original has to survive.
func TestAFragmentCannotCollideWithItsParent(t *testing.T) {
	v := newVault()
	parent := "Agent/memory/semantic/blob.md"
	v.files[parent] = "---\ntitle: A blob\n---\n\ntext\n"
	a := applier(t, v, &recorder{}, func(string) (bool, error) { return false, nil })

	_, err := a.ApplySplit(context.Background(), parent, v.files[parent],
		plan(frag("blob", "A", "one"), frag("b", "B", "two")), TriggerBatch)
	if err == nil {
		t.Fatal("a fragment overwrote the note it came from")
	}
	if !strings.Contains(err.Error(), "collides") {
		t.Errorf("the rejection does not say what happened: %v", err)
	}
}

// A split into a derived class is refused like any other write into one.
func TestASplitCannotWriteIntoADerivedClass(t *testing.T) {
	v := newVault()
	parent := "Agent/memory/mocs/blob.md"
	v.files[parent] = "---\ntitle: A blob\n---\n\ntext\n"
	a := applier(t, v, &recorder{}, func(string) (bool, error) { return false, nil })

	if _, err := a.ApplySplit(context.Background(), parent, v.files[parent],
		plan(frag("a", "A", "one"), frag("b", "B", "two")), TriggerBatch); err == nil {
		t.Error("a split wrote into a derived class")
	}
}
