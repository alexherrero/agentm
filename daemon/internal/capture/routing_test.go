package capture

import (
	"strings"
	"testing"
)

// Filing v2, the write path: a note whose type the contract knows lands in
// the class the contract routes that type to, not in a year/month shard —
// where the corpus migration put everything already home, and where the
// retrieval gate and the scorecard read. The harness is configured with the
// date shard on purpose: class routing has to win over it.
func TestCaptureRoutesATypedNoteToItsClass(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{Text: "Run the battery before every commit.", Type: "workflow"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(res.Path, "memory/procedural/") {
		t.Fatalf("a workflow routes to memory/procedural/, got %q", res.Path)
	}
	if got := frontmatterLine(t, cp.cfg.VaultPath, res.Path, "lifecycle:"); got != "lifecycle: active" {
		t.Fatalf("lifecycle stamp: %q", got)
	}
	if got := frontmatterLine(t, cp.cfg.VaultPath, res.Path, "filing_confidence:"); got != "filing_confidence: high" {
		t.Fatalf("a caller who named the type stands behind it: %q", got)
	}
}

// An untyped capture takes the contract's default type, lands in that type's
// class, and says it was a guess: `unfiled` at low confidence is what the
// enrichment pass drains and the needs-review reading selects on.
func TestCaptureFilesAnUntypedNoteAtTheDefaultClassAtLowConfidence(t *testing.T) {
	cp := newHarness(t)
	res, err := cp.Do(Request{Text: "Something worth keeping, typed by nobody."})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "unfiled" {
		t.Fatalf("status %q, want unfiled", res.Status)
	}
	if strings.Contains(res.Path, "/20") {
		t.Fatalf("a defaulted type is still a type the contract routes; got the shard %q", res.Path)
	}
	if !strings.HasPrefix(res.Path, "memory/") || strings.Count(res.Path, "/") != 2 {
		t.Fatalf("expected memory/<class>/<slug>.md, got %q", res.Path)
	}
	if got := frontmatterLine(t, cp.cfg.VaultPath, res.Path, "filing_confidence:"); got != "filing_confidence: low" {
		t.Fatalf("a defaulted type is the contract's guess: %q", got)
	}
}

func TestClassDirAcceptsVaultRelativeAndSpaceRelativeRouting(t *testing.T) {
	holder := newHarness(t).cfg.Rules
	contract, err := holder.Get()
	if err != nil {
		t.Fatal(err)
	}
	if got := classDir(contract, nil, "workflow", "memory"); got != "memory/procedural" {
		t.Fatalf("vault-relative routing: %q", got)
	}
	if got := classDir(contract, nil, "", "memory"); got != "" {
		t.Fatalf("no type, nothing to route by: %q", got)
	}
	if got := classDir(contract, nil, "not-a-type", "memory"); got != "" {
		t.Fatalf("an unrouted type falls back to the shard: %q", got)
	}
	if got := classDir(nil, errHalted, "workflow", "memory"); got != "" {
		t.Fatalf("a halted contract routes nothing: %q", got)
	}
}

type haltedErr struct{}

func (haltedErr) Error() string { return "halted" }

var errHalted error = haltedErr{}
