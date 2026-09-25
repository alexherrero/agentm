package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The maps of the two shared spaces the operator added on 2026-09-24
// (agentm-vault § The layout): `resources/moc-resources.md` and
// `systems/moc-systems.md`.

func spacesFixture(t *testing.T) (root, vault string) {
	t.Helper()
	root, vault = rootMapVault(t)
	for i := 0; i < spaceMapListLimit+3; i++ {
		writeAt(t, vault, fmt.Sprintf("resources/topics/sqlite/card-%02d.md", i),
			fmt.Sprintf("---\ntitle: SQLite card %02d\nkind: reference\ncreated: 2026-09-0%d\n---\n\nBody.\n", i, 1+i%5))
	}
	writeAt(t, vault, "resources/topics/go-git/one-card.md", "---\ntitle: go-git card\ncreated: 2026-08-20\n---\n\nBody.\n")
	writeAt(t, vault, "resources/university/index.md",
		"---\ntitle: University shelf\ncreated: 2026-09-24\nupdated: 2026-09-30\n---\n\nStudy guides go here.\n")
	writeAt(t, vault, "systems/homelab/system.md", "---\ntitle: Homelab\nkind: system\ncreated: 2026-09-10\n---\n\nThe homelab.\n")
	writeAt(t, vault, "systems/homelab/components/nas-backup.md", "---\ntitle: NAS backup\nkind: component\ncreated: 2026-09-11\n---\n\nBody.\n")
	writeAt(t, vault, "systems/homelab/components/network-topology.md", "---\ntitle: Network topology\nkind: component\n---\n\nBody.\n")
	writeAt(t, vault, "systems/agentm/system.md", "---\ntitle: AgentM\nkind: system\ncreated: 2026-09-12\n---\n\nFront door.\n")
	return root, vault
}

func TestSpaceMapsAreWrittenAtEachSpaceRoot(t *testing.T) {
	root, _ := spacesFixture(t)
	plan, err := PlanSpaceMaps(root, projectsNow)
	if err != nil {
		t.Fatal(err)
	}
	res := plannedText(plan, "../resources/moc-resources.md")
	sys := plannedText(plan, "../systems/moc-systems.md")
	if res == "" || sys == "" {
		t.Fatalf("want both maps planned, got %d intents: %+v", len(plan.Intents), plan.Pages)
	}
	for _, want := range []string{
		"slug: moc-resources", "kind: moc", "members: 17", "updated: 2026-09-24",
		"## topics", "- `sqlite/` — 15 notes", "- `go-git/`", "  - [[resources/topics/go-git/one-card|go-git card]]",
		"## university", "- [[resources/university/index|University shelf]]",
	} {
		if !strings.Contains(res, want) {
			t.Errorf("resources map lacks %q:\n%s", want, res)
		}
	}
	// A folder past the limit is counted, not listed: the map must not become a
	// second copy of every card title.
	if strings.Contains(res, "SQLite card") {
		t.Errorf("the large folder was listed title by title:\n%s", res)
	}
	for _, want := range []string{
		"members: 4", "## agentm", "- [[systems/agentm/system|AgentM]]",
		"## homelab", "- [[systems/homelab/system|Homelab]]", "- `components/`",
		"  - [[systems/homelab/components/nas-backup|NAS backup]]",
		"  - [[systems/homelab/components/network-topology|Network topology]]",
	} {
		if !strings.Contains(sys, want) {
			t.Errorf("systems map lacks %q:\n%s", want, sys)
		}
	}
}

// Dated by `created`, never by the enrichment stamp: the university note's
// `updated: 2026-09-30` must not become the page's date.
func TestSpaceMapIsDatedByCreated(t *testing.T) {
	root, _ := spacesFixture(t)
	plan, _ := PlanSpaceMaps(root, projectsNow)
	res := plannedText(plan, "../resources/moc-resources.md")
	if strings.Contains(res, "2026-09-30") {
		t.Errorf("the map took an enrichment stamp as its date:\n%s", res)
	}
}

func TestSpaceMapRegenerationOverUnchangedInputsWritesNothing(t *testing.T) {
	root, vault := spacesFixture(t)
	plan, _ := PlanSpaceMaps(root, projectsNow)
	for _, in := range plan.Intents {
		writeAt(t, root, in.Rel, string(in.After))
	}
	again, _ := PlanSpaceMaps(root, projectsNow.AddDate(0, 0, 3))
	if len(again.Intents) != 0 {
		t.Errorf("an unchanged space rewrote its map: %+v", again.Intents)
	}
	if len(again.Pages) != 2 {
		t.Errorf("want both pages reported, got %+v", again.Pages)
	}
	// The map is not a member of itself.
	if _, err := os.Stat(filepath.Join(vault, "resources", "moc-resources.md")); err != nil {
		t.Fatalf("the map was not written where the plan said: %v", err)
	}
	if !strings.Contains(string(again.Pages[0].Rel), "moc-") || again.Pages[0].Members != 17 {
		t.Errorf("the written map counted itself: %+v", again.Pages[0])
	}
}

// No space, no page; an empty space, no page — a directory is discovered, and
// a map of nothing is not written.
func TestSpaceMapsSkipAbsentAndEmptySpaces(t *testing.T) {
	root, vault := rootMapVault(t)
	plan, err := PlanSpaceMaps(root, projectsNow)
	if err != nil || len(plan.Intents) != 0 {
		t.Fatalf("no space should plan nothing: %v %+v", err, plan.Intents)
	}
	if err := os.MkdirAll(filepath.Join(vault, "systems", "homelab"), 0o755); err != nil {
		t.Fatal(err)
	}
	writeAt(t, vault, "systems/Icon\r", "")
	writeAt(t, vault, "systems/.DS_Store", "")
	plan, _ = PlanSpaceMaps(root, projectsNow)
	if len(plan.Intents) != 0 {
		t.Errorf("an empty space got a map: %+v", plan.Intents)
	}
}

// The root map lists both maps, including on the night they are first planned.
func TestRootMapListsTheSharedSpaceMaps(t *testing.T) {
	root, _ := spacesFixture(t)
	plan, _ := PlanSpaceMaps(root, projectsNow)
	var planned []string
	for _, p := range plan.Pages {
		planned = append(planned, p.Rel)
	}
	rootPlan, err := PlanRootMap(root, planned, projectsNow)
	if err != nil {
		t.Fatal(err)
	}
	text := rootMapText(rootPlan)
	for _, want := range []string{"## Resources", "- [[moc-resources]]", "## Systems", "- [[moc-systems]]"} {
		if !strings.Contains(text, want) {
			t.Errorf("root map lacks %q:\n%s", want, text)
		}
	}
}
