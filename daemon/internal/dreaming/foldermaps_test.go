package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A folder map, once the night has written it, is kept true while it stays on
// disk (foldermaps.go): regenerated whatever its folder holds, listing a
// project folder's moved notes from `completed/`, and rewritten as an emptied
// page once there is nothing to list. The case that found it: on 2026-11-21
// `projects/agentm/desk/briefs/moc-briefs.md` still listed 13 briefs at their
// old paths after they moved to `completed/desk/briefs/`, because the map was
// planned only while the folder held more than twelve notes.

// writePlan writes a plan's pages to disk, as the night's apply would.
func writePlan(t *testing.T, root string, plan MocsPlan) {
	t.Helper()
	for _, in := range plan.Intents {
		writeAt(t, root, in.Rel, string(in.After))
	}
}

// moveNote moves a note on disk, as the lifecycle move would.
func moveNote(t *testing.T, vault, from, to string) {
	t.Helper()
	dst := filepath.Join(vault, filepath.FromSlash(to))
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(filepath.Join(vault, filepath.FromSlash(from)), dst); err != nil {
		t.Fatal(err)
	}
}

func briefsFixture(t *testing.T) (root, vault string) {
	t.Helper()
	root, vault = rootMapVault(t)
	p := "projects/demo/"
	writeAt(t, vault, p+"charter.md", "---\ntitle: Demo\ncreated: 2026-08-01\n---\n\n**What:** A demo.\n")
	writeAt(t, vault, p+"desk/brief.md", "---\ntitle: Desk brief\ncreated: 2026-08-02\n---\n")
	writeAt(t, vault, p+"desk/briefs/README.md", "---\ntitle: Briefs README\ncreated: 2026-08-03\n---\n")
	for i := 0; i < spaceMapListLimit+1; i++ {
		writeAt(t, vault, fmt.Sprintf(p+"desk/briefs/b%02d.md", i),
			fmt.Sprintf("---\ntitle: Brief %02d\ncreated: 2026-09-%02d\n---\n", i, i+1))
	}
	return root, vault
}

// The briefs case: the folder's notes move to `completed/`, the folder drops
// to one note, and its map is kept, now listing the one note and, below it,
// the thirteen that moved, at their new paths.
func TestAFolderMapFollowsItsNotesToCompleted(t *testing.T) {
	root, vault := briefsFixture(t)
	first, _ := PlanProjectMaps(root, projectsNow)
	if plannedText(first, "../projects/demo/desk/briefs/moc-briefs.md") == "" {
		t.Fatalf("a folder of 14 notes got no map: %v", plannedRels(first))
	}
	writePlan(t, root, first)
	for i := 0; i < spaceMapListLimit+1; i++ {
		n := fmt.Sprintf("desk/briefs/b%02d.md", i)
		moveNote(t, vault, "projects/demo/"+n, "projects/demo/completed/"+n)
	}

	plan, _ := PlanProjectMaps(root, projectsNow)
	briefs := plannedText(plan, "../projects/demo/desk/briefs/moc-briefs.md")
	mustContain(t, "moc-briefs", briefs,
		"status: active\n", "members: 14\n", "updated: 2026-09-13\n",
		"The 1 note in `desk/briefs/` of project demo, newest first. Below, the 13 that moved to `completed/desk/briefs/` when their work finished.",
		"- [[projects/demo/desk/briefs/README|Briefs README]] · 2026-08-03\n\n## Completed\n\n"+
			"- [[projects/demo/completed/desk/briefs/b12|Brief 12]] · 2026-09-13\n",
		"- [[projects/demo/completed/desk/briefs/b00|Brief 00]] · 2026-09-01\n")
	if strings.Contains(briefs, "[[projects/demo/desk/briefs/b") {
		t.Errorf("the kept map still links a brief where it was:\n%s", briefs)
	}
	demo := plannedText(plan, "../projects/demo/moc-demo.md")
	mustContain(t, "moc-demo", demo,
		"## Desk\n\n- [[projects/demo/desk/brief|Desk brief]] · 2026-08-02\n"+
			"- [[projects/demo/desk/briefs/moc-briefs|briefs/]] — 1 note · 13 completed\n")

	// Written out, nothing changes on the next night.
	writePlan(t, root, plan)
	again, _ := PlanProjectMaps(root, projectsNow.AddDate(0, 0, 2))
	if len(again.Intents) != 0 {
		t.Errorf("an unchanged kept map rewrote: %v", plannedRels(again))
	}
}

