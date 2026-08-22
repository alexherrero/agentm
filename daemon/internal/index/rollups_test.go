package index

import (
	"context"
	"strings"
	"testing"
)

// The rollup stage's input: what the corpus mentions, how often, and whether
// each already has a file.

func TestEntityMentionsCountsNotesNotOccurrences(t *testing.T) {
	ctx := context.Background()
	x, vault := newVaultIndex(t)
	// One note mentioning the same repository three times is one note that is
	// about it, not three — the number that means "how much of the corpus is
	// about this" rather than "how wordy one note was".
	writeVaultNote(t, vault, "memory/a.md", "---\ntitle: a\n---\n\n"+
		"github.com/alexherrero/agentm and github.com/alexherrero/agentm again, "+
		"plus github.com/alexherrero/agentm.\n")
	writeVaultNote(t, vault, "memory/b.md", "---\ntitle: b\n---\n\n"+
		"Also github.com/alexherrero/agentm.\n")
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}

	got, err := x.EntityMentions(ctx, 1)
	if err != nil {
		t.Fatal(err)
	}
	var found *EntityMention
	for i := range got {
		if got[i].URI == "repo:alexherrero/agentm" {
			found = &got[i]
		}
	}
	if found == nil {
		t.Fatalf("the repository was not recorded at all: %+v", got)
	}
	if found.Mentions != 2 {
		t.Errorf("Mentions = %d, want 2 — the two notes, not the four references",
			found.Mentions)
	}
}

func TestEntityMentionsRespectsTheFloor(t *testing.T) {
	ctx := context.Background()
	x, vault := newVaultIndex(t)
	writeVaultNote(t, vault, "memory/a.md", "---\ntitle: a\n---\n\n"+
		"github.com/alexherrero/rare\n")
	for _, rel := range []string{"memory/b.md", "memory/c.md", "memory/d.md"} {
		writeVaultNote(t, vault, rel, "---\ntitle: n\n---\n\n"+
			"github.com/alexherrero/common\n")
	}
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}

	got, err := x.EntityMentions(ctx, 3)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range got {
		if e.Mentions < 3 {
			t.Errorf("%s came back with %d mentions, under the floor of 3",
				e.URI, e.Mentions)
		}
	}
	if len(got) != 1 {
		t.Errorf("got %d entities at or above the floor, want 1: %+v", len(got), got)
	}
}

// An entity that already has a file must not be proposed for a rollup. The
// match is on the path's last segment, because the URI carries a namespace the
// filesystem does not.
func TestEntityMentionsFindsAnExistingEntityFile(t *testing.T) {
	ctx := context.Background()
	x, vault := newVaultIndex(t)
	writeVaultNote(t, vault, "memory/a.md", "---\ntitle: a\n---\n\n"+
		"About cl/12345 and about cl/99999.\n")
	writeVaultNote(t, vault, "memory/entities/12345.md",
		"---\ntitle: the changelist\n---\n\nIts own file.\n")
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}

	got, err := x.EntityMentions(ctx, 1)
	if err != nil {
		t.Fatal(err)
	}
	byURI := map[string]EntityMention{}
	for _, e := range got {
		byURI[e.URI] = e
	}
	withFile, ok := byURI["cl:12345"]
	if !ok {
		t.Fatalf("cl:12345 was not recorded: %+v", got)
	}
	if !withFile.HasFile() {
		t.Errorf("cl:12345 has a file at %s and was reported as having none",
			"memory/entities/12345.md")
	}
	without, ok := byURI["cl:99999"]
	if !ok {
		t.Fatalf("cl:99999 was not recorded: %+v", got)
	}
	if without.HasFile() {
		t.Errorf("cl:99999 has no file and was reported as having %s", without.File)
	}
}

// The stub stage's input: what the corpus links to and does not have.

func TestDanglingTargetsCountsNotesNotLinks(t *testing.T) {
	ctx := context.Background()
	x, vault := newVaultIndex(t)
	// One note expecting the same missing target twice expects it once.
	//
	// Under two different display texts, because extraction dedups on target
	// plus text — the same target under different words is deliberately two
	// rows, "two facts about the graph rather than one", and that is the case
	// this dedup is for. Written `[[missing]]` twice, the corpus produces one
	// row and the property is untestable.
	writeVaultNote(t, vault, "memory/a.md", "---\ntitle: a\n---\n\n"+
		"See [[missing|the first way]] and again [[missing|the second way]].\n")
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}

	got, err := x.DanglingTargets(ctx, 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("dangling targets are %+v, want one", got)
	}
	if len(got[0].Sources) != 1 {
		t.Errorf("Sources = %v, want the one note that expects it — a stub floor "+
			"counted in links rather than notes lets one note vote twice",
			got[0].Sources)
	}
}

func TestDanglingTargetsRespectsTheFloor(t *testing.T) {
	ctx := context.Background()
	x, vault := newVaultIndex(t)
	writeVaultNote(t, vault, "memory/a.md",
		"---\ntitle: a\n---\n\nSee [[probably-a-typo]] and [[really-missing]].\n")
	writeVaultNote(t, vault, "memory/b.md",
		"---\ntitle: b\n---\n\nAlso [[really-missing]].\n")
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}

	got, err := x.DanglingTargets(ctx, 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].Target != "really-missing" {
		t.Errorf("targets at or above a floor of 2 are %+v, want only "+
			"really-missing — a stub for a typo resolves the link and hides the "+
			"mistake", got)
	}
}

