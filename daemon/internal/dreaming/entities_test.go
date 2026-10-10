package dreaming

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/index"
)

// Task 179 step 5: the builder writes repository, issue and release pages
// from a fixture vault, a rebuild writes nothing, and a page below its bar is
// removed with its bytes in the journal.

var entityNow = time.Date(2026, 9, 30, 3, 0, 0, 0, time.UTC)

// entityVault is a vault root with a memory root under `agent/`, indexed.
func entityVault(t *testing.T, notes map[string]string) (root string, x *index.Index) {
	t.Helper()
	vault := filepath.Join(t.TempDir(), "vault")
	if err := os.MkdirAll(filepath.Join(vault, ".obsidian"), 0o755); err != nil {
		t.Fatal(err)
	}
	for rel, body := range notes {
		putEntityNote(t, vault, rel, body)
	}
	x, err := index.Open(filepath.Join(t.TempDir(), "index.db"), vault, "agent", false)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { x.Close() })
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}
	return filepath.Join(vault, "agent"), x
}

func putEntityNote(t *testing.T, vault, rel, body string) {
	t.Helper()
	abs := filepath.Join(vault, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(abs, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

// applyEntityIntents writes a plan to disk the way the journal would.
func applyEntityIntents(t *testing.T, root string, intents []Intent) {
	t.Helper()
	for _, in := range intents {
		abs := filepath.Join(root, filepath.FromSlash(in.Rel))
		if in.Delete {
			if err := os.Remove(abs); err != nil {
				t.Fatal(err)
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(abs, in.After, 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

func fixtureEntityNotes() map[string]string {
	return map[string]string{
		"projects/agentm/project.yaml": "slug: agentm\nrepositories:\n  - alexherrero/agentm\n",
		"projects/agentm/tasks/001-a/progress.md": "---\nupdated: 2026-09-20\n---\n\n" +
			"Paired with https://github.com/alexherrero/crickets today; closed #466.\n" +
			"Shipped v10.0.0. Landed as 8296fc5a.\n",
		"projects/agentm/tasks/002-b/plan.md": "---\nupdated: 2026-09-25\n---\n\n" +
			"Reopened #466 after v10.0.0.\nSee github.com/alexherrero/crickets again. 8296fc5a.\n",
		// A third origin for the issue and the release, whose bar is three
		// (task 190); the repository's is two.
		"projects/agentm/research/history.md": "---\nupdated: 2026-09-22\n---\n\n" +
			"The fix for #466 landed in v10.0.0.\n",
		"agent/memory/semantic/a-card.md": "---\ntitle: A card\ntype: reference\ncreated: 2026-08-01\nproject: agentm\n---\n\n" +
			"The toolkit lives at github.com/alexherrero/crickets.\n",
		"personal/notes/loose.md":     "---\ntitle: Loose\n---\n\nSee #77 and github.com/alexherrero/rare.\n",
		"personal/notes/loose-too.md": "---\ntitle: Loose too\n---\n\nSee #77 as well.\n",
		// A superseded note never counts: rare stays at one mention.
		"agent/memory/semantic/old.md": "---\ntitle: Old\ntype: reference\nlifecycle: superseded\n---\n\n" +
			"github.com/alexherrero/rare was the old home.\n",
		// A map lists what it maps; it is no mention.
		"projects/moc-projects.md": "---\ntitle: Projects\nkind: moc\n---\n\ngithub.com/alexherrero/rare\n",
		// A hand-written note in entities/ is never touched.
		"agent/memory/entities/repos/handmade.md": "---\ntitle: Handmade\ntype: reference\n---\n\ngithub.com/alexherrero/rare\n",
		// A page the builder wrote for an entity no longer mentioned enough.
		"agent/memory/entities/repos/alexherrero-gone.md": "---\ntitle: alexherrero/gone\nkind: entity-profile\n" +
			"created: 2026-09-01\nupdated: 2026-09-01\nentity_type: repo\nentity_id: repo:alexherrero/gone\n---\n\n# gone\n",
	}
}

func TestTheBuilderWritesRepoIssueAndReleasePagesAndNothingElse(t *testing.T) {
	root, x := entityVault(t, fixtureEntityNotes())
	plan, err := PlanEntities(root, filepath.Dir(root), x, PeopleOptions{}, nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	got := map[string]EntityPage{}
	for _, p := range plan.Pages {
		got[p.ID] = p
	}
	for id, want := range map[string]int{
		"repo:alexherrero/crickets":          3,
		"issue:alexherrero/agentm#466":       3,
		"release:alexherrero/agentm@v10.0.0": 3,
	} {
		if got[id].Mentions != want {
			t.Errorf("%s: %d mentions, want %d (pages %v)", id, got[id].Mentions, want, plan.Pages)
		}
	}
	if len(plan.Pages) != 3 {
		t.Errorf("pages %v: want exactly the three — no commit, no bare number, nothing under its bar", plan.Pages)
	}
	if len(plan.Removed) != 1 || plan.Removed[0] != "memory/entities/repos/alexherrero-gone.md" {
		t.Errorf("removed %v, want the stale builder page and never the hand-written note", plan.Removed)
	}
	var page string
	for _, in := range plan.Intents {
		if in.Rel == "memory/entities/repos/alexherrero-crickets.md" {
			page = string(in.After)
		}
	}
	for _, want := range []string{
		"title: alexherrero/crickets\nkind: entity-profile\ncreated: 2026-09-30\nupdated: 2026-09-25\n",
		"entity_type: repo\nentity_id: \"repo:alexherrero/crickets\"\nprojects: [agentm]\n",
		"first_seen: 2026-08-01\nlast_seen: 2026-09-25\nmentions: 3\nslug: alexherrero-crickets\naliases: [crickets]\n",
		"## agentm\n\n### memory\n\n- 2026-08-01 · [[agent/memory/semantic/a-card|A card]]\n",
		"### projects\n\n- 2026-09-25 · [[projects/agentm/tasks/002-b/plan|",
	} {
		if !strings.Contains(page, want) {
			t.Errorf("the crickets page lacks %q:\n%s", want, page)
		}
	}
}

func TestARebuildWritesNothingAndAPageUnderItsBarIsRemovedWithItsBytesJournaled(t *testing.T) {
	notes := fixtureEntityNotes()
	root, x := entityVault(t, notes)
	first, err := PlanEntities(root, filepath.Dir(root), x, PeopleOptions{}, nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	applyEntityIntents(t, root, first.Intents)
	second, err := PlanEntities(root, filepath.Dir(root), x, PeopleOptions{}, nil, entityNow.Add(24*time.Hour))
	if err != nil {
		t.Fatal(err)
	}
	if len(second.Intents) != 0 {
		t.Fatalf("a rebuild over an unchanged corpus planned %d write(s): %+v", len(second.Intents), second.Intents)
	}

	// One of the three origins naming #466 stops naming it: the page falls
	// under the bar of three and goes, and the journal keeps what it said.
	vault := filepath.Dir(root)
	putEntityNote(t, vault, "projects/agentm/tasks/002-b/plan.md", "---\nupdated: 2026-09-25\n---\n\n"+
		"Reopened after v10.0.0.\nSee github.com/alexherrero/crickets again.\n")
	if _, err := x.Reconcile(); err != nil {
		t.Fatal(err)
	}
	third, err := PlanEntities(root, filepath.Dir(root), x, PeopleOptions{}, nil, entityNow.Add(48*time.Hour))
	if err != nil {
		t.Fatal(err)
	}
	const gone = "memory/entities/issues/alexherrero-agentm-466.md"
	if len(third.Removed) != 1 || third.Removed[0] != gone {
		t.Fatalf("removed %v, want %s", third.Removed, gone)
	}
	onDisk, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(gone)))
	if err != nil {
		t.Fatal(err)
	}
	journal, err := OpenJournal(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	for i, in := range third.Intents {
		if _, err := journal.Commit(root, "run-1", "run-1-"+string(rune('a'+i)), in, entityNow); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := os.Stat(filepath.Join(root, filepath.FromSlash(gone))); !os.IsNotExist(err) {
		t.Fatalf("the page under its bar is still on disk: %v", err)
	}
	entries, err := journal.Read()
	if err != nil {
		t.Fatal(err)
	}
	kept := false
	for _, e := range entries {
		if e.Rel == gone && e.Removed != "" {
			b, _ := base64.StdEncoding.DecodeString(e.Removed)
			kept = string(b) == string(onDisk)
		}
	}
	if !kept {
		t.Error("the journal does not hold the removed page's bytes")
	}
}

// The mocs job lists the builder's pages by type, people first; with no pages
// it removes the map it generated before, and never a page it did not write.
func TestTheEntityMapListsThePagesAndGoesWithThem(t *testing.T) {
	root := filepath.Join(t.TempDir(), "agent")
	pages := []EntityPage{
		{Type: "repo", ID: "repo:alexherrero/crickets", Rel: "agent/memory/entities/repos/alexherrero-crickets.md",
			Title: "alexherrero/crickets", Mentions: 12, Last: "2026-09-28"},
		{Type: "person", ID: "person:ben-okafor", Rel: "agent/memory/entities/people/ben-okafor.md",
			Title: "Ben Okafor", Mentions: 3, SharedWork: 2, Last: "2026-09-12"},
	}
	plan := PlanEntityMap(root, pages, entityNow)
	if len(plan.Intents) != 1 {
		t.Fatalf("intents %+v", plan.Intents)
	}
	text := string(plan.Intents[0].After)
	people, repos := strings.Index(text, "## People (1)"), strings.Index(text, "## Repositories (1)")
	if people < 0 || repos < people ||
		!strings.Contains(text, "- [[agent/memory/entities/people/ben-okafor|Ben Okafor]] — 2 pieces of shared work, 3 notes") ||
		!strings.Contains(text, "- [[agent/memory/entities/repos/alexherrero-crickets|alexherrero/crickets]] — 12 notes") ||
		!strings.Contains(text, "updated: 2026-09-28\n") {
		t.Errorf("the entity map:\n%s", text)
	}
	applyEntityIntents(t, root, plan.Intents)
	if again := PlanEntityMap(root, pages, entityNow.Add(24*time.Hour)); len(again.Intents) != 0 {
		t.Errorf("an unchanged map was rewritten: %+v", again.Intents)
	}
	gone := PlanEntityMap(root, nil, entityNow)
	if len(gone.Intents) != 1 || !gone.Intents[0].Delete {
		t.Errorf("with no pages the generated map stays: %+v", gone.Intents)
	}
}

// A by-hand build writes the pages and the map through the journal under the
// lock, and a second build over the same corpus writes nothing.
func TestABuildByHandJournalsThePagesAndARebuildIsANoOp(t *testing.T) {
	notes := fixtureEntityNotes()
	root, x := entityVault(t, notes)
	indexPath := x.Path()
	x.Close()
	cfg := &config.Config{VaultPath: filepath.Dir(root), MemoryRoot: "agent", IndexPath: indexPath,
		EngineStateDir: t.TempDir()}
	plan, entityMap, rep, err := BuildEntities(cfg, BuildEntitiesOptions{Apply: true, Now: entityNow})
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Pages) != 3 || rep.Applied != len(plan.Intents)+len(entityMap.Intents) || rep.Skipped != 0 {
		t.Fatalf("pages %d, applied %d of %d, skipped %d", len(plan.Pages), rep.Applied,
			len(plan.Intents)+len(entityMap.Intents), rep.Skipped)
	}
	for _, rel := range []string{"memory/entities/repos/alexherrero-crickets.md", "memory/mocs/moc-entities.md"} {
		if _, err := os.Stat(filepath.Join(root, filepath.FromSlash(rel))); err != nil {
			t.Errorf("%s was not written: %v", rel, err)
		}
	}
	again, againMap, rep2, err := BuildEntities(cfg, BuildEntitiesOptions{Apply: true, Now: entityNow.Add(time.Hour)})
	if err != nil {
		t.Fatal(err)
	}
	if len(again.Intents)+len(againMap.Intents) != 0 || rep2.Applied != 0 {
		t.Errorf("a rebuild wrote %d page(s) and %d map(s)", len(again.Intents), len(againMap.Intents))
	}
}