// A folder that shrinks under the limit keeps its map, current, and the
// project map keeps linking it rather than flipping back to a listing.
func TestAFolderMapIsKeptWhenItsFolderShrinks(t *testing.T) {
	root, vault := briefsFixture(t)
	first, _ := PlanProjectMaps(root, projectsNow)
	writePlan(t, root, first)
	for i := 3; i < spaceMapListLimit+1; i++ {
		if err := os.Remove(filepath.Join(vault, fmt.Sprintf("projects/demo/desk/briefs/b%02d.md", i))); err != nil {
			t.Fatal(err)
		}
	}
	plan, _ := PlanProjectMaps(root, projectsNow)
	briefs := plannedText(plan, "../projects/demo/desk/briefs/moc-briefs.md")
	mustContain(t, "moc-briefs", briefs, "members: 4\n", "- [[projects/demo/desk/briefs/b02|Brief 02]] · 2026-09-03\n")
	if strings.Contains(briefs, "b03") || strings.Contains(briefs, "## Completed") {
		t.Errorf("the kept map lists a note that is gone:\n%s", briefs)
	}
	mustContain(t, "moc-demo", plannedText(plan, "../projects/demo/moc-demo.md"),
		"- [[projects/demo/desk/briefs/moc-briefs|briefs/]] — 4 notes\n")
}

// A folder left with no note, here or in `completed/`, gets an emptied page in
// place of its map: no links, `status: archived`, `members: 0`, the page's own
// dates kept. The project map no longer lists the folder. Nothing is deleted,
// and a second night writes nothing.
func TestAnEmptiedFolderMapIsRewrittenNotDeleted(t *testing.T) {
	root, vault := briefsFixture(t)
	first, _ := PlanProjectMaps(root, projectsNow)
	writePlan(t, root, first)
	for _, n := range []string{"README"} {
		if err := os.Remove(filepath.Join(vault, "projects/demo/desk/briefs/"+n+".md")); err != nil {
			t.Fatal(err)
		}
	}
	for i := 0; i < spaceMapListLimit+1; i++ {
		if err := os.Remove(filepath.Join(vault, fmt.Sprintf("projects/demo/desk/briefs/b%02d.md", i))); err != nil {
			t.Fatal(err)
		}
	}
	plan, _ := PlanProjectMaps(root, projectsNow)
	briefs := plannedText(plan, "../projects/demo/desk/briefs/moc-briefs.md")
	mustContain(t, "moc-briefs", briefs,
		"title: demo desk/briefs/ — map of content\n", "status: archived\n", "members: 0\n",
		"created: 2026-09-13\n", "updated: 2026-09-13\n", "slug: moc-briefs\n", "generated_by: agentmdream\n",
		"# desk/briefs/\n\n[[projects/demo/moc-demo|demo]]\n",
		"`desk/briefs/` of project demo holds no notes now, and none of it is in `completed/`, so this map lists none.")
	if strings.Contains(briefs, "[[projects/demo/desk/briefs/") {
		t.Errorf("the emptied page still links a note:\n%s", briefs)
	}
	if demo := plannedText(plan, "../projects/demo/moc-demo.md"); strings.Contains(demo, "briefs/") {
		t.Errorf("the project map lists an emptied folder:\n%s", demo)
	}
	writePlan(t, root, plan)
	if _, err := os.Stat(filepath.Join(vault, "projects/demo/desk/briefs/moc-briefs.md")); err != nil {
		t.Fatalf("the emptied map is gone: %v", err)
	}
	again, _ := PlanProjectMaps(root, projectsNow.AddDate(0, 0, 2))
	if len(again.Intents) != 0 {
		t.Errorf("an unchanged emptied page rewrote: %v", plannedRels(again))
	}
}