func TestDanglingContextsAreCapped(t *testing.T) {
	ctx := context.Background()
	x, vault := newVaultIndex(t)
	for i := 0; i < maxStubContexts+5; i++ {
		writeVaultNote(t, vault, "memory/n"+string(rune('a'+i))+".md",
			"---\ntitle: n\n---\n\nA sentence about [[missing]] here.\n")
	}
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}

	got, err := x.DanglingTargets(ctx, 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("dangling targets are %+v", got)
	}
	if len(got[0].Sources) != maxStubContexts+5 {
		t.Errorf("Sources = %d, want every note that expects it",
			len(got[0].Sources))
	}
	if len(got[0].Contexts) != maxStubContexts {
		t.Errorf("Contexts = %d, want the cap of %d — a target every note "+
			"mentions should not carry the whole corpus",
			len(got[0].Contexts), maxStubContexts)
	}
}

// The two resolution paths agree, which is the property that matters: a second
// implementation of "which note does this name mean" would differ on exactly the
// cases that are hard to notice.
//
// Compared at the resolver rather than through Reconcile, because through
// Reconcile the answer is confounded by walk order — a link resolves against the
// paths indexed before it, so which of two equal candidates it finds depends on
// which was walked first. That is a real limitation and it is recorded in
// `resolve.go`; it is not what this test is about.
func TestBothResolutionPathsUseTheSameResolver(t *testing.T) {
	ctx := context.Background()
	x, vault := newVaultIndex(t)
	writeVaultNote(t, vault, "memory/near/target.md", "---\ntitle: near\n---\n\nx\n")
	writeVaultNote(t, vault, "memory/near/a.md",
		"---\ntitle: a\n---\n\nPoints at [[target]].\n")
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}

	viaWrite, err := x.Backlinks("memory/near/target.md")
	if err != nil {
		t.Fatal(err)
	}
	if len(viaWrite) != 1 {
		t.Fatalf("the write path resolved %d links, want 1", len(viaWrite))
	}

	if _, err := x.db.Exec(`UPDATE links SET resolved = ''`); err != nil {
		t.Fatal(err)
	}
	if _, err := x.ResolveDangling(ctx); err != nil {
		t.Fatal(err)
	}
	viaResolve, err := x.Backlinks("memory/near/target.md")
	if err != nil {
		t.Fatal(err)
	}
	if len(viaResolve) != len(viaWrite) {
		t.Errorf("the two paths disagree: %d backlinks via the write path, %d via "+
			"re-resolution", len(viaWrite), len(viaResolve))
	}
}

// The sibling tiebreak, reachable where it matters.
//
// Two equally-specific candidates are separated by which is nearer the note that
// wrote the link. Through the write path that is confounded by walk order — the
// tiebreak only sees candidates indexed before the linking note. Through
// re-resolution both already exist, which is the case where the tiebreak can
// actually do its job.
func TestReResolutionPrefersTheNearerOfTwoEqualCandidates(t *testing.T) {
	ctx := context.Background()
	x, vault := newVaultIndex(t)
	// The nearer candidate is deliberately the *deeper* one. Distance counts
	// steps from the source and therefore also penalises depth, so with the
	// source consulted the deep sibling wins on proximity and without it the
	// shallow stranger wins on depth. Arranged the other way round both answers
	// agree and the argument does no visible work.
	writeVaultNote(t, vault, "memory/deep/nest/target.md",
		"---\ntitle: the sibling\n---\n\nx\n")
	writeVaultNote(t, vault, "memory/target.md",
		"---\ntitle: the stranger\n---\n\nx\n")
	writeVaultNote(t, vault, "memory/deep/nest/a.md",
		"---\ntitle: a\n---\n\nPoints at [[target]].\n")
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}

	// Blank every resolution, so re-resolution runs with the whole corpus known.
	if _, err := x.db.Exec(`UPDATE links SET resolved = ''`); err != nil {
		t.Fatal(err)
	}
	if _, err := x.ResolveDangling(ctx); err != nil {
		t.Fatal(err)
	}

	sibling, err := x.Backlinks("memory/deep/nest/target.md")
	if err != nil {
		t.Fatal(err)
	}
	if len(sibling) != 1 {
		stranger, _ := x.Backlinks("memory/target.md")
		t.Errorf("the link resolved to the stranger (%d backlinks there, %d on "+
			"the sibling beside it); the note that wrote the link is not being "+
			"consulted", len(stranger), len(sibling))
	}
}

// The entity file match is on the last segment, not the whole URI — a namespace
// the filesystem does not carry must not stop the match.
func TestTheEntityFileMatchIgnoresTheNamespace(t *testing.T) {
	ctx := context.Background()
	x, vault := newVaultIndex(t)
	writeVaultNote(t, vault, "memory/a.md",
		"---\ntitle: a\n---\n\nAbout github.com/alexherrero/agentm.\n")
	writeVaultNote(t, vault, "memory/entities/agentm.md",
		"---\ntitle: agentm\n---\n\nIts file.\n")
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}

	got, err := x.EntityMentions(ctx, 1)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range got {
		if !strings.HasPrefix(e.URI, "repo:") {
			continue
		}
		if !e.HasFile() {
			t.Errorf("%s has a file at memory/entities/agentm.md and was reported "+
				"as having none", e.URI)
		}
	}
}
