package restated

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const sacred = "---\ntitle: Tests are sacred\ntype: convention\nsummary: \"Tests check real behaviour.\"\n" +
	"importance: 9\nstatus: active\nlifecycle: active\ncreated: 2026-05-19\nslug: tests-are-sacred\n---\n\n" +
	"Tests check real behaviour. Don't dumb them down.\n"

const neverEdit = "---\ntitle: Never Edit or Delete a Failing Test to Make It Pass\ntype: convention\n" +
	"summary: \"A failing test is information: fix the code, never the test.\"\nimportance: 7\n" +
	"why: \"kept after a weakened assertion slipped through\"\nstatus: active\nlifecycle: active\n" +
	"created: 2026-08-21\nslug: never-edit-or-delete-a-failing-test\n---\n\n" +
	"A failing test is information. Read it, then fix the implementation.\n"

func writeNote(t *testing.T, dir, name, text string) {
	t.Helper()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, name), []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestGatherReadsActiveRuleCardsOnly(t *testing.T) {
	vault := t.TempDir()
	sem := filepath.Join(vault, "agent", "memory", "semantic")
	writeNote(t, sem, "tests-are-sacred.md", sacred)
	writeNote(t, sem, "never-edit-or-delete-a-failing-test.md", neverEdit)
	writeNote(t, sem, "a-fact.md", "---\ntype: reference\n---\nA fact.\n")
	writeNote(t, sem, "old-rule.md", "---\ntype: convention\nlifecycle: superseded\n---\nOld.\n")
	writeNote(t, sem, "a-lesson.md", "---\nkind: crystallized\ntype: convention\n---\nA record.\n")
	writeNote(t, sem, "_index.md", "---\ntype: convention\n---\nAn index.\n")
	writeNote(t, filepath.Join(vault, "agent", "memory", "procedural"), "run-the-battery.md",
		"---\ntype: workflow\ncreated: 2026-09-01\n---\nRun the battery.\n")
	notes, err := Gather(vault, "agent")
	if err != nil {
		t.Fatal(err)
	}
	var rels []string
	for _, n := range notes {
		rels = append(rels, n.Rel)
	}
	want := "agent/memory/procedural/run-the-battery.md agent/memory/semantic/never-edit-or-delete-a-failing-test.md agent/memory/semantic/tests-are-sacred.md"
	if strings.Join(rels, " ") != want {
		t.Errorf("gathered %v", rels)
	}
	if notes[2].MemoryRel != "memory/semantic/tests-are-sacred.md" || notes[2].Importance != "9" {
		t.Errorf("note read as %+v", notes[2])
	}
}

func TestShortlistScoresByTheBestChunkPairAboveTheLine(t *testing.T) {
	a, b, c := Note{Rel: "a"}, Note{Rel: "b"}, Note{Rel: "c"}
	vecs := map[string][][]float32{
		"a": {{1, 0, 0}, {0, 1, 0}},
		"b": {{0, 0.8, 0.6}},
		"c": {{0, 0, 1}},
	}
	got := Shortlist([]Note{a, b, c}, vecs, 0.5)
	// a~b is 0.8 through a's second chunk; b~c is 0.6; a~c is 0.
	if len(got) != 2 || got[0].A.Rel != "a" || got[0].B.Rel != "b" || got[1].A.Rel != "b" || got[1].B.Rel != "c" {
		t.Fatalf("shortlist %+v", got)
	}
	if d := got[0].Similarity - 0.8; d > 1e-6 || d < -1e-6 {
		t.Errorf("a~b = %v, want 0.8 from a's second chunk", got[0].Similarity)
	}
	if len(Shortlist([]Note{a, b, c}, vecs, 0.9)) != 0 {
		t.Error("pairs under the line were shortlisted")
	}
}

func TestParseVerdictReadsTheJudgesObject(t *testing.T) {
	v, err := ParseVerdict("```json\n{\"verdict\": \"Same\", \"reason\": \"both forbid editing a failing test\"}\n```")
	if err != nil || !v.Same() || v.Reason == "" {
		t.Errorf("verdict %+v, err %v", v, err)
	}
	for _, bad := range []string{"no json here", `{"verdict": "maybe"}`, `{"verdict": `} {
		if _, err := ParseVerdict(bad); err == nil {
			t.Errorf("%q parsed", bad)
		}
	}
}

func TestAMergeKeepsTheOlderNoteWithTheNewerWording(t *testing.T) {
	vault := t.TempDir()
	sem := filepath.Join(vault, "agent", "memory", "semantic")
	writeNote(t, sem, "tests-are-sacred.md", sacred)
	writeNote(t, sem, "never-edit-or-delete-a-failing-test.md", neverEdit)
	notes, _ := Gather(vault, "agent")
	older, newer := Order(Pair{A: notes[0], B: notes[1]})
	if older.Title != "Tests are sacred" {
		t.Fatalf("older is %q", older.Title)
	}
	survivor, superseded, err := Merge(older, newer, "2026-09-29")
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"title: Tests are sacred\n", "slug: tests-are-sacred\n", "importance: 9\n",
		"summary: \"A failing test is information: fix the code, never the test.\"\n",
		"why: \"kept after a weakened assertion slipped through\"\n",
		"supersedes: memory/semantic/never-edit-or-delete-a-failing-test.md\n", "updated: 2026-09-29\n",
		"A failing test is information. Read it, then fix the implementation.\n"} {
		if !strings.Contains(survivor, want) {
			t.Errorf("survivor lacks %q:\n%s", want, survivor)
		}
	}
	if strings.Contains(survivor, "Don't dumb them down") || strings.Contains(survivor, "importance: 7") {
		t.Errorf("survivor kept the older wording or took the newer importance:\n%s", survivor)
	}
	for _, want := range []string{"lifecycle: superseded\n", "superseded_by: memory/semantic/tests-are-sacred.md\n",
		"lifecycle_since: 2026-09-29\n", "status: active\n", "Read it, then fix the implementation."} {
		if !strings.Contains(superseded, want) {
			t.Errorf("superseded lacks %q:\n%s", want, superseded)
		}
	}
}
