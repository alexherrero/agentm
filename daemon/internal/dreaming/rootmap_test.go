package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// rootMapVault is the shipped nested layout: the memory root `Agent/` inside a
// vault Obsidian opens, with every directory an area map lives in.
func rootMapVault(t *testing.T) (root, vault string) {
	t.Helper()
	vault = t.TempDir()
	root = filepath.Join(vault, "Agent")
	for _, d := range []string{".obsidian", "Agent/memory/mocs", "Agent/diagnostics", "Calendar/2026", "standards", "Projects"} {
		if err := os.MkdirAll(filepath.Join(vault, d), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	return root, vault
}

func writeAt(t *testing.T, base, rel, text string) {
	t.Helper()
	p := filepath.Join(base, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

func rootMapText(plan MocsPlan) string {
	for _, in := range plan.Intents {
		if in.Rel == MocRel(MocRootSlug) {
			return string(in.After)
		}
	}
	return ""
}

func TestRootMapListsEveryAreaMapAndNothingElse(t *testing.T) {
	root, vault := rootMapVault(t)
	now := time.Date(2026, 9, 13, 9, 0, 0, 0, time.UTC)
	for _, rel := range []string{"Agent/memory/mocs/moc-memory.md", "Agent/memory/mocs/needs-review.md",
		"Agent/diagnostics/moc-diagnostics.md", "Calendar/moc-calendar-2025.md", "Calendar/moc-calendar-2026.md",
		"standards/moc-standards.md", "Projects/moc-projects.md"} {
		writeAt(t, vault, rel, "---\nkind: moc\n---\n\n# a map\n")
	}
	for _, rel := range []string{"Agent/diagnostics/notes.md", "standards/storage-rules.md",
		"Calendar/2026/2026-08-10-diary.md", "Projects/index.md", "Agent/memory/mocs/workflow.md"} {
		writeAt(t, vault, rel, "---\ntitle: not an area map\n---\n")
	}
	plan, err := PlanRootMap(root, nil, now)
	if err != nil {
		t.Fatal(err)
	}
	text := rootMapText(plan)
	want := "# root\n\nThe entry point: every area's map, generated nightly. Not edited by hand.\n\n" +
		"## Memory\n\n- [[moc-memory]]\n- [[needs-review]]\n\n" +
		"## Calendar\n\n- [[moc-calendar-2026]]\n- [[moc-calendar-2025]]\n\n" +
		"## Diagnostics\n\n- [[moc-diagnostics]]\n\n" +
		"## Standards\n\n- [[moc-standards]]\n\n" +
		"## Projects\n\n- [[moc-projects]]\n"
	if !strings.HasSuffix(text, want) {
		t.Errorf("root map:\n%s\nwant it to end with:\n%s", text, want)
	}
	for _, line := range []string{"slug: moc-root\n", "kind: moc\n", "created: 2026-09-13\n", "updated: 2026-09-13\n", "generated_by: agentmdream\n"} {
		if !strings.Contains(text, line) {
			t.Errorf("root map frontmatter lacks %q:\n%s", line, text)
		}
	}
	for _, unwanted := range []string{"[[notes]]", "[[storage-rules]]", "[[2026-08-10-diary]]", "[[index]]", "[[workflow]]", "[[Home]]", "group:"} {
		if strings.Contains(text, unwanted) {
			t.Errorf("root map carries %q:\n%s", unwanted, text)
		}
	}
}

func TestRootMapRewritesOnlyWhenTheListChanges(t *testing.T) {
	root, vault := rootMapVault(t)
	day := time.Date(2026, 9, 13, 9, 0, 0, 0, time.UTC)
	writeAt(t, vault, "Agent/memory/mocs/moc-memory.md", "---\nkind: moc\n---\n")
	first, _ := PlanRootMap(root, nil, day)
	if len(first.Intents) != 1 {
		t.Fatalf("the first plan writes the root map, got %d intent(s)", len(first.Intents))
	}
	j, _ := OpenJournal(t.TempDir())
	if _, err := j.Commit(root, "r", "r-1", first.Intents[0], day); err != nil {
		t.Fatal(err)
	}
	if again, _ := PlanRootMap(root, nil, day.AddDate(0, 0, 3)); len(again.Intents) != 0 {
		t.Errorf("an unchanged list three days later writes nothing, got %d intent(s)", len(again.Intents))
	}
	writeAt(t, vault, "Projects/moc-tasks.md", "---\nkind: moc\n---\n")
	changed, _ := PlanRootMap(root, nil, day.AddDate(0, 0, 5))
	text := rootMapText(changed)
	for _, want := range []string{"- [[moc-tasks]]\n", "updated: 2026-09-18\n", "created: 2026-09-13\n"} {
		if !strings.Contains(text, want) {
			t.Errorf("a new area map rewrites the page, stamps the day it changed and keeps created — lacks %q:\n%s", want, text)
		}
	}
}

func TestRootMapCountsAMapThisPassPlans(t *testing.T) {
	root, _ := rootMapVault(t)
	plan, _ := PlanRootMap(root, []string{MocRel(MocMemorySlug), "../Calendar/moc-calendar-2026.md"},
		time.Date(2026, 9, 13, 9, 0, 0, 0, time.UTC))
	text := rootMapText(plan)
	for _, want := range []string{"- [[moc-memory]]\n", "- [[moc-calendar-2026]]\n"} {
		if !strings.Contains(text, want) {
			t.Errorf("a map this pass plans is on the root map before it is on disk — lacks %q:\n%s", want, text)
		}
	}
}

func TestNoRootMapWithoutAnAreaMap(t *testing.T) {
	root, _ := rootMapVault(t)
	plan, _ := PlanRootMap(root, nil, time.Date(2026, 9, 13, 9, 0, 0, 0, time.UTC))
	if len(plan.Intents) != 0 || len(plan.Pages) != 0 {
		t.Errorf("no area map, no root map: %s", fmt.Sprint(plan.Pages))
	}
}