// A page of the same name the night did not write is not kept: a small folder
// with a hand `moc-*.md` gets no map, and the page is not rewritten.
func TestAHandPageIsNotKeptAsAFolderMap(t *testing.T) {
	root, vault := briefsFixture(t)
	for i := 0; i < spaceMapListLimit+1; i++ {
		if err := os.Remove(filepath.Join(vault, fmt.Sprintf("projects/demo/desk/briefs/b%02d.md", i))); err != nil {
			t.Fatal(err)
		}
	}
	writeAt(t, vault, "projects/demo/desk/briefs/moc-briefs.md", "---\ntitle: My briefs\nkind: moc\n---\n\nMine.\n")
	plan, _ := PlanProjectMaps(root, projectsNow)
	for _, rel := range plannedRels(plan) {
		if strings.Contains(rel, "moc-briefs") {
			t.Errorf("a hand page was taken for a kept map: %s", rel)
		}
	}
	mustContain(t, "moc-demo", plannedText(plan, "../projects/demo/moc-demo.md"),
		"- `briefs/`\n  - [[projects/demo/desk/briefs/README|Briefs README]] · 2026-08-03\n")
}

// The shared spaces keep their folder maps the same way: a counted folder that
// shrinks keeps its map and its link on the space map, and one that empties
// gets an emptied page and leaves the space map.
func TestASpaceFolderMapIsKeptThenEmptied(t *testing.T) {
	root, vault := spacesFixture(t)
	first, _ := PlanSpaceMaps(root, projectsNow)
	writePlan(t, root, first)
	for i := 3; i < spaceMapListLimit+3; i++ {
		if err := os.Remove(filepath.Join(vault, fmt.Sprintf("resources/topics/sqlite/card-%02d.md", i))); err != nil {
			t.Fatal(err)
		}
	}
	plan, _ := PlanSpaceMaps(root, projectsNow)
	sub := plannedText(plan, "../resources/topics/sqlite/moc-sqlite.md")
	mustContain(t, "moc-sqlite", sub, "status: active\n", "members: 3\n",
		"- [[resources/topics/sqlite/card-02|SQLite card 02]] · 2026-09-03\n")
	if strings.Contains(sub, "card-03") {
		t.Errorf("the kept map lists a card that is gone:\n%s", sub)
	}
	res := plannedText(plan, "../resources/moc-resources.md")
	mustContain(t, "moc-resources", res, "- [[resources/topics/sqlite/moc-sqlite|sqlite/]] — 3 notes\n")
	if strings.Contains(res, "SQLite card") {
		t.Errorf("the space map listed a folder that keeps a map:\n%s", res)
	}
	writePlan(t, root, plan)

	for i := 0; i < 3; i++ {
		if err := os.Remove(filepath.Join(vault, fmt.Sprintf("resources/topics/sqlite/card-%02d.md", i))); err != nil {
			t.Fatal(err)
		}
	}
	plan, _ = PlanSpaceMaps(root, projectsNow)
	sub = plannedText(plan, "../resources/topics/sqlite/moc-sqlite.md")
	mustContain(t, "moc-sqlite", sub, "status: archived\n", "members: 0\n", "# topics/sqlite/\n\n[[resources/moc-resources|resources]]\n",
		"`topics/sqlite/` of the resources space holds no notes now, so this map lists none.")
	if strings.Contains(sub, "card-") {
		t.Errorf("the emptied page still links a card:\n%s", sub)
	}
	if res := plannedText(plan, "../resources/moc-resources.md"); strings.Contains(res, "sqlite") {
		t.Errorf("the space map lists an emptied folder:\n%s", res)
	}
	writePlan(t, root, plan)
	again, _ := PlanSpaceMaps(root, projectsNow.AddDate(0, 0, 2))
	if len(again.Intents) != 0 {
		t.Errorf("an unchanged emptied space map rewrote: %+v", again.Intents)
	}
}
