package main

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/ledger"
)

// An unchanged judgment writes nothing (task 181 step 5), through whole nights
// of the command against the model stub.

type stubNight struct {
	t      *testing.T
	vault  string
	state  string
	kernel string
	idx    string
	types  []string
}

func newStubNight(t *testing.T) *stubNight {
	t.Helper()
	stub := t.TempDir()
	src := filepath.Join(stub, "main.go")
	if err := os.WriteFile(src, []byte(nightStubSource), 0o644); err != nil {
		t.Fatal(err)
	}
	bin := filepath.Join(stub, "claude")
	if runtime.GOOS == "windows" {
		bin += ".exe"
	}
	if out, err := exec.Command("go", "build", "-o", bin, src).CombinedOutput(); err != nil {
		t.Fatalf("building the model stub: %v\n%s", err, out)
	}
	t.Setenv("PATH", stub+string(os.PathListSeparator)+os.Getenv("PATH"))
	if found, err := exec.LookPath("claude"); err != nil || filepath.Dir(found) != stub {
		t.Fatalf("claude resolves to %q (%v), not to the stub", found, err)
	}
	t.Setenv("AGENTMD_STUB_VERDICT", `{"grounded": true}`)
	n := &stubNight{t: t, vault: t.TempDir(), state: t.TempDir(), types: []string{"reference"}}
	t.Setenv("AGENTM_STATE_DIR", n.state)
	t.Setenv("AGENTM_STORAGE_RULES", "")
	os.Unsetenv("AGENTM_STORAGE_RULES")
	writeRules(t, n.vault, n.types...)
	n.kernel = filepath.Join(t.TempDir(), "agentm-config.json")
	if err := os.WriteFile(n.kernel, []byte(`{"plugins.obsidian-vault.memory_root": "agent", `+
		`"daemon.embedder_url": "http://127.0.0.1:1"}`), 0o644); err != nil {
		t.Fatal(err)
	}
	n.idx = filepath.Join(t.TempDir(), "index.db")
	return n
}

func (n *stubNight) put(rel, body string) {
	n.t.Helper()
	x, err := index.Open(n.idx, n.vault, "agent", false)
	if err != nil {
		n.t.Fatal(err)
	}
	putNote(n.t, x, n.vault, rel, body)
	x.Close()
}

// declare adds a memory type: an edit a judgment reads, which makes every
// judged note owed again without touching any of them.
func (n *stubNight) declare(ty string) {
	n.t.Helper()
	n.types = append(n.types, ty)
	writeRules(n.t, n.vault, n.types...)
}

