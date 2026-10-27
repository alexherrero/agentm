package enrich

import (
	"context"
	"reflect"
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/fmlist"
)

// A split's links read back as the notes they name (task 193 step 7, LD-5):
// the original's `superseded_by` as every fragment, each fragment's
// `derived_from` as the original.
func TestASplitsLinksReadBackAsTheNotes(t *testing.T) {
	v := newVault()
	parent := "agent/memory/semantic/blob.md"
	v.files[parent] = "---\ntitle: A blob\n---\n\ntext\n"
	a := applier(t, v, &recorder{}, func(string) (bool, error) { return false, nil })
	written, err := a.ApplySplit(context.Background(), parent, v.files[parent],
		plan(frag("a", "A", "one"), frag("b", "B", "two")), TriggerBatch, Stamp{})
	if err != nil {
		t.Fatal(err)
	}
	var got []string
	for _, l := range fmlist.LinksOf(v.files[parent], "superseded_by") {
		got = append(got, l.MemoryRel(""))
	}
	if !reflect.DeepEqual(got, written) {
		t.Errorf("superseded_by reads back as %v, want %v", got, written)
	}
	for _, rel := range written {
		l, ok := fmlist.First(fmlist.LinksOf(v.files[rel], "derived_from"))
		if !ok || l.MemoryRel("") != parent {
			t.Errorf("%s derived_from reads back as %+v, want %s", rel, l, parent)
		}
	}
}
