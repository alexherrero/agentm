package dreaming

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/people"
)

// The defects the release review of task 179 found in the builder
// (2026-09-30), each held by a test.

// A hand-written note at a page's path is never overwritten: the page is held
// and listed, and the note stays as you wrote it.
func TestAHandWrittenNoteAtAPagesPathIsHeldNotOverwritten(t *testing.T) {
	notes := fixtureEntityNotes()
	notes["agent/memory/entities/repos/alexherrero-crickets.md"] =
		"---\ntitle: Crickets, by hand\ntype: reference\n---\n\nMy own notes on the toolkit.\n"
	root, x := entityVault(t, notes)
	plan, err := PlanEntities(root, filepath.Dir(root), x, PeopleOptions{}, nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	for _, in := range plan.Intents {
		if in.Rel == "memory/entities/repos/alexherrero-crickets.md" {
			t.Fatalf("the builder plans to write over a hand-written note (before %q)", string(in.Before))
		}
	}
	if len(plan.Held) != 1 || plan.Held[0] != "agent/memory/entities/repos/alexherrero-crickets.md" {
		t.Errorf("held %v, want the crickets page", plan.Held)
	}
}

// Two spellings of one person share one page, and a rebuild writes nothing.
func TestTwoSpellingsOfOnePersonShareOnePageAndARebuildIsANoOp(t *testing.T) {
	notes := map[string]string{
		"projects/agentm/project.yaml":            "slug: agentm\nrepositories:\n  - alexherrero/agentm\n",
		"projects/agentm/tasks/001-a/progress.md": "---\nupdated: 2026-09-20\npeople: [Jean-Luc Picard]\n---\n\nJean-Luc Picard reviewed.\n",
		"projects/agentm/tasks/002-b/progress.md": "---\nupdated: 2026-09-21\npeople: [Jean-Luc Picard]\n---\n\nJean-Luc Picard again.\n",
		"projects/agentm/tasks/003-c/progress.md": "---\nupdated: 2026-09-22\npeople: [Jean Luc Picard]\n---\n\nJean Luc Picard.\n",
		"projects/agentm/tasks/004-d/progress.md": "---\nupdated: 2026-09-23\npeople: [jean luc picard]\n---\n\njean luc picard.\n",
	}
	root, x := entityVault(t, notes)
	first, err := PlanEntities(root, filepath.Dir(root), x, PeopleOptions{}, nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	pages := personPages(first)
	if len(pages) != 1 || pages["person:jean-luc-picard"].SharedWork != 4 {
		t.Fatalf("person pages %+v, want one page with all four notes", pages)
	}
	applyEntityIntents(t, root, first.Intents)
	second, err := PlanEntities(root, filepath.Dir(root), x, PeopleOptions{}, nil, entityNow.Add(24*time.Hour))
	if err != nil {
		t.Fatal(err)
	}
	if len(second.Intents) != 0 {
		t.Errorf("a rebuild over an unchanged corpus planned %d write(s)", len(second.Intents))
	}
}

// A name in another script keeps its letters in the page's name.
func TestANonLatinNameGetsAPageOfItsOwn(t *testing.T) {
	notes := map[string]string{
		"projects/agentm/tasks/001-a/progress.md": "---\nupdated: 2026-09-20\npeople: [Дмитрий Иванов]\n---\n\nwith Дмитрий Иванов today\n",
		"projects/agentm/tasks/002-b/progress.md": "---\nupdated: 2026-09-21\npeople: [Дмитрий Иванов]\n---\n\nwith Дмитрий Иванов again\n",
	}
	root, x := entityVault(t, notes)
	plan, err := PlanEntities(root, filepath.Dir(root), x, PeopleOptions{}, nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	if p := personPages(plan)["person:дмитрий-иванов"]; !strings.HasSuffix(p.Rel, "/дмитрий-иванов.md") {
		t.Errorf("person pages %+v", personPages(plan))
	}
}

// A note the builder cannot read tonight holds the people pages as they were:
// nothing is removed or rewritten, and the plan says which note.
func TestAnUnreadableNoteHoldsThePeoplePagesForTheNight(t *testing.T) {
	if os.Getuid() == 0 {
		t.Skip("root reads a file whatever its mode")
	}
	root, x := entityVault(t, peopleFixture())
	first, err := PlanEntities(root, filepath.Dir(root), x, peopleOpts(t), nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	applyEntityIntents(t, root, first.Intents)
	unreadable := filepath.Join(filepath.Dir(root), "projects/agentm/decisions/ship-weekly.md")
	if err := os.Chmod(unreadable, 0); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.Chmod(unreadable, 0o644) })
	second, err := PlanEntities(root, filepath.Dir(root), x, peopleOpts(t), nil, entityNow.Add(24*time.Hour))
	if err != nil {
		t.Fatal(err)
	}
	if len(second.Removed) != 0 || len(second.PeopleHeld) != 1 {
		t.Errorf("removed %v, people held %v", second.Removed, second.PeopleHeld)
	}
	for _, in := range second.Intents {
		if strings.Contains(in.Rel, "/people/") {
			t.Errorf("a people page was written while a note could not be read: %s", in.Rel)
		}
	}
}

// A one-word alias that is a month names no one on its own.
func TestAOneWordAliasThatIsAMonthNamesNoOne(t *testing.T) {
	notes := map[string]string{
		"calendar/2026/2026-05-04-meetings.md": "---\ncreated: 2026-05-04\n---\n\nPlanning for May: ship the ranker.\n",
		"calendar/2026/2026-05-11-meetings.md": "---\ncreated: 2026-05-11\n---\n\nMay retro: the ranker shipped.\n",
	}
	tb, err := people.Parse("```people\naliases:\n  May Chen: [May]\n```\n")
	if err != nil {
		t.Fatal(err)
	}
	root, x := entityVault(t, notes)
	plan, err := PlanEntities(root, filepath.Dir(root), x, PeopleOptions{Table: tb}, nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	if pages := personPages(plan); len(pages) != 0 {
		t.Errorf("notes naming only the month gave %v", pages)
	}
}

// The vault root comes from the configuration, not from a `.obsidian/` folder.
func TestTheVaultRootIsTheConfiguredOne(t *testing.T) {
	root, x := entityVault(t, peopleFixture())
	first, err := PlanEntities(root, filepath.Dir(root), x, peopleOpts(t), nil, entityNow)
	if err != nil {
		t.Fatal(err)
	}
	applyEntityIntents(t, root, first.Intents)
	if err := os.RemoveAll(filepath.Join(filepath.Dir(root), ".obsidian")); err != nil {
		t.Fatal(err)
	}
	second, err := PlanEntities(root, filepath.Dir(root), x, peopleOpts(t), nil, entityNow.Add(24*time.Hour))
	if err != nil {
		t.Fatal(err)
	}
	if len(second.Intents) != 0 {
		t.Errorf("without .obsidian/ the unchanged corpus planned %d write(s); removed %v",
			len(second.Intents), second.Removed)
	}
}

// A builder that cannot plan leaves the entity map, and its pages, alone.
func TestABuilderThatCannotPlanKeepsTheEntityMap(t *testing.T) {
	root, x := entityVault(t, fixtureEntityNotes())
	indexPath := x.Path()
	x.Close()
	vault := filepath.Dir(root)
	cfg := &config.Config{VaultPath: vault, MemoryRoot: "agent", IndexPath: indexPath, EngineStateDir: t.TempDir()}
	if _, _, _, err := BuildEntities(cfg, BuildEntitiesOptions{Apply: true, Now: entityNow}); err != nil {
		t.Fatal(err)
	}
	mapPath := filepath.Join(root, "memory/mocs/moc-entities.md")
	if _, err := os.Stat(mapPath); err != nil {
		t.Fatal(err)
	}
	putEntityNote(t, vault, "standards/people/aliases.md", "```people\naliases: [unclosed\n```\n")
	rep, err := Run(cfg, Options{Apply: true, Force: true, Now: entityNow.Add(24 * time.Hour)})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Entities.Skipped == "" {
		t.Error("a table that does not parse did not stop the builder")
	}
	if _, err := os.Stat(mapPath); err != nil {
		t.Errorf("the entity map was removed while its pages stay: %v", err)
	}
}

// A memory card about meetings is not a meeting's notes.
func TestACardAboutMeetingsIsNotSharedWork(t *testing.T) {
	if IsSharedWork("agent/memory/semantic/prefer-short-meetings.md") {
		t.Error("a card about meetings counted as shared work")
	}
}
