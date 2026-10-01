package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/enrich"
	"github.com/alexherrero/agentm/daemon/internal/ledger"
)

// The judgment-hash cutover (task 181 step 3), over a vault whose contract has
// a git history, through the shipped opener.

func git(t *testing.T, dir string, args ...string) {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-C", dir}, args...)...)
	cmd.Env = append(os.Environ(), "GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@example.com",
		"GIT_COMMITTER_NAME=t", "GIT_COMMITTER_EMAIL=t@example.com")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
}

// editContract rewrites the contract's block with one edit.
func editContract(t *testing.T, vault, from, to string) {
	t.Helper()
	p := filepath.Join(vault, "standards", "storage-rules.md")
	raw, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), from) {
		t.Fatalf("the contract has no %q to edit", from)
	}
	if err := os.WriteFile(p, []byte(strings.Replace(string(raw), from, to, 1)), 0o644); err != nil {
		t.Fatal(err)
	}
}

// vaultDigest fingerprints every file under the vault but git's own, so a test
// can say the cutover wrote nothing there.
func vaultDigest(t *testing.T, vault string) string {
	t.Helper()
	h := sha256.New()
	err := filepath.WalkDir(vault, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			if d.Name() == ".git" {
				return fs.SkipDir
			}
			return nil
		}
		raw, err := os.ReadFile(p)
		if err != nil {
			return err
		}
		info, _ := d.Info()
		rel, _ := filepath.Rel(vault, p)
		h.Write([]byte(rel + "\x00" + info.ModTime().String() + "\x00"))
		h.Write(raw)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return hex.EncodeToString(h.Sum(nil))
}

// A contract edit to something no judgment reads, made before the cutover,
// left every note judged under the old contract stale. The cutover translates
// those rows: the note reads as current, no note file is written, and a refusal
// keyed the old way is carried across too.
func TestTheCutoverCarriesJudgmentsAcrossAnEditTheyNeverRead(t *testing.T) {
	ctx := context.Background()
	f := newLedgerFixture(t)
	git(t, f.vault, "init", "-q")
	git(t, f.vault, "add", "-A")
	git(t, f.vault, "commit", "-q", "-m", "the contract as judged")

	loaded, err := f.cfg.Rules.Get()
	if err != nil {
		t.Fatal(err)
	}
	judgedUnder := loaded.Hash
	old := &enrich.Fingerprint{Version: enrich.PassVersion, RulesHash: judgedUnder}

	// The edit, committed, as the live contract.
	editContract(t, f.vault, "thresholds: {low_confidence: 0.65}\n",
		"thresholds: {low_confidence: 0.65}\ndampened_spaces: [resources]\n")
	git(t, f.vault, "commit", "-q", "-am", "dampen resources")
	if _, err := f.cfg.Rules.Refresh(time.Now()); err != nil {
		t.Fatal(err)
	}
	now, _ := f.cfg.Rules.Get()
	if now.Hash == judgedUnder {
		t.Fatal("the edit did not move the contract's hash; the test tests nothing")
	}

	// The ledger and the refusals as the old binary left them.
	idx := f.openIndex(t)
	defer idx.Close()
	pre, err := ledger.Open(idx.DB())
	if err != nil {
		t.Fatal(err)
	}
	if err := pre.Record(ctx, ledger.Entry{
		Stage: ledger.StageEnrich, Target: f.rel, Version: enrich.PassVersion,
		RulesHash: judgedUnder, InputKey: old.Key("the raw capture"),
		OutputKey: old.Key(f.body), Outcome: ledger.Done,
		At: time.Date(2026, 9, 30, 9, 0, 0, 0, time.UTC),
	}); err != nil {
		t.Fatal(err)
	}
	refusals, err := enrich.NewRefusals(enrichStateDir(f.cfg))
	if err != nil {
		t.Fatal(err)
	}
	refusedRel := "memory/semantic/refused.md"
	refusedBody := writeNote(t, f.vault, refusedRel, response("refused", 0.9), enrich.Stamp{})
	if err := refusals.Record(enrich.Refusal{Rel: refusedRel, Gate: "grounding",
		Key: old.Key(refusedBody), Version: enrich.PassVersion, RulesHash: judgedUnder,
		Gates: enrich.GatesVersion, Reason: "invented a claim"}); err != nil {
		t.Fatal(err)
	}
	before := vaultDigest(t, f.vault)

	log := &bytes.Buffer{}
	led, err := openLedger(ctx, f.cfg, idx, log)
	if err != nil {
		t.Fatal(err)
	}
	defer led.Close()
	if !strings.Contains(log.String(), "judgment-hash cutover: 1 row(s) translated, 1 of them re-keyed") ||
		!strings.Contains(log.String(), "1 refusal(s) translated") {
		t.Errorf("the cutover said %q", log)
	}
	if after := vaultDigest(t, f.vault); after != before {
		t.Error("the cutover wrote into the vault")
	}

	rep, err := pendingFor(ctx, ledger.StageEnrich, f.cfg, idx, led)
	if err != nil {
		t.Fatal(err)
	}
	// The population is the indexed card, asserted so "current" is not true of
	// an empty queue. Before the cutover it read as stale.
	if rep.Eligible != 1 || rep.Current != 1 {
		t.Errorf("after the cutover: eligible %d · current %d · pending %+v; want the "+
			"judged note current", rep.Eligible, rep.Current, rep.Pending)
	}
	again, err := enrich.NewRefusals(enrichStateDir(f.cfg))
	if err != nil {
		t.Fatal(err)
	}
	if _, standing := again.Standing(refusedRel,
		enrichFingerprint(f.cfg, nil).Key(refusedBody), enrich.GatesVersion); !standing {
		t.Error("the refusal does not stand under the new key; the card would be " +
			"bought another answer to a question it was refused on")
	}
}

