package capture

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/rules"
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

// The contract's routing values are written from three vantage points and all
// three have to land in the same class directory inside the space. The middle
// one is the case that shipped broken: the live space is `Agent/memory` and the
// live routing value is `memory/procedural`, relative to the memory root, and
// joining it onto the space built `Agent/memory/memory/procedural` — a second
// class tree the index walked as readily as the real one, which is why the
// daily round-trip probe passed for eight weeks from inside it.
func TestClassDirResolvesEveryRoutingVantagePointIntoTheSpace(t *testing.T) {
	holder := newHarness(t).cfg.Rules
	contract, err := holder.Get()
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range []struct{ name, space, want string }{
		{"space names the value's own root", "memory", "memory/procedural"},
		{"relative to the memory root (the live shape)", "Agent/memory", "Agent/memory/procedural"},
		{"nested memory root", "a/b/memory", "a/b/memory/procedural"},
	} {
		if got := classDir(contract, nil, "workflow", c.space); got != c.want {
			t.Errorf("%s: classDir(workflow, %q) = %q, want %q", c.name, c.space, got, c.want)
		}
	}
	// Whatever the vantage point, the answer is inside the space. This is the
	// invariant the bug broke: a routing value must not be able to place a note
	// in a directory the space does not contain.
	for _, space := range []string{"memory", "Agent/memory", "a/b/memory"} {
		got := classDir(contract, nil, "workflow", space)
		if got != space && !strings.HasPrefix(got, space+"/") {
			t.Errorf("space %q: routed outside the space, to %q", space, got)
		}
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

// A space-relative routing value still resolves, and still lands in the space.
// It is the form the memory-root branch must not swallow: read from the memory
// root, "procedural" would resolve to `Agent/procedural` — outside the space,
// and the reason that branch checks its answer before returning it.
//
// The contract is written to a vault and read back through the holder the
// daemon uses, rather than assembled as a literal: `rules.Rules` embeds an
// unexported struct, and a routing table that never went through the parser
// would prove the parser agrees with nothing.
func TestClassDirKeepsSpaceRelativeRoutingInsideTheSpace(t *testing.T) {
	dir := t.TempDir()
	vault := filepath.Join(dir, "vault")
	if err := os.MkdirAll(filepath.Join(vault, "standards"), 0o755); err != nil {
		t.Fatal(err)
	}
	// One value rewritten, class-relative. The rest of the shipped contract is
	// left alone so the parser sees a real file.
	line := regexp.MustCompile(`(?m)^[ \t]*workflow:[ \t]*memory/procedural[ \t]*$`)
	text := rules.Default()
	if !line.MatchString(text) {
		t.Fatal("the shipped contract no longer routes workflow to memory/procedural")
	}
	text = line.ReplaceAllString(text, "  workflow: procedural")
	if err := os.WriteFile(filepath.Join(vault, "standards", "storage-rules.md"),
		[]byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	contract, err := rules.NewHolder(vault, time.Now()).Get()
	if err != nil {
		t.Fatal(err)
	}
	if got := contract.Routing["workflow"]; got != "procedural" {
		t.Fatalf("the rewritten routing value did not survive the parser: %q", got)
	}
	for _, c := range []struct{ space, want string }{
		{"memory", "memory/procedural"},
		{"Agent/memory", "Agent/memory/procedural"},
	} {
		if got := classDir(contract, nil, "workflow", c.space); got != c.want {
			t.Errorf("classDir(workflow, %q) = %q, want %q", c.space, got, c.want)
		}
	}
}

type haltedErr struct{}

func (haltedErr) Error() string { return "halted" }

var errHalted error = haltedErr{}
