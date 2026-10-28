package dreaming

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// The night's own moves repair the links into what they move (task 193 LD-7),
// as the task mover does: a link that names an archived or re-filed note by
// its path follows it, in a journaled edit of the same pass.

const coldLinks = "See [[agent/memory/semantic/cold|the cold note]], [[agent/memory/semantic/cold]] " +
	"and [the file](/agent/memory/semantic/cold.md).\n"

func TestAnArchiveMoveRewritesThePathLinksIntoTheNote(t *testing.T) {
	root, vault := rootMapVault(t)
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)
	cold := writeNote(t, root, "memory/semantic/cold.md", "dormant", 2000, now, "")
	// A note the job sinks tonight that also links the cold one: one intent,
	// carrying both changes.
	old := writeNote(t, root, "memory/semantic/old.md", "active", 400, now, "related: \"[[agent/memory/semantic/cold|cold]]\"\n")
	writeAt(t, vault, "projects/x/notes.md", "---\ntitle: notes\nrelated: \"[[agent/memory/semantic/cold|cold]]\"\n---\n\n"+coldLinks)
	writeAt(t, vault, "projects/x/by-name.md", "Just [[cold]].\n")
	writeAt(t, vault, "personal/diary.md", coldLinks)
	writeAt(t, vault, "standards/rules.md", coldLinks)

	plan, err := PlanLifecycle(root, "", axisContract(t), now, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Archived) != 1 || plan.Archived[0].Rel != cold {
		t.Fatalf("archived %+v, want the cold note", plan.Archived)
	}
	byRel := map[string][]Intent{}
	for _, in := range plan.Intents {
		byRel[in.Rel] = append(byRel[in.Rel], in)
	}
	for rel, ins := range byRel {
		if len(ins) != 1 {
			t.Errorf("%s carries %d intents; a note gets one a pass", rel, len(ins))
		}
	}
	if _, ok := byRel["../personal/diary.md"]; ok {
		t.Error("a personal note was edited")
	}
	if _, ok := byRel["../standards/rules.md"]; ok {
		t.Error("a standards note was edited")
	}
	if _, ok := byRel["../projects/x/by-name.md"]; ok {
		t.Error("a basename link, which still resolves, was rewritten")
	}
	edit, ok := byRel["../projects/x/notes.md"]
	if !ok {
		t.Fatalf("the project note's links were not repaired; intents %+v", plan.Intents)
	}
	if edit[0].Job != JobLifecycle || edit[0].Meta != nil {
		t.Errorf("the edit is the lifecycle job's and records no transition: %+v", edit[0])
	}
	if len(plan.LinkEdits) != 2 || plan.Links != 5 {
		t.Errorf("link edits %+v, %d links; want the project note (4) and the sinking note (1)", plan.LinkEdits, plan.Links)
	}
	if sunk := string(byRel[old][0].After); !strings.Contains(sunk, "lifecycle: dormant") ||
		!strings.Contains(sunk, `"[[agent/archive/memory/semantic/cold|cold]]"`) {
		t.Errorf("the sinking note's one intent lacks the sink or the repair:\n%s", sunk)
	}

	// Applied through the journal, each edit is a line of its own and the
	// links land on the archived note.
	state := t.TempDir()
	j, _ := OpenJournal(state)
	for _, in := range plan.Intents {
		if kind, err := j.Commit(root, "r", "r-1", in, now); err != nil || kind != KindApplied {
			t.Fatalf("commit %s: kind=%s err=%v", in.Rel, kind, err)
		}
	}
	got := readOr(filepath.Join(vault, "projects/x/notes.md"))
	for _, want := range []string{
		`related: "[[agent/archive/memory/semantic/cold|cold]]"`,
		"[[agent/archive/memory/semantic/cold|the cold note]]",
		"[[agent/archive/memory/semantic/cold|agent/memory/semantic/cold]]",
		"[the file](/agent/archive/memory/semantic/cold.md)",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("the project note lacks %q:\n%s", want, got)
		}
	}
	if readOr(filepath.Join(vault, "personal/diary.md")) != coldLinks {
		t.Error("the personal note changed on disk")
	}
	journal, _ := os.ReadFile(j.Path)
	if !strings.Contains(string(journal), `"rel":"../projects/x/notes.md"`) {
		t.Errorf("the journal holds no line for the edit:\n%s", journal)
	}
}

func TestARefileRewritesThePathLinksIntoTheNote(t *testing.T) {
	root, vault := rootMapVault(t)
	r := testRules(t)
	class, ok := r.ClassFor("workflow")
	if !ok || class == "semantic" {
		t.Skipf("the packaged contract routes workflow to %q; the fixture needs a class other than semantic", class)
	}
	writeRaw(t, root, "memory/semantic/misfiled.md",
		"---\ntitle: a procedure filed as a fact\ntype: workflow\nstatus: active\n---\n\nSee [the twin](twin.md).\n")
	writeRaw(t, root, "memory/semantic/twin.md", "---\ntitle: twin\ntype: fact\nstatus: active\n---\n\nA fact.\n")
	writeAt(t, vault, "projects/x/notes.md", "Read [[agent/memory/semantic/misfiled|the procedure]].\n")
	plan, err := PlanRefile(root, r)
	if err != nil {
		t.Fatal(err)
	}
	var move, edit *Intent
	for i := range plan.Intents {
		switch plan.Intents[i].Rel {
		case "memory/semantic/misfiled.md":
			move = &plan.Intents[i]
		case "../projects/x/notes.md":
			edit = &plan.Intents[i]
		}
	}
	if move == nil || edit == nil {
		t.Fatalf("want the move and the edit: %+v", plan.Intents)
	}
	if want := "[[agent/memory/" + class + "/misfiled|the procedure]]"; !strings.Contains(string(edit.After), want) {
		t.Errorf("the project note's link was not pointed at the class: want %q in\n%s", want, edit.After)
	}
	if edit.Job != JobRefile {
		t.Errorf("the edit is %q's, want refile's", edit.Job)
	}
	// The moved note's own relative link is recomputed from its new folder.
	if !strings.Contains(string(move.After), "[the twin](../semantic/twin.md)") {
		t.Errorf("the moved note's relative link was not recomputed:\n%s", move.After)
	}
	if len(plan.LinkEdits) != 1 || plan.LinkEdits[0].Path != "projects/x/notes.md" {
		t.Errorf("link edits %+v", plan.LinkEdits)
	}
}
