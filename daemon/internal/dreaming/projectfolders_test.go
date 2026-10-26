package dreaming

import (
	"fmt"
	"strings"
	"testing"
)

// A project's working folders on its map (task 193 step 4, LD-2): research/,
// drafts/ and desk/ listed note by note up to spaceMapListLimit, a larger group
// given its own map beside its notes, and any other folder only when
// project.yaml names it under map_folders:.

func folderProjectFixture(t *testing.T) (root, vault string) {
	t.Helper()
	root, vault = rootMapVault(t)
	p := "projects/demo/"
	writeAt(t, vault, p+"charter.md", "---\ntitle: Demo\ncreated: 2026-08-01\n---\n\n**What:** A demo.\n")
	for i := 0; i < 3; i++ {
		writeAt(t, vault, fmt.Sprintf(p+"research/small/n%d.md", i), fmt.Sprintf("---\ntitle: Small %d\ncreated: 2026-09-0%d\n---\n", i, i+1))
	}
	for i := 0; i < 20; i++ {
		writeAt(t, vault, fmt.Sprintf(p+"research/big/n%02d.md", i), fmt.Sprintf("---\ntitle: Big %02d\ncreated: 2026-08-%02d\n---\n", i, i+1))
	}
	writeAt(t, vault, p+"research/big/_scratch/hidden.md", "# Hidden\n")
	writeAt(t, vault, p+"desk/brief.md", "# Brief\n")
	writeAt(t, vault, p+"desk/handoff/step-1.md", "---\ntitle: Step 1\ncreated: 2026-09-20\n---\n")
	writeAt(t, vault, p+"drafts/post.md", "---\ntitle: A post\ncreated: 2026-09-15\nupdated: 2026-10-01\n---\n")
	writeAt(t, vault, p+"drafts/moc-old.md", "---\nkind: moc\n---\n")
	writeAt(t, vault, p+"analysis/chart.md", "# Chart\n")
	writeAt(t, vault, p+"_private/x.md", "# X\n")
	return root, vault
}

func TestProjectMapListsWorkingFoldersAndMapsTheLargeOnes(t *testing.T) {
	root, _ := folderProjectFixture(t)
	plan, err := PlanProjectMaps(root, projectsNow)
	if err != nil {
		t.Fatal(err)
	}
	demo := plannedText(plan, "../projects/demo/moc-demo.md")
	mustContain(t, "moc-demo", demo,
		"members: 26\n",
		// the newest working note's created; the draft's `updated` is not an input
		"updated: 2026-09-20\n",
		"## Research\n\n"+
			"- [[projects/demo/research/big/moc-big|big/]] — 20 notes\n"+
			"- `small/`\n"+
			"  - [[projects/demo/research/small/n2|Small 2]] · 2026-09-03\n"+
			"  - [[projects/demo/research/small/n1|Small 1]] · 2026-09-02\n"+
			"  - [[projects/demo/research/small/n0|Small 0]] · 2026-09-01\n",
		"## Drafts\n\n- [[projects/demo/drafts/post|A post]] · 2026-09-15\n",
		"## Desk\n\n- [[projects/demo/desk/brief|Brief]]\n- `handoff/`\n  - [[projects/demo/desk/handoff/step-1|Step 1]] · 2026-09-20\n")
	for _, absent := range []string{"Big 00", "analysis", "Hidden", "_private", "moc-old", "2026-10-01"} {
		if strings.Contains(demo, absent) {
			t.Errorf("moc-demo lists %q:\n%s", absent, demo)
		}
	}

	big := plannedText(plan, "../projects/demo/research/big/moc-big.md")
	mustContain(t, "moc-big", big,
		"kind: moc\n", "slug: moc-big\n", "members: 20\n", "created: 2026-08-20\n", "updated: 2026-08-20\n",
		"# research/big/\n\n[[projects/demo/moc-demo|demo]]\n",
		"- [[projects/demo/research/big/n19|Big 19]] · 2026-08-20\n- [[projects/demo/research/big/n18|Big 18]] · 2026-08-19\n",
		"- [[projects/demo/research/big/n00|Big 00]] · 2026-08-01\n")
	if strings.Contains(big, "Hidden") {
		t.Errorf("the bundle map lists a `_`-prefixed folder's note:\n%s", big)
	}
	for _, rel := range plannedRels(plan) {
		if strings.Contains(rel, "small/moc-") || strings.Contains(rel, "desk/moc-") || strings.Contains(rel, "drafts/moc-") {
			t.Errorf("a small group got its own map: %s", rel)
		}
	}
}

func TestProjectMapFoldersMapsANamedFolder(t *testing.T) {
	root, vault := folderProjectFixture(t)
	writeAt(t, vault, "projects/demo/project.yaml",
		"slug: demo\ntitle: Demo\nstatus: active\nrepositories: []\ncode_paths: []\nmap_folders:\n  - analysis\n  - decisions\n  - ../escape\n  - _private\n")
	plan, _ := PlanProjectMaps(root, projectsNow)
	demo := plannedText(plan, "../projects/demo/moc-demo.md")
	mustContain(t, "moc-demo", demo, "## Desk\n", "\n\n## analysis/\n\n- [[projects/demo/analysis/chart|Chart]]\n", "members: 27\n")
	for _, absent := range []string{"## decisions/", "escape", "_private"} {
		if strings.Contains(demo, absent) {
			t.Errorf("map_folders let %q through:\n%s", absent, demo)
		}
	}
}

func TestProjectAndFolderMapsAreByteIdenticalOnUnchangedInput(t *testing.T) {
	root, _ := folderProjectFixture(t)
	plan, _ := PlanProjectMaps(root, projectsNow)
	for _, in := range plan.Intents {
		writeAt(t, root, in.Rel, string(in.After))
	}
	again, err := PlanProjectMaps(root, projectsNow.AddDate(0, 0, 3))
	if err != nil {
		t.Fatal(err)
	}
	if len(again.Intents) != 0 {
		t.Errorf("an unchanged project rewrote %d pages, first %s:\n%s", len(again.Intents), again.Intents[0].Rel, again.Intents[0].After)
	}
	// The written folder map is not a member of its own folder.
	if text := plannedText(plan, "../projects/demo/research/big/moc-big.md"); !strings.Contains(text, "members: 20\n") {
		t.Errorf("the folder map counted itself:\n%s", text)
	}
	for _, p := range again.Pages {
		if strings.HasSuffix(p.Rel, "moc-big.md") && p.Members != 20 {
			t.Errorf("after writing, the folder map counts %d members", p.Members)
		}
	}
}

func TestALargeLooseGroupGetsItsOwnMap(t *testing.T) {
	root, vault := rootMapVault(t)
	writeAt(t, vault, "projects/demo/charter.md", "---\ntitle: Demo\n---\n\n**What:** A demo.\n")
	for i := 0; i < spaceMapListLimit+1; i++ {
		writeAt(t, vault, fmt.Sprintf("projects/demo/research/loose-%02d.md", i), fmt.Sprintf("# Loose %02d\n", i))
	}
	plan, _ := PlanProjectMaps(root, projectsNow)
	mustContain(t, "moc-demo", plannedText(plan, "../projects/demo/moc-demo.md"),
		"## Research\n\n- [[projects/demo/research/moc-research|research/]] — 13 notes\n")
	mustContain(t, "moc-research", plannedText(plan, "../projects/demo/research/moc-research.md"),
		"# research/\n", "members: 13\n", "- [[projects/demo/research/loose-00|Loose 00]]\n")
}
