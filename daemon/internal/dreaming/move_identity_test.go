package dreaming

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// ── the three movers keep a moved note's identity ───────────────────────────
//
// The journal's own tests hold a single move to the rename. These hold each
// job that moves notes (the task mover, the projects axis, the lifecycle
// archive) to it from its own plan: a note whose bytes the move leaves alone
// is the same file at its new path, and a note the move rewrites is renamed
// first and rewritten in place, so a sync client sees a move and an edit,
// never a new file and a deleted one.

func TestATaskFolderMoveKeepsEachUnchangedFile(t *testing.T) {
	root, space := projectVault(t)
	src := closedTask(t, space, "agentm", "001-done", "done", "2026-08-01")
	// The plan links the roadmap by a relative path, so the move repairs it.
	writeAt(t, space, "agentm/roadmap.md", "# Roadmap\n")
	was := map[string]os.FileInfo{}
	bytesBefore := map[string]string{}
	for _, f := range []string{"tracker.md", "plan.md", "progress.md"} {
		was[f] = pinned(t, filepath.Join(src, f))
		bytesBefore[f] = readOr(filepath.Join(src, f))
	}
	cfg := moverConfig(t, root)
	if _, rep, err := MoveTasks(cfg, MoveTasksOptions{Now: taskNow, Apply: true}); err != nil || rep.Applied != 3 {
		t.Fatalf("move: applied %d, err %v", rep.Applied, err)
	}
	dst := filepath.Join(space, "agentm", "completed", "tasks", "001-done")
	kept := 0
	for f, before := range was {
		now, err := os.Stat(filepath.Join(dst, f))
		if err != nil {
			t.Fatalf("%s did not arrive: %v", f, err)
		}
		if readOr(filepath.Join(dst, f)) == bytesBefore[f] {
			kept++
			if !os.SameFile(before, now) {
				t.Errorf("%s moved unchanged, so it is the same file at its new path", f)
			}
		}
	}
	if kept < 2 {
		t.Errorf("the tracker and the progress log move unchanged; %d files did", kept)
	}
	if got := readOr(filepath.Join(dst, "plan.md")); !strings.Contains(got, "(../../../roadmap.md)") {
		t.Errorf("the plan's relative link is repaired for its new depth:\n%s", got)
	}
}

func TestAProjectsAxisMoveKeepsTheRecord(t *testing.T) {
	root, space := projectVault(t)
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)
	writeProjectFile(t, filepath.Join(space, "agentm", "tasks", "001-done", "tracker.md"),
		map[string]string{"kind": "tracker", "status": "done", "task": "001-done",
			"closed": "2026-09-01"}, "## Outcome\n\nit shipped")
	bundle := filepath.Join(space, "agentm", "research", "a-bundle.md")
	writeProjectFile(t, bundle, map[string]string{"kind": "note", "task": "001-done"}, "what it found")
	decision := filepath.Join(space, "agentm", "decisions", "old.md")
	writeProjectFile(t, decision, map[string]string{"kind": "note", "superseded_by": "[[new]]"}, "the old ruling")
	was := map[string]os.FileInfo{"a-bundle.md": pinned(t, bundle), "old.md": pinned(t, decision)}

	plan, err := PlanCompleted(root, ClosedTasks(root), nil, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Intents) != 2 {
		t.Fatalf("intents %d, want the bundle and the decision", len(plan.Intents))
	}
	j, _ := OpenJournal(t.TempDir())
	for i, in := range plan.Intents {
		if kind, err := j.Commit(root, "r", "r-"+string(rune('a'+i)), in, now); err != nil || kind != KindApplied {
			t.Fatalf("commit %s: kind=%s err=%v", in.Rel, kind, err)
		}
		landed, err := os.Stat(filepath.Join(root, filepath.FromSlash(in.To)))
		if err != nil {
			t.Fatalf("%s did not arrive: %v", in.To, err)
		}
		if !os.SameFile(was[filepath.Base(in.To)], landed) {
			t.Errorf("%s moved unchanged, so it is the same file at %s", in.Rel, in.To)
		}
	}
}

func TestALifecycleArchiveRenamesBeforeItRestamps(t *testing.T) {
	root := t.TempDir()
	now := time.Date(2026, 9, 18, 9, 0, 0, 0, time.UTC)
	cold := writeNote(t, root, "memory/semantic/cold.md", "dormant", 2000, now, "")
	plan, err := PlanLifecycle(root, "", axisContract(t), now, 0)
	if err != nil {
		t.Fatal(err)
	}
	var archive *Intent
	for i := range plan.Intents {
		if plan.Intents[i].Rel == cold && plan.Intents[i].To != "" {
			archive = &plan.Intents[i]
		}
	}
	if archive == nil {
		t.Fatalf("the cold note is past the archive line and should move: %+v", plan.Intents)
	}
	j, _ := OpenJournal(t.TempDir())
	var renames []string
	j.rename = func(o, n string) error {
		renames = append(renames, filepath.ToSlash(o)+" -> "+filepath.ToSlash(n))
		return os.Rename(o, n)
	}
	if kind, err := j.Commit(root, "r", "r-1", *archive, now); err != nil || kind != KindApplied {
		t.Fatalf("commit: kind=%s err=%v", kind, err)
	}
	want := filepath.ToSlash(filepath.Join(root, cold)) + " -> " + filepath.ToSlash(filepath.Join(root, archive.To))
	if len(renames) != 1 || renames[0] != want {
		t.Errorf("the archive is a rename of the note, then its restamp: renames %v, want [%s]", renames, want)
	}
	if got := readOr(filepath.Join(root, archive.To)); got != string(archive.After) {
		t.Errorf("the archived note carries its new lifecycle stamp:\n%s", got)
	}
}