// A note edited since its judgment keeps its old key, so the cutover cannot
// make it look current: it reads as changed, as it would have before. And a row
// under a contract the history does not hold is left as it is, stale.
func TestTheCutoverChangesNoDecisionTheOldHashGotRight(t *testing.T) {
	ctx := context.Background()
	f := newLedgerFixture(t)
	git(t, f.vault, "init", "-q")
	git(t, f.vault, "add", "-A")
	git(t, f.vault, "commit", "-q", "-m", "the contract")
	loaded, _ := f.cfg.Rules.Get()
	old := &enrich.Fingerprint{Version: enrich.PassVersion, RulesHash: loaded.Hash}

	idx := f.openIndex(t)
	defer idx.Close()
	pre, err := ledger.Open(idx.DB())
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range []ledger.Entry{
		// Judged over a body that is no longer the note's.
		{Target: f.rel, RulesHash: loaded.Hash, OutputKey: old.Key("an older body")},
		// Judged under a contract nobody committed.
		{Target: "memory/semantic/elsewhere.md", RulesHash: "0123456789abcdef",
			OutputKey: "k"},
	} {
		e.Stage, e.Version, e.Outcome = ledger.StageEnrich, enrich.PassVersion, ledger.Done
		if err := pre.Record(ctx, e); err != nil {
			t.Fatal(err)
		}
	}

	led, err := openLedger(ctx, f.cfg, idx, &bytes.Buffer{})
	if err != nil {
		t.Fatal(err)
	}
	defer led.Close()
	rep, err := pendingFor(ctx, ledger.StageEnrich, f.cfg, idx, led)
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.Pending) != 1 || rep.Pending[0].Reason != ledger.ReasonChanged {
		t.Errorf("an edited note reads as %+v, want changed", rep.Pending)
	}
	kept, ok, err := led.Lookup(ctx, ledger.StageEnrich, "memory/semantic/elsewhere.md")
	if err != nil || !ok || kept.RulesHash != "0123456789abcdef" || kept.OutputKey != "k" {
		t.Errorf("an unmapped row = %+v (%v, %v), want it left as it was", kept, ok, err)
	}

	// Once per file.
	led.Close()
	log := &bytes.Buffer{}
	led, err = openLedger(ctx, f.cfg, idx, log)
	if err != nil {
		t.Fatal(err)
	}
	// Closed before the test's temporary directory is removed: Windows will
	// not delete a file another handle holds open.
	defer led.Close()
	if log.Len() != 0 {
		t.Errorf("a second open said %q; the cutover runs once", log)
	}
}
