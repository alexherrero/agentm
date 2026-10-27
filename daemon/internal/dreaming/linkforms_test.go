package dreaming

import (
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/fmlist"
)

// The readers of link-valued keys read the quoted-wikilink form (task 193
// step 6, LD-5) as well as the bare paths they were written with.

func TestRefileReadsARelatedTwinInEveryForm(t *testing.T) {
	root, _ := rootMapVault(t)
	r := testRules(t)
	flagged := func(related string) string {
		return "---\ntitle: flagged\ntype: fact\nstatus: active\nreview_flags: [near-duplicate]\nrelated: " + related +
			"\n---\n\nA fact with a twin.\n"
	}
	writeRaw(t, root, "memory/semantic/twin.md", "---\ntitle: twin\ntype: fact\nstatus: active\n---\n\nA fact, nearly.\n")
	writeRaw(t, root, "memory/semantic/by-name.md", flagged(`["[[twin]]"]`))
	writeRaw(t, root, "memory/semantic/by-vault-path.md", flagged(`"[[agent/memory/semantic/twin|the twin]]"`))
	writeRaw(t, root, "memory/semantic/by-memory-path.md", flagged("memory/semantic/twin.md"))
	writeRaw(t, root, "memory/semantic/gone-by-name.md", flagged(`["[[gone]]"]`))
	writeRaw(t, root, "memory/semantic/gone-by-path.md", flagged(`"[[agent/memory/semantic/gone]]"`))
	plan, err := PlanRefile(root, r)
	if err != nil {
		t.Fatal(err)
	}
	got := map[string]bool{}
	for _, u := range plan.Unflags {
		got[u.Rel] = true
	}
	for rel, want := range map[string]bool{
		"memory/semantic/by-name.md": false, "memory/semantic/by-vault-path.md": false,
		"memory/semantic/by-memory-path.md": false,
		"memory/semantic/gone-by-name.md":   true, "memory/semantic/gone-by-path.md": true,
	} {
		if got[rel] != want {
			t.Errorf("%s unflagged = %v, want %v (unflags %+v)", rel, got[rel], want, plan.Unflags)
		}
	}
}

func TestALessonsSuccessorIsReadInEveryForm(t *testing.T) {
	root, _ := rootMapVault(t)
	writeRaw(t, root, "memory/crystallized/keep.md", "---\ntitle: keep\nlifecycle: active\n---\n\nThe lesson.\n")
	for _, value := range []string{
		"memory/crystallized/keep.md",
		`"[[keep]]"`,
		`"[[agent/memory/crystallized/keep|the lesson]]"`,
		`["[[agent/memory/crystallized/keep]]"]`,
		"\n  - \"[[keep]]\"",
	} {
		text := "---\ntitle: old\nsuperseded_by: " + value + "\n---\n\nOld.\n"
		if got := liveSuccessor(root, "memory/crystallized/old.md", text); got != "memory/crystallized/keep.md" {
			t.Errorf("superseded_by %q: successor %q, want memory/crystallized/keep.md", value, got)
		}
	}
	// A source version names no lesson.
	if got := liveSuccessor(root, "memory/crystallized/old.md", "---\nsuperseded_by: \"src-1 at v3\"\n---\n"); got != "" {
		t.Errorf("a source version answered as successor %q", got)
	}
}

// The copies job names the earliest copy as a quoted wikilink by vault path
// (task 193 step 7), and step 6's parser reads it back as that note.
func TestACopysSuccessorReadsBackAsTheCanonical(t *testing.T) {
	root, _ := rootMapVault(t)
	body := "Run the checks, then push.\n"
	writeRaw(t, root, "memory/procedural/canon.md", "---\ntitle: canon\nkind: workflow\nstatus: active\ncreated: 2026-01-01\n---\n\n"+body)
	writeRaw(t, root, "memory/procedural/copy.md", "---\ntitle: copy\nkind: workflow\nstatus: active\ncreated: 2026-02-01\n---\n\n"+body)
	plan, err := PlanCopies(root, 0)
	if err != nil || len(plan.Intents) != 1 {
		t.Fatalf("plan %+v, %v", plan.Intents, err)
	}
	l, ok := fmlist.First(fmlist.LinksOf(string(plan.Intents[0].After), "superseded_by"))
	if !ok || l.Target != "agent/memory/procedural/canon" || l.MemoryRel("agent") != "memory/procedural/canon.md" {
		t.Errorf("superseded_by reads back as %+v", l)
	}
}
