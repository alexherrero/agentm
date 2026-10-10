package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// A type that falls below the floor loses its page, on the operator's ruling of
// 2026-09-13 (agentm-vault plan 07 review, finding 2). moc-memory.md lists the
// type's notes in full, and a page left behind would fail the maps gate with
// nothing to repair it. The removal is a journaled delete carrying the page's
// bytes.
func TestAPageWhoseTypeFellBelowTheFloorIsRemoved(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC)
	for i := 0; i < 4; i++ {
		writeTyped(t, root, fmt.Sprintf("memory/semantic/fact-%d.md", i), "fact", "2026-08-01", "A fact worth keeping.\n")
	}
	for i := 0; i < 5; i++ {
		writeTyped(t, root, fmt.Sprintf("memory/procedural/step-%d.md", i), "workflow", "2026-08-10", "A step.\n")
	}
	factPage := "---\nkind: moc\nslug: fact\ntype_of_members: fact\n---\n\n# fact\n\n- [[fact-0]]\n"
	ideaPage := "---\nkind: moc\nslug: idea\ntype_of_members: idea\n---\n\n# idea\n"
	writeRaw(t, root, "memory/mocs/fact.md", factPage)
	writeRaw(t, root, "memory/mocs/idea.md", ideaPage)
	// Pages the job did not write for a type are not its to remove: one under a
	// type's name made by hand, and one under no type's name at all.
	writeRaw(t, root, "memory/mocs/fix.md", "---\nkind: moc\n---\n\n# my own fix notes\n")
	writeRaw(t, root, "memory/mocs/notes.md", "---\nkind: moc\n---\n\n# not a type's page\n")
	r := &rules.Rules{}
	r.MemoryTypes = []string{"fact", "fix", "idea", "workflow"}

	plan, err := PlanMocs(root, r, now)
	if err != nil {
		t.Fatal(err)
	}
	deleted := map[string]string{}
	for _, in := range plan.Intents {
		if in.Delete {
			deleted[in.Rel] = string(in.Before)
		}
	}
	if want := map[string]string{"memory/mocs/fact.md": factPage, "memory/mocs/idea.md": ideaPage}; !reflect.DeepEqual(deleted, want) {
		t.Errorf("deleted %v; want the page of the type below the floor and of the type with no live note left", deleted)
	}
	if want := []string{"memory/mocs/fact.md", "memory/mocs/idea.md"}; !reflect.DeepEqual(plan.Removed, want) {
		t.Errorf("removed %v, want %v", plan.Removed, want)
	}
	memory := ""
	for _, in := range plan.Intents {
		if in.Rel == MocRel(MocMemorySlug) {
			memory = string(in.After)
		}
	}
	for _, want := range []string{"- fact — 4 notes, listed below\n", "\n## fact\n", "[[fact-0]]", "[[workflow]]"} {
		if !strings.Contains(memory, want) {
			t.Errorf("moc-memory.md lacks %q:\n%s", want, memory)
		}
	}
}