// run is one night with the model answering `answer`; it returns the night's
// run record.
func (n *stubNight) run(answer string) enrichRun {
	n.t.Helper()
	n.t.Setenv("AGENTMD_STUB_ANSWER", answer)
	captureStdout(n.t, func() error {
		return cmdEnrich([]string{"-config", n.kernel, "-vault", n.vault, "-index", n.idx,
			"-page-size", "10", "-yes"})
	})
	raw, err := os.ReadFile(filepath.Join(n.state, "enrich-runs.jsonl"))
	if err != nil {
		n.t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	var run enrichRun
	if err := json.Unmarshal([]byte(lines[len(lines)-1]), &run); err != nil {
		n.t.Fatal(err)
	}
	return run
}

// pending is the ledger's report over the cards, as `agentmd ledger --pending`.
func (n *stubNight) pending() ledger.Report {
	n.t.Helper()
	cfg, err := config.Load(config.Options{ConfigPath: n.kernel, VaultPath: n.vault, IndexPath: n.idx})
	if err != nil {
		n.t.Fatal(err)
	}
	x, err := index.Open(n.idx, n.vault, "agent", false)
	if err != nil {
		n.t.Fatal(err)
	}
	defer x.Close()
	led, err := openLedger(context.Background(), cfg, x, &bytes.Buffer{})
	if err != nil {
		n.t.Fatal(err)
	}
	defer led.Close()
	rep, err := pendingFor(context.Background(), ledger.StageEnrich, cfg, x, led)
	if err != nil {
		n.t.Fatal(err)
	}
	return rep
}

func fileState(t *testing.T, path string) ([]byte, time.Time) {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	return raw, st.ModTime()
}

const sameAnswer = `{"title": "Keep git out of Drive", "type": "reference", ` +
	`"summary": "Drive corrupts a git directory.", "tags": ["git", "drive"], ` +
	`"confidence": 0.9, "importance_proposed": 6, "body": "Drive corrupts git."}`

func TestAJudgmentThatMatchesTheNoteWritesNothing(t *testing.T) {
	n := newStubNight(t)
	card := "agent/memory/semantic/keep-git-out-of-drive.md"
	n.put(card, "---\ntype: reference\nstatus: unfiled\n---\n\nDrive corrupts git.\n")
	abs := filepath.Join(n.vault, filepath.FromSlash(card))

	if run := n.run(sameAnswer); run.Enriched != 1 || run.Verdicts.Unchanged != 0 {
		t.Fatalf("the first night enriched %d, unchanged %d; want the first judgment written",
			run.Enriched, run.Verdicts.Unchanged)
	}
	before, beforeMod := fileState(t, abs)
	if !strings.Contains(string(before), "enriched_at:") {
		t.Fatalf("the first night did not stamp the card:\n%s", before)
	}

	// Owed again without a change to the card: a type declared.
	n.declare("recipe")
	if rep := n.pending(); rep.Eligible != 1 || len(rep.Pending) != 1 {
		t.Fatalf("after the edit: eligible %d, pending %+v; the card should be owed again",
			rep.Eligible, rep.Pending)
	}
	time.Sleep(20 * time.Millisecond) // so a rewrite could not keep the old mtime

	run := n.run(sameAnswer)
	if run.Enriched != 1 || run.Verdicts.Unchanged != 1 {
		t.Fatalf("the second night enriched %d, unchanged %d; want one judgment that "+
			"wrote nothing", run.Enriched, run.Verdicts.Unchanged)
	}
	after, afterMod := fileState(t, abs)
	if !bytes.Equal(before, after) {
		t.Errorf("the card was rewritten for the same answer:\nbefore %s\nafter  %s", before, after)
	}
	if !afterMod.Equal(beforeMod) {
		t.Errorf("the card's mtime moved from %s to %s", beforeMod, afterMod)
	}
	if rep := n.pending(); rep.Current != 1 || len(rep.Pending) != 0 {
		t.Errorf("after the unchanged judgment: current %d, pending %+v; the note is owed "+
			"again, so the next night would pay for it", rep.Current, rep.Pending)
	}

	// A judgment that changes a real field still writes.
	n.declare("tip")
	changed := strings.Replace(sameAnswer, "Drive corrupts a git directory.",
		"Drive's sync corrupts a git directory's objects.", 1)
	if run := n.run(changed); run.Verdicts.Unchanged != 0 {
		t.Errorf("a changed summary counted as unchanged")
	}
	if now, _ := fileState(t, abs); bytes.Equal(now, after) ||
		!strings.Contains(string(now), "Drive's sync corrupts") {
		t.Errorf("a changed judgment was not written:\n%s", now)
	}
}

// The choice step 5 settles: `enriched_by` is not masked, so a pass-version
// bump writes it, and the note is not left owing the deep pass.
func TestAPassVersionBumpStillWritesAndLeavesNothingOwed(t *testing.T) {
	n := newStubNight(t)
	card := "agent/memory/semantic/keep-git-out-of-drive.md"
	n.put(card, "---\ntype: reference\nstatus: unfiled\n---\n\nDrive corrupts git.\n")
	abs := filepath.Join(n.vault, filepath.FromSlash(card))
	n.run(sameAnswer)

	saved := enrich.PassVersion
	t.Cleanup(func() { enrich.PassVersion = saved })
	enrich.PassVersion = saved + "-bumped"
	if raw, _ := fileState(t, abs); enrich.PassDepth(string(raw)) != enrich.DepthDeep {
		t.Fatal("the bump did not make the card owed the deep pass; the test tests nothing")
	}
	run := n.run(sameAnswer)
	if run.Enriched != 1 || run.Verdicts.Unchanged != 0 {
		t.Fatalf("the bumped night enriched %d, unchanged %d; want the new stamp written",
			run.Enriched, run.Verdicts.Unchanged)
	}
	raw, _ := fileState(t, abs)
	if got := enrich.FrontmatterValue(string(raw), "enriched_by"); got != enrich.PassVersion {
		t.Errorf("enriched_by = %q, want the bumped pass %q", got, enrich.PassVersion)
	}
	if enrich.PassDepth(string(raw)) != enrich.DepthLight {
		t.Error("after the bumped judgment the card still owes the deep pass")
	}
	if rep := n.pending(); len(rep.Pending) != 0 {
		t.Errorf("after the bumped judgment the card is still pending: %+v", rep.Pending)
	}
}

// Through a whole night: a project record with no frontmatter block costs no
// model call, writes nothing, and is not offered as never judged again.
func TestARecordWithNoFrontmatterCostsTheNightNothing(t *testing.T) {
	n := newStubNight(t)
	rec := "projects/agentm/decisions/no-front.md"
	n.put(rec, "# A decision\n\nWe keep the wall.\n")
	run := n.run(sameAnswer)
	if run.ModelCalls != 0 || run.Failed != 0 || run.Enriched != 0 {
		t.Fatalf("the night made %d call(s), failed %d, enriched %d; want the record "+
			"declined before any call", run.ModelCalls, run.Failed, run.Enriched)
	}
	if run.Skipped != 1 {
		t.Errorf("skipped %d, want the record", run.Skipped)
	}
	raw, err := os.ReadFile(filepath.Join(n.vault, filepath.FromSlash(rec)))
	if err != nil || string(raw) != "# A decision\n\nWe keep the wall.\n" {
		t.Errorf("the record was written: %q (%v)", raw, err)
	}
	if run.Owed["never"] != 1 {
		t.Fatalf("owed at the start %v; want the record owed as never judged", run.Owed)
	}
	// The next night owes it as skipped — free, and after every note a call
	// could help — not as never judged at the head of the queue.
	if again := n.run(sameAnswer); again.Owed["never"] != 0 || again.Owed["skipped"] != 1 ||
		again.ModelCalls != 0 {
		t.Errorf("the second night owed %v and made %d call(s); want the record owed "+
			"as skipped and no call", again.Owed, again.ModelCalls)
	}
}
