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
		"## topics", "- [[resources/topics/sqlite/moc-sqlite|sqlite/]] — 15 notes", "- `go-git/`", "  - [[resources/topics/go-git/one-card|go-git card]]",
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
	// Both space maps, and the counted `sqlite/` folder's own map.
	if len(again.Pages) != 3 {
		t.Errorf("want three pages reported, got %+v", again.Pages)
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
	// What a sync client or a desktop leaves behind is not a note. (Drive's
	// `Icon\r` is the same case, and a name Windows cannot create.)
	writeAt(t, vault, "systems/.DS_Store", "")
	writeAt(t, vault, "systems/homelab/desktop.ini", "")
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

// A counted folder gets a map of its own, beside its notes, listing every note
// newest first under a link back up to the space map; the space map's counted
// line links it (the graph review of 2026-11-21: a counted folder's cards had
// no link in or out).
func TestCountedSpaceFolderGetsItsOwnMap(t *testing.T) {
	root, _ := spacesFixture(t)
	plan, _ := PlanSpaceMaps(root, projectsNow)
	sub := plannedText(plan, "../resources/topics/sqlite/moc-sqlite.md")
	if sub == "" {
		t.Fatalf("the counted folder got no map: %+v", plan.Pages)
	}
	for _, want := range []string{
		"title: resources topics/sqlite/ — map of content", "kind: moc", "slug: moc-sqlite",
		"members: 15", "updated: 2026-09-05", "tags: [moc, resources]",
		"# topics/sqlite/", "[[resources/moc-resources|resources]]",
		"The 15 notes in `topics/sqlite/` of the resources space, newest first.",
	} {
		if !strings.Contains(sub, want) {
			t.Errorf("the folder map lacks %q:\n%s", want, sub)
		}
	}
	for i := 0; i < spaceMapListLimit+3; i++ {
		want := fmt.Sprintf("- [[resources/topics/sqlite/card-%02d|SQLite card %02d]] · 2026-09-0%d", i, i, 1+i%5)
		if !strings.Contains(sub, want) {
			t.Errorf("the folder map lacks card %02d (%q):\n%s", i, want, sub)
		}
	}
	// Newest `created` first, ties by link: card 04 (09-05) leads, card 00 (09-01) trails card 14.
	if a, b := strings.Index(sub, "card-04|"), strings.Index(sub, "card-00|"); a < 0 || b < 0 || a > b {
		t.Errorf("the folder map is not newest first:\n%s", sub)
	}
	res := plannedText(plan, "../resources/moc-resources.md")
	if !strings.Contains(res, "- [[resources/topics/sqlite/moc-sqlite|sqlite/]] — 15 notes") {
		t.Errorf("the space map does not link the folder's map:\n%s", res)
	}
	if strings.Contains(res, "SQLite card") {
		t.Errorf("the space map listed the counted folder title by title:\n%s", res)
	}
}

// A folder at or under the limit is listed in the space map and gets no map of
// its own.
func TestSpaceFolderAtTheLimitGetsNoMap(t *testing.T) {
	root, vault := spacesFixture(t)
	for i := 0; i < spaceMapListLimit; i++ {
		writeAt(t, vault, fmt.Sprintf("resources/topics/twelve/card-%02d.md", i),
			fmt.Sprintf("---\ntitle: Twelve %02d\ncreated: 2026-09-01\n---\n\nBody.\n", i))
	}
	plan, _ := PlanSpaceMaps(root, projectsNow)
	for _, p := range plan.Pages {
		if strings.Contains(p.Rel, "moc-twelve") || strings.Contains(p.Rel, "moc-go-git") || strings.Contains(p.Rel, "moc-topics") ||
			strings.Contains(p.Rel, "moc-university") || strings.Contains(p.Rel, "moc-homelab") {
			t.Errorf("a folder within the limit got a map: %s", p.Rel)
		}
	}
	res := plannedText(plan, "../resources/moc-resources.md")
	for _, want := range []string{"- `twelve/`", "  - [[resources/topics/twelve/card-11|Twelve 11]]", "- `go-git/`"} {
		if !strings.Contains(res, want) {
			t.Errorf("the space map lacks %q:\n%s", want, res)
		}
	}
}

// The rule recurses: inside a folder's map a subfolder past the limit is
// counted and linked to a map of its own, which links back up to the folder's
// map, not the space's. A section folder whose own notes are counted gets a map
// too, which also lists its small subfolders whole.
func TestSpaceFolderMapsRecurse(t *testing.T) {
	root, vault := spacesFixture(t)
	for i := 0; i < 3; i++ {
		writeAt(t, vault, fmt.Sprintf("resources/topics/big/top-%d.md", i),
			fmt.Sprintf("---\ntitle: Big %d\ncreated: 2026-09-1%d\n---\n\nBody.\n", i, i))
	}
	for i := 0; i < spaceMapListLimit+1; i++ {
		writeAt(t, vault, fmt.Sprintf("resources/topics/big/deep/card-%02d.md", i),
			fmt.Sprintf("---\ntitle: Deep %02d\ncreated: 2026-09-02\n---\n\nBody.\n", i))
	}
	writeAt(t, vault, "resources/topics/big/small/only.md", "---\ntitle: Small only\ncreated: 2026-09-03\n---\n\nBody.\n")
	for i := 0; i < spaceMapListLimit+1; i++ {
		writeAt(t, vault, fmt.Sprintf("resources/briefs/brief-%02d.md", i),
			fmt.Sprintf("---\ntitle: Brief %02d\ncreated: 2026-10-%02d\n---\n\nBody.\n", i, 1+i))
	}
	writeAt(t, vault, "resources/briefs/old/one.md", "---\ntitle: Old brief\ncreated: 2026-08-01\n---\n\nBody.\n")
	plan, _ := PlanSpaceMaps(root, projectsNow)

	big := plannedText(plan, "../resources/topics/big/moc-big.md")
	for _, want := range []string{
		"members: 17", "[[resources/moc-resources|resources]]",
		"- [[resources/topics/big/top-2|Big 2]] · 2026-09-12",
		"- [[resources/topics/big/deep/moc-deep|deep/]] — 13 notes",
		"- `small/`", "  - [[resources/topics/big/small/only|Small only]] · 2026-09-03",
	} {
		if !strings.Contains(big, want) {
			t.Errorf("the big/ map lacks %q:\n%s", want, big)
		}
	}
	if strings.Contains(big, "Deep 0") {
		t.Errorf("the big/ map listed its counted subfolder:\n%s", big)
	}
	deep := plannedText(plan, "../resources/topics/big/deep/moc-deep.md")
	for _, want := range []string{"# topics/big/deep/", "[[resources/topics/big/moc-big|topics/big/]]", "members: 13",
		"- [[resources/topics/big/deep/card-12|Deep 12]] · 2026-09-02"} {
		if !strings.Contains(deep, want) {
			t.Errorf("the deep/ map lacks %q:\n%s", want, deep)
		}
	}
	res := plannedText(plan, "../resources/moc-resources.md")
	for _, want := range []string{
		"- [[resources/topics/big/moc-big|big/]] — 17 notes",
		"## briefs", "- [[resources/briefs/moc-briefs|briefs/]] — 13 notes", "- `old/`",
	} {
		if !strings.Contains(res, want) {
			t.Errorf("the space map lacks %q:\n%s", want, res)
		}
	}
	briefs := plannedText(plan, "../resources/briefs/moc-briefs.md")
	for _, want := range []string{"members: 14", "- [[resources/briefs/brief-12|Brief 12]] · 2026-10-13",
		"- `old/`", "  - [[resources/briefs/old/one|Old brief]] · 2026-08-01"} {
		if !strings.Contains(briefs, want) {
			t.Errorf("the briefs/ map lacks %q:\n%s", want, briefs)
		}
	}

	// Written out, every map regenerates byte for byte, and no map counts itself.
	for _, in := range plan.Intents {
		writeAt(t, root, in.Rel, string(in.After))
	}
	again, _ := PlanSpaceMaps(root, projectsNow.AddDate(0, 0, 3))
	if len(again.Intents) != 0 {
		t.Errorf("unchanged folders rewrote their maps: %+v", again.Intents)
	}
	if len(again.Pages) != len(plan.Pages) {
		t.Errorf("pages changed on regeneration: %+v vs %+v", plan.Pages, again.Pages)
	}
	for _, p := range again.Pages {
		if strings.HasSuffix(p.Rel, "moc-deep.md") && p.Members != 13 {
			t.Errorf("the deep/ map counted itself: %+v", p)
		}
	}
	// The re-render is the same bytes as the file on disk, not merely the same
	// but for `updated`.
	for _, in := range plan.Intents {
		disk, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(in.Rel)))
		if err != nil || string(disk) != string(in.After) {
			t.Errorf("%s on disk differs from its plan: %v", in.Rel, err)
		}
	}
}