// With nothing typed to map, the pass writes no map and removes none: a corpus
// that reads as empty is more likely a scan gone wrong than a vault emptied.
func TestNoTypedNoteRemovesNoPage(t *testing.T) {
	root := t.TempDir()
	writeRaw(t, root, "memory/mocs/fact.md", "---\nkind: moc\nslug: fact\ntype_of_members: fact\n---\n\n# fact\n")
	r := &rules.Rules{}
	r.MemoryTypes = []string{"fact"}

	plan, err := PlanMocs(root, r, time.Date(2026, 9, 5, 9, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Intents) != 0 || len(plan.Removed) != 0 {
		t.Errorf("a map a fuller corpus left behind stays as it is when nothing is typed: removed %v", plan.Removed)
	}
}

// applyMocs writes a plan's pages to disk, as the night would.
func applyMocs(t *testing.T, root string, plan MocsPlan) {
	t.Helper()
	for _, in := range plan.Intents {
		if in.Delete {
			continue
		}
		writeRaw(t, root, in.Rel, string(in.After))
	}
}

// probeNote is the daemon's self-probe as the capture door writes it: an
// ordinary `type: reference` card in memory/semantic, marked `probe:`.
func probeNote(stamp string) string {
	return "---\ntitle: \"AgentM self-probe " + stamp + "\"\ntype: reference\nstatus: active\n" +
		"created: " + stamp + "\nupdated: " + stamp + "\nprobe: self-probe\n---\n\nSynthetic round-trip probe.\n"
}

// The daemon's self-probe is no member of any map (task 190 step 3). It is
// written and retired every day, so as a member it rewrote reference.md and
// moc-memory.md every night with nothing in either changed: the write-quality
// audit of 2026-10-07 found both rewritten on 10-05 and 10-06 for the probe.
func TestTheSelfProbeIsNoMemberAndANewOneWritesNoMap(t *testing.T) {
	root := t.TempDir()
	for i := 0; i < 5; i++ {
		writeTyped(t, root, fmt.Sprintf("memory/semantic/ref-%d.md", i), "reference", "2026-09-20", "A reference.\n")
	}
	writeRaw(t, root, "memory/semantic/agentm-self-probe-2026-10-05t07-32-20z.md", probeNote("2026-10-05T07:32:20Z"))
	first, err := PlanMocs(root, nil, time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	for _, in := range first.Intents {
		if strings.Contains(string(in.After), "self-probe") {
			t.Errorf("%s lists the self-probe:\n%s", in.Rel, in.After)
		}
	}
	for _, p := range first.Pages {
		if p.Rel == MocRel("reference") && p.Members != 5 {
			t.Errorf("reference.md has %d members, want the 5 references without the probe", p.Members)
		}
	}
	applyMocs(t, root, first)

	// The next night: the old probe retired, a new one written, nothing else.
	if err := os.Remove(filepath.Join(root, "memory/semantic/agentm-self-probe-2026-10-05t07-32-20z.md")); err != nil {
		t.Fatal(err)
	}
	writeRaw(t, root, "memory/semantic/agentm-self-probe-2026-10-06t07-35-48z.md", probeNote("2026-10-06T07:35:48Z"))
	next, err := PlanMocs(root, nil, time.Date(2026, 10, 6, 9, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	for _, in := range next.Intents {
		t.Errorf("a night whose only change is a new self-probe rewrote %s:\n%s", in.Rel, in.After)
	}
}

// A map is rewritten only when its membership or a member's line changes
// (task 190 step 3). A type whose members carry no date takes today's date as
// its newest, so its page and moc-memory.md moved `updated` every night with
// nothing else changed; a regeneration that differs from the page on disk in
// `updated` alone keeps the page as it is.
func TestAMapWhoseOnlyChangeWouldBeItsDateIsNotRewritten(t *testing.T) {
	root := t.TempDir()
	for i := 0; i < 5; i++ {
		writeTyped(t, root, fmt.Sprintf("memory/procedural/undated-%d.md", i), "workflow", "", "A step with no date.\n")
	}
	first, err := PlanMocs(root, nil, time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if len(first.Intents) == 0 {
		t.Fatal("the first night wrote no map")
	}
	applyMocs(t, root, first)

	next, err := PlanMocs(root, nil, time.Date(2026, 10, 6, 9, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	for _, in := range next.Intents {
		t.Errorf("an unchanged membership rewrote %s for its date alone:\n%s", in.Rel, in.After)
	}
	for _, p := range next.Pages {
		if p.Changed {
			t.Errorf("%s reports changed on an unchanged membership", p.Rel)
		}
	}
}

// A real change still writes, and carries the new date with it.
func TestAMapWhoseMembershipChangedIsRewrittenWithItsNewDate(t *testing.T) {
	root := t.TempDir()
	for i := 0; i < 5; i++ {
		writeTyped(t, root, fmt.Sprintf("memory/procedural/step-%d.md", i), "workflow", "2026-09-01", "A step.\n")
	}
	first, err := PlanMocs(root, nil, time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	applyMocs(t, root, first)
	writeTyped(t, root, "memory/procedural/step-new.md", "workflow", "2026-10-05", "A new step.\n")
	next, err := PlanMocs(root, nil, time.Date(2026, 10, 6, 9, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	pages := map[string]string{}
	for _, in := range next.Intents {
		pages[in.Rel] = string(in.After)
	}
	for _, rel := range []string{MocRel("workflow"), MocRel(MocMemorySlug)} {
		got, ok := pages[rel]
		if !ok {
			t.Errorf("a new member did not rewrite %s", rel)
			continue
		}
		if !strings.Contains(got, "updated: 2026-10-05\n") {
			t.Errorf("%s does not carry the new member's date:\n%s", rel, got)
		}
	}
}
