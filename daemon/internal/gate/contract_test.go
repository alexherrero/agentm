package gate

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// A corpus-wide job under a broken filing contract is worse than one with no
// undo. Every decision it makes about where something belongs is a guess, and it
// makes thousands of them. Health reports the halt; this is what stops it biting.

const validBlock = "```storage-rules\n" + `classes:
  semantic: Facts and principles.
  procedural: How to do a thing.
  episodic: Session traces.
  entities: One file per referent.
  crystallized: Distilled lessons.
  mocs: Maps of content.
memory_types: [preference, convention, reference, workflow, fix, idea]
default_type: preference
routing:
  preference: memory/semantic
  convention: memory/semantic
  reference: memory/semantic
  workflow: memory/procedural
  fix: memory/procedural
  idea: desk
record_kinds: [brief]
deprecations: {preferences: preference}
warrants: {}
thresholds: {low_confidence: 0.65}
` + "```\n"

// holderOver writes a rules file into a fresh directory and points a holder at
// it via the explicit override, so the test never depends on a vault layout.
func holderOver(t *testing.T, block string) *rules.Holder {
	t.Helper()
	path := filepath.Join(t.TempDir(), "storage-rules.md")
	if err := os.WriteFile(path, []byte("# Rules\n\n"+block), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AGENTM_STORAGE_RULES", path)
	return rules.NewHolder("", time.Unix(0, 0))
}

func TestEvaluate_RefusesWhenTheContractIsBroken(t *testing.T) {
	// A vault that would otherwise pass every git check, so the refusal can only
	// be the contract.
	dir := vaultWithCommittedNote(t, nfcNote)
	holder := holderOver(t, "```storage-rules\nmemory_types: [unclosed\n```\n")

	res, err := Evaluate(&config.Config{VaultPath: dir, Rules: holder})
	if err == nil {
		t.Fatal("the gate opened for a corpus-wide job with no filing contract")
	}
	if res.Pass {
		t.Fatal("res.Pass is true on a refusal")
	}

	var found bool
	for _, r := range res.Reasons {
		if r.Code == ReasonNoContract {
			found = true
			if !strings.Contains(r.Detail, "not valid YAML") {
				t.Errorf("the refusal does not carry the parse error: %q", r.Detail)
			}
			if !strings.Contains(r.Remedy, "no restart") {
				t.Errorf("the remedy does not say the fix is picked up live: %q", r.Remedy)
			}
		}
	}
	if !found {
		t.Errorf("no %s reason given:\n%s", ReasonNoContract, res.Explain())
	}
}

// The positive. A refusal that fires on everything proves nothing, so a working
// contract has to let the gate through to its git checks and open on a clean
// vault.
func TestEvaluate_AWorkingContractDoesNotHoldTheGateShut(t *testing.T) {
	dir := vaultWithCommittedNote(t, nfcNote)
	holder := holderOver(t, validBlock)

	res, err := Evaluate(&config.Config{VaultPath: dir, Rules: holder})
	if err != nil {
		t.Fatalf("the gate refused a clean vault with a working contract: %v\n%s",
			err, res.Explain())
	}
	if !res.Pass {
		t.Errorf("gate did not pass:\n%s", res.Explain())
	}
}

// The contract is checked before git, so a vault that is broken in both ways
// names the cheaper and more specific problem first.
func TestEvaluate_TheContractIsNamedBeforeGit(t *testing.T) {
	dir := t.TempDir() // not a repository at all
	holder := holderOver(t, "```storage-rules\nmemory_types: [unclosed\n```\n")

	res, err := Evaluate(&config.Config{VaultPath: dir, Rules: holder})
	if err == nil {
		t.Fatal("the gate opened on a non-repository with a broken contract")
	}
	if len(res.Reasons) == 0 {
		t.Fatal("refused with no reason")
	}
	if res.Reasons[0].Code != ReasonNoContract {
		t.Errorf("first reason is %q; the contract is the cheaper, more specific "+
			"refusal and should be named first", res.Reasons[0].Code)
	}
}

// A nil holder is not a refusal. Callers that never resolved a contract — the
// one-shot CLI paths, and every existing test — must not be blocked by a check
// aimed at the serving daemon.
func TestEvaluate_ANilHolderIsNotARefusal(t *testing.T) {
	dir := vaultWithCommittedNote(t, nfcNote)
	if _, err := Evaluate(&config.Config{VaultPath: dir}); err != nil {
		t.Errorf("a config with no holder was refused: %v", err)
	}
}
