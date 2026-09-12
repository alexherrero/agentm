package cardshape

import (
	"strings"
	"testing"
)

// The order the enrichment render and CarryProvenance produced before the card
// backfill: the judgment first, the carried provenance appended after the
// machine stamps. Reordered, the read block leads and the machine block closes.
func TestReorderPutsTheReadBlockFirstAndTheMachineBlockLast(t *testing.T) {
	in := "---\n" +
		"title: A title\n" +
		"type: reference\n" +
		"status: active\n" +
		"confidence: 0.82\n" +
		"filing_confidence: high\n" +
		"tags: [a, b]\n" +
		"summary: One line.\n" +
		"updated: \"2026-09-12\"\n" +
		"enriched_by: enrich/1+prompt/5d3a4cca1b02\n" +
		"rules_hash: 3c57fd89087c1a25\n" +
		"enriched_at: \"2026-09-12T09:27:39Z\"\n" +
		"source: conversation\n" +
		"lifecycle: active\n" +
		"created: 2026-08-12\n" +
		"slug: a-title\n" +
		"---\n\nThe body.\n"
	want := "---\n" +
		"title: A title\n" +
		"type: reference\n" +
		"summary: One line.\n" +
		"status: active\n" +
		"lifecycle: active\n" +
		"filing_confidence: high\n" +
		"source: conversation\n" +
		"created: 2026-08-12\n" +
		"updated: \"2026-09-12\"\n" +
		"tags: [a, b]\n" +
		"slug: a-title\n" +
		"confidence: 0.82\n" +
		"enriched_by: enrich/1+prompt/5d3a4cca1b02\n" +
		"enriched_at: \"2026-09-12T09:27:39Z\"\n" +
		"rules_hash: 3c57fd89087c1a25\n" +
		"---\n\nThe body.\n"
	if got := Reorder(in); got != want {
		t.Fatalf("reordered:\n%s\nwant:\n%s", got, want)
	}
}

// A block list, a folded value and a comment travel with the key above them;
// the body after the fence — including a second `---` rule — is untouched.
func TestReorderKeepsEveryLineWithItsKeyAndTheBodyAsItWas(t *testing.T) {
	in := "---\n" +
		"slug: s\n" +
		"derived_from:\n" +
		"  - memory/semantic/a.md\n" +
		"  - memory/semantic/b.md\n" +
		"summary: >\n" +
		"  folded\n" +
		"title: T\n" +
		"---\n\nBody\n\n---\n\nstatus: not a key\n"
	got := Reorder(in)
	wantHead := "---\ntitle: T\nsummary: >\n  folded\nslug: s\nderived_from:\n" +
		"  - memory/semantic/a.md\n  - memory/semantic/b.md\n---\n"
	if !strings.HasPrefix(got, wantHead) {
		t.Fatalf("head:\n%s", got)
	}
	if !strings.HasSuffix(got, "---\n\nBody\n\n---\n\nstatus: not a key\n") {
		t.Fatalf("the body moved:\n%s", got)
	}
}

// A key neither block names — a record's own field, or one a writer appended —
// sits after the read block and keeps its order relative to its peers.
func TestReorderKeepsUnknownKeysInTheirOrderBetweenTheBlocks(t *testing.T) {
	in := "---\nslug: t\nday: 2026-09-05\nkind: session-trace\nsession: abc\ntitle: T\ncreated: 2026-09-05\n---\n"
	want := "---\ntitle: T\nkind: session-trace\ncreated: 2026-09-05\nday: 2026-09-05\nsession: abc\nslug: t\n---\n"
	if got := Reorder(in); got != want {
		t.Fatalf("got:\n%s\nwant:\n%s", got, want)
	}
}

// An ordered note is returned as the same string, so a writer that reorders
// changes nothing it did not have to; a second pass is a no-op.
func TestReorderLeavesAnOrderedNoteAndItsOwnOutputAlone(t *testing.T) {
	ordered := "---\ntitle: T\ntype: idea\nstatus: unfiled\nslug: t\n---\nbody\n"
	if got := Reorder(ordered); got != ordered {
		t.Fatalf("an ordered note changed:\n%s", got)
	}
	messy := "---\nslug: t\ntitle: T\n---\nbody\n"
	once := Reorder(messy)
	if twice := Reorder(once); twice != once {
		t.Fatalf("not idempotent:\n%s\n%s", once, twice)
	}
}

func TestReorderLeavesANoteWithoutABlockAlone(t *testing.T) {
	for _, in := range []string{"plain body\n", "---\nunterminated: yes\n", "---\n---\nempty\n"} {
		if got := Reorder(in); got != in {
			t.Fatalf("%q changed to %q", in, got)
		}
	}
}

// The two lists are disjoint and name no key twice; a key in both would have
// two places.
func TestTheTwoBlocksNameEachKeyOnce(t *testing.T) {
	seen := map[string]bool{}
	for _, k := range append(append([]string{}, ReadOrder...), MachineOrder...) {
		if seen[k] {
			t.Fatalf("%q is listed twice", k)
		}
		seen[k] = true
	}
}
