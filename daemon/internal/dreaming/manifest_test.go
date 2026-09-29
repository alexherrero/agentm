package dreaming

import (
	"bufio"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// manifestVault is a nested vault: <tmp>/vault/.obsidian marks the vault root,
// <tmp>/vault/agent is the memory root.
func manifestVault(t *testing.T) (root string) {
	t.Helper()
	vault := filepath.Join(t.TempDir(), "vault")
	root = filepath.Join(vault, "agent")
	for _, d := range []string{".obsidian", "agent/memory/episodic", "personal/ideas"} {
		if err := os.MkdirAll(filepath.Join(vault, d), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func supersedeAct(t *testing.T, root, rel, to string) ManifestAct {
	t.Helper()
	cur, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
	if err != nil {
		t.Fatal(err)
	}
	after := strings.Replace(string(cur), "lifecycle: active\n", "lifecycle: superseded\nsuperseded_by: "+to+"\n", 1)
	return ManifestAct{Rel: rel, Before: Hash(cur), After: after, Summary: "folded into " + to,
		From: "active", To: "superseded"}
}

const traceBody = "---\ntitle: \"yes\"\nkind: session-trace\nstatus: active\nlifecycle: active\n---\n\n## Asked\n\nyes\n"

func TestAManifestThatIsCheckedWritesNothing(t *testing.T) {
	root := manifestVault(t)
	writeAt(t, root, "memory/episodic/b.md", traceBody)
	cfg := moverConfig(t, root)
	m := Manifest{Job: "manifest-traces", Acts: []ManifestAct{supersedeAct(t, root, "memory/episodic/b.md", "memory/episodic/a.md")}}
	res, err := ApplyManifest(cfg, m, ApplyManifestOptions{Now: taskNow})
	if err != nil {
		t.Fatal(err)
	}
	if res.Mode != "check" || res.Ready != 1 || res.Applied != 0 {
		t.Errorf("check: %+v", res)
	}
	got, _ := os.ReadFile(filepath.Join(root, "memory/episodic/b.md"))
	if string(got) != traceBody {
		t.Errorf("a check rewrote the note:\n%s", got)
	}
	if _, err := os.Stat(filepath.Join(cfg.EngineStateDir, "dreaming", "journal.jsonl")); err == nil {
		if raw, _ := os.ReadFile(filepath.Join(cfg.EngineStateDir, "dreaming", "journal.jsonl")); len(raw) > 0 {
			t.Errorf("a check journaled something:\n%s", raw)
		}
	}
}

func TestAnAppliedManifestIsJournaledAndReachesTheLifecycleJournal(t *testing.T) {
	root := manifestVault(t)
	writeAt(t, root, "memory/episodic/b.md", traceBody)
	writeAt(t, root, "../personal/ideas/card.md", "---\ntype: idea\n---\nAn idea.\n")
	cfg := moverConfig(t, root)
	card := "../personal/ideas/card.md"
	cardBefore, _ := os.ReadFile(filepath.Join(root, card))
	m := Manifest{Job: "manifest-traces", Acts: []ManifestAct{
		supersedeAct(t, root, "memory/episodic/b.md", "memory/episodic/a.md"),
		{Rel: card, Before: Hash(cardBefore), After: string(cardBefore) + "\n## Added by capture\n\nMore.\n"},
	}}
	res, err := ApplyManifest(cfg, m, ApplyManifestOptions{Now: taskNow, Apply: true, RunID: "run-m"})
	if err != nil {
		t.Fatal(err)
	}
	if res.Applied != 2 || res.Skipped != 0 {
		t.Fatalf("applied %d skipped %d", res.Applied, res.Skipped)
	}
	got, _ := os.ReadFile(filepath.Join(root, "memory/episodic/b.md"))
	if !strings.Contains(string(got), "superseded_by: memory/episodic/a.md") {
		t.Errorf("the supersede was not written:\n%s", got)
	}
	got, _ = os.ReadFile(filepath.Join(root, card))
	if !strings.HasSuffix(string(got), "## Added by capture\n\nMore.\n") {
		t.Errorf("the card outside the memory root was not written:\n%s", got)
	}
	journal, _ := OpenJournal(cfg.EngineStateDir)
	entries, _ := journal.Read()
	kinds := map[string]int{}
	for _, e := range entries {
		kinds[e.Kind]++
		if e.Kind == KindIntent && e.Job != "manifest-traces" {
			t.Errorf("intent journaled under job %q", e.Job)
		}
	}
	if kinds[KindIntent] != 2 || kinds[KindApplied] != 2 || kinds[KindRunStart] != 1 || kinds[KindRunDone] != 1 {
		t.Errorf("journal kinds %v", kinds)
	}
	// The supersede is a lifecycle transition, and the lifecycle journal says so;
	// the card's append is not one, and it says nothing.
	f, err := os.Open(filepath.Join(cfg.EngineStateDir, "lifecycle-journal.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var lines []map[string]any
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		var l map[string]any
		if json.Unmarshal(sc.Bytes(), &l) == nil {
			lines = append(lines, l)
		}
	}
	if len(lines) != 1 || !strings.Contains(toJSON(lines), `"superseded"`) {
		t.Errorf("lifecycle journal: %v", lines)
	}
}

func toJSON(v any) string {
	b, _ := json.Marshal(v)
	return string(b)
}

func TestAManifestWithAChangedNoteIsRefusedWhole(t *testing.T) {
	root := manifestVault(t)
	writeAt(t, root, "memory/episodic/a.md", traceBody)
	writeAt(t, root, "memory/episodic/b.md", traceBody)
	cfg := moverConfig(t, root)
	m := Manifest{Job: "manifest-traces", Acts: []ManifestAct{
		supersedeAct(t, root, "memory/episodic/a.md", "x"),
		supersedeAct(t, root, "memory/episodic/b.md", "x"),
	}}
	writeAt(t, root, "memory/episodic/b.md", traceBody+"\nedited by hand\n")
	res, err := ApplyManifest(cfg, m, ApplyManifestOptions{Now: taskNow, Apply: true})
	if !errors.Is(err, ErrManifestNotReady) {
		t.Fatalf("err %v, want ErrManifestNotReady", err)
	}
	if len(res.Changed) != 1 || res.Changed[0] != "memory/episodic/b.md" || res.Applied != 0 {
		t.Errorf("result %+v", res)
	}
	got, _ := os.ReadFile(filepath.Join(root, "memory/episodic/a.md"))
	if string(got) != traceBody {
		t.Error("the ready act was applied although the manifest was refused")
	}
}

func TestAManifestCannotReachPastTheVault(t *testing.T) {
	root := manifestVault(t)
	cfg := moverConfig(t, root)
	outside := filepath.Join(filepath.Dir(filepath.Dir(root)), "outside.md")
	if err := os.WriteFile(outside, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	for _, rel := range []string{"../../outside.md", "../.git/config", "", "/etc/hosts"} {
		m := Manifest{Job: "manifest-x", Acts: []ManifestAct{{Rel: rel, Before: Hash([]byte("x")), After: "y"}}}
		res, err := ApplyManifest(cfg, m, ApplyManifestOptions{Now: taskNow, Apply: true})
		if !errors.Is(err, ErrManifestNotReady) || len(res.Refused) != 1 {
			t.Errorf("%q: err %v refused %v", rel, err, res.Refused)
		}
	}
	if got, _ := os.ReadFile(outside); string(got) != "x" {
		t.Error("a manifest wrote outside the vault")
	}
	m := Manifest{Job: "Bad Job", Acts: nil}
	if res, _ := ApplyManifest(cfg, m, ApplyManifestOptions{Now: taskNow}); len(res.Refused) != 1 {
		t.Errorf("a job name that is not a lower-case name was accepted: %+v", res)
	}
}
