package rules

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// validBlock is a minimal contract that passes every shape check. Tests break one
// thing at a time against it, so a failure names the rule it broke.
const validBlock = `classes:
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
record_kinds: [brief, telemetry]
deprecations: {preferences: preference, insight: idea}
warrants: {}
thresholds: {low_confidence: 0.65}
lifecycle: [pinned, active, dormant, archived, superseded]
default_lifecycle: active
sources: {operator-direct: trusted, external-fetch: untrusted}
facets: [meetings, diary]
`

func rulesFile(block string) string {
	return "# Storage rules\n\nSome prose.\n\n```storage-rules\n" + block + "```\n\nMore prose.\n"
}

func writeRules(t *testing.T, dir, block string) string {
	t.Helper()
	path := filepath.Join(dir, "storage-rules.md")
	if err := os.WriteFile(path, []byte(rulesFile(block)), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	return path
}

// clearEnv removes the override so a developer's own AGENTM_STORAGE_RULES does
// not silently decide what these tests read.
func clearEnv(t *testing.T) {
	t.Helper()
	t.Setenv("AGENTM_STORAGE_RULES", "")
	os.Unsetenv("AGENTM_STORAGE_RULES")
}

func TestValidBlockRoundTrips(t *testing.T) {
	path := writeRules(t, t.TempDir(), validBlock)
	r, err := LoadFile(path)
	if err != nil {
		t.Fatalf("LoadFile: %v", err)
	}
	if len(r.MemoryTypes) != 6 {
		t.Errorf("memory types = %d, want 6", len(r.MemoryTypes))
	}
	if !r.IsMemoryType("workflow") || r.IsMemoryType("brief") {
		t.Error("IsMemoryType does not separate the two registers")
	}
	if !r.IsRecordKind("brief") || r.IsRecordKind("workflow") {
		t.Error("IsRecordKind does not separate the two registers")
	}
	if to, ok := r.ReplacementFor("preferences"); !ok || to != "preference" {
		t.Errorf("ReplacementFor(preferences) = %q, %v; want preference, true", to, ok)
	}
	if _, ok := r.ReplacementFor("workflow"); ok {
		t.Error("a current value reported as retired")
	}
	if r.DefaultType != "preference" {
		t.Errorf("default type = %q", r.DefaultType)
	}
	if !r.IsLifecycle("dormant") || r.IsLifecycle("expired") {
		t.Error("IsLifecycle does not carry the v2 axis (or resurrects a retired value)")
	}
	if r.DefaultLifecycle != "active" {
		t.Errorf("default lifecycle = %q", r.DefaultLifecycle)
	}
	if tier, ok := r.SourceTier("external-fetch"); !ok || tier != "untrusted" {
		t.Errorf("SourceTier(external-fetch) = %q, %v; want untrusted, true", tier, ok)
	}
	if _, ok := r.SourceTier("carrier-pigeon"); ok {
		t.Error("an unnamed transport reported a tier — the caller must see absence, not a guess")
	}
	if !r.IsFacet("diary") || r.IsFacet("interrupts") {
		t.Error("IsFacet does not read the registry")
	}
}

// Each case breaks one rule. The point of every message is that a block can be
// valid YAML and still be a malformed rule — shape validation is as load-bearing
// as the parse, because the alternative is handing the malformation to a model.
func TestShapeValidation(t *testing.T) {
	cases := []struct {
		name  string
		block string
		want  string
	}{
		{"unparseable YAML", "memory_types: [unclosed\n", "not valid YAML"},
		{"missing classes",
			strings.Replace(validBlock, "classes:\n  semantic: Facts and principles.\n", "", 1),
			"classes"},
		{"a seventh class",
			strings.Replace(validBlock, "  mocs: Maps of content.\n", "  meetings: A meeting.\n", 1),
			"six retrieval classes"},
		{"a value in both registers",
			strings.Replace(validBlock, "record_kinds: [brief, telemetry]",
				"record_kinds: [brief, workflow]", 1),
			"both"},
		{"a type with no route",
			strings.Replace(validBlock, "  fix: memory/procedural\n", "", 1),
			"no `routing` entry"},
		{"a route to a derived class",
			strings.Replace(validBlock, "  fix: memory/procedural", "  fix: memory/crystallized", 1),
			"derived class"},
		{"a deprecation pointing nowhere",
			strings.Replace(validBlock, "insight: idea", "insight: musing", 1),
			"no register carries"},
		{"a value retired and registered at once",
			strings.Replace(validBlock, "insight: idea", "brief: idea", 1),
			"retired or current"},
		{"a non-kebab type",
			strings.Replace(validBlock, "memory_types: [preference,", "memory_types: [Preference,", 1),
			"kebab-case"},
		{"no default type",
			strings.Replace(validBlock, "default_type: preference\n", "", 1),
			"default_type"},
		{"a default type that is not a type",
			strings.Replace(validBlock, "default_type: preference", "default_type: brief", 1),
			"not a memory type"},
		{"a warrant missing a field",
			strings.Replace(validBlock, "warrants: {}",
				"warrants:\n  person:\n    query_class: who is X\n    nearest: reference", 1),
			"why_not"},
		{"a lifecycle vocabulary with no default",
			strings.Replace(validBlock, "default_lifecycle: active\n", "", 1),
			"default_lifecycle"},
		{"a default lifecycle the axis does not name",
			strings.Replace(validBlock, "default_lifecycle: active", "default_lifecycle: expired", 1),
			"does not name"},
		{"a non-kebab lifecycle value",
			strings.Replace(validBlock, "lifecycle: [pinned,", "lifecycle: [Pinned,", 1),
			"kebab-case"},
		{"a source mapped to an unknown tier",
			strings.Replace(validBlock, "external-fetch: untrusted", "external-fetch: dubious", 1),
			"tier"},
		{"a duplicate facet",
			strings.Replace(validBlock, "facets: [meetings, diary]", "facets: [diary, diary]", 1),
			"twice"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			path := writeRules(t, t.TempDir(), tc.block)
			_, err := LoadFile(path)
			if err == nil {
				t.Fatal("expected an error, got none")
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Errorf("error %q does not mention %q", err, tc.want)
			}
		})
	}
}

func TestNoBlockIsAnError(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "storage-rules.md")
	if err := os.WriteFile(path, []byte("# Storage rules\n\nProse only.\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadFile(path); err == nil || !strings.Contains(err.Error(), "storage-rules fenced block") {
		t.Errorf("err = %v; want a missing-block error", err)
	}
}

// Absence falls through; corruption halts. These two are the property the whole
// fail-closed arrangement rests on.
func TestResolution(t *testing.T) {
	t.Run("a vault file wins over the embedded default", func(t *testing.T) {
		clearEnv(t)
		vault := t.TempDir()
		if err := os.MkdirAll(filepath.Join(vault, "standards"), 0o755); err != nil {
			t.Fatal(err)
		}
		writeRules(t, filepath.Join(vault, "standards"), validBlock)
		r, err := Load(vault)
		if err != nil {
			t.Fatalf("Load: %v", err)
		}
		if r.IsPackagedDefault {
			t.Error("the embedded default won over a present vault file")
		}
	})

	t.Run("the split layout is probed from the memory root", func(t *testing.T) {
		clearEnv(t)
		vault := t.TempDir()
		memoryRoot := filepath.Join(vault, "Agent")
		if err := os.MkdirAll(memoryRoot, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.MkdirAll(filepath.Join(vault, "standards"), 0o755); err != nil {
			t.Fatal(err)
		}
		writeRules(t, filepath.Join(vault, "standards"), validBlock)
		r, err := Load(memoryRoot)
		if err != nil {
			t.Fatalf("Load: %v", err)
		}
		if r.IsPackagedDefault {
			t.Error("the sibling probe did not find the rules file")
		}
	})

	t.Run("an absent vault file falls through", func(t *testing.T) {
		clearEnv(t)
		r, err := Load(t.TempDir())
		if err != nil {
			t.Fatalf("Load: %v", err)
		}
		if !r.IsPackagedDefault {
			t.Error("absence did not fall through to the embedded default")
		}
	})

	t.Run("a corrupt vault file halts and never falls back", func(t *testing.T) {
		clearEnv(t)
		vault := t.TempDir()
		if err := os.MkdirAll(filepath.Join(vault, "standards"), 0o755); err != nil {
			t.Fatal(err)
		}
		writeRules(t, filepath.Join(vault, "standards"), "memory_types: [unclosed\n")
		if _, err := Load(vault); err == nil {
			t.Fatal("a corrupt rules file fell back to the embedded default — that is " +
				"the improvising the fail-closed rule exists to stop")
		}
	})

	t.Run("the override wins over both", func(t *testing.T) {
		dir := t.TempDir()
		path := writeRules(t, dir, strings.Replace(validBlock,
			"record_kinds: [brief, telemetry]", "record_kinds: [brief]", 1))
		t.Setenv("AGENTM_STORAGE_RULES", path)
		r, err := Load("")
		if err != nil {
			t.Fatalf("Load: %v", err)
		}
		if len(r.RecordKinds) != 1 {
			t.Errorf("record kinds = %v; the override did not win", r.RecordKinds)
		}
	})

	t.Run("an override pointing at nothing is absence, not corruption", func(t *testing.T) {
		t.Setenv("AGENTM_STORAGE_RULES", filepath.Join(t.TempDir(), "nope.md"))
		r, err := Load("")
		if err != nil {
			t.Fatalf("Load: %v", err)
		}
		if !r.IsPackagedDefault {
			t.Error("a dangling override did not fall through")
		}
	})
}

// The hash identifies the contract a judgment was made under, so it has to be
// stable against everything that is not a change to what the block says.
func TestContentHash(t *testing.T) {
	dir := t.TempDir()
	base, err := LoadFile(writeRules(t, dir, validBlock))
	if err != nil {
		t.Fatal(err)
	}

	t.Run("prose edits do not change it", func(t *testing.T) {
		path := filepath.Join(t.TempDir(), "storage-rules.md")
		body := "# Rules\n\nEntirely different prose.\n\n```storage-rules\n" + validBlock + "```\n"
		if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
		other, err := LoadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		if other.Hash != base.Hash {
			t.Error("rewording the prose invalidated every judgment in the corpus")
		}
	})

	t.Run("reordering a list does change it", func(t *testing.T) {
		reordered := strings.Replace(validBlock, "record_kinds: [brief, telemetry]",
			"record_kinds: [telemetry, brief]", 1)
		other, err := LoadFile(writeRules(t, t.TempDir(), reordered))
		if err != nil {
			t.Fatal(err)
		}
		if other.Hash == base.Hash {
			t.Error("a list order change should be visible; the hash is over parsed content, " +
				"and a slice preserves order")
		}
	})

	t.Run("changing what the block says changes it", func(t *testing.T) {
		changed := strings.Replace(validBlock, "low_confidence: 0.65", "low_confidence: 0.8", 1)
		other, err := LoadFile(writeRules(t, t.TempDir(), changed))
		if err != nil {
			t.Fatal(err)
		}
		if other.Hash == base.Hash {
			t.Error("a threshold change left the hash alone")
		}
	})
}

// The shipped default is the contract every fresh install runs on, so it gets the
// same scrutiny a vault instance would.
func TestPackagedDefault(t *testing.T) {
	clearEnv(t)
	r, err := Load("")
	if err != nil {
		t.Fatalf("the embedded default does not parse: %v", err)
	}
	if !r.IsPackagedDefault {
		t.Fatal("Load with no vault did not reach the embedded default")
	}

	want := map[string]bool{
		"preference": true, "convention": true, "reference": true,
		"workflow": true, "fix": true, "idea": true,
	}
	if len(r.MemoryTypes) != len(want) {
		t.Errorf("memory types = %v, want the design's six", r.MemoryTypes)
	}
	for _, tp := range r.MemoryTypes {
		if !want[tp] {
			t.Errorf("unexpected memory type %q", tp)
		}
	}
	for _, c := range append(append([]string{}, ObservationalClasses...), DerivedClasses...) {
		if _, ok := r.Classes[c]; !ok {
			t.Errorf("class %q is missing from the shipped contract", c)
		}
	}
	if len(r.Deprecations) == 0 {
		t.Error("the shipped contract retires nothing, so the collapse has no map to run from")
	}

	// The v2 vocabulary is optional in an arbitrary contract (absence is the
	// pre-v2 state, tolerated while the migration runs) but required in the one
	// the repo ships — a fresh install starts on the current design, not the
	// previous one.
	wantLifecycle := []string{"pinned", "active", "dormant", "archived", "superseded"}
	if len(r.Lifecycles) != len(wantLifecycle) {
		t.Errorf("shipped lifecycle axis = %v, want the design's five", r.Lifecycles)
	}
	for _, v := range wantLifecycle {
		if !r.IsLifecycle(v) {
			t.Errorf("shipped contract is missing lifecycle value %q", v)
		}
	}
	if r.IsLifecycle("expired") {
		t.Error("`expired` survived into the shipped lifecycle axis; it was retired as a data-quality artifact")
	}
	if r.DefaultLifecycle != "active" {
		t.Errorf("shipped default lifecycle = %q, want active", r.DefaultLifecycle)
	}
	for _, transport := range []string{"operator-direct", "conversation", "external-fetch", "email"} {
		if _, ok := r.SourceTier(transport); !ok {
			t.Errorf("shipped contract is missing source transport %q", transport)
		}
	}
	if tier, _ := r.SourceTier("external-fetch"); tier != "untrusted" {
		t.Error("external-fetch must ship untrusted — screening cannot grade plausible content")
	}
	wantFacets := []string{"meetings", "correspondence", "docs", "diary"}
	if len(r.Facets) != len(wantFacets) {
		t.Errorf("shipped facets = %v, want the ruled four", r.Facets)
	}
	for _, f := range wantFacets {
		if !r.IsFacet(f) {
			t.Errorf("shipped contract is missing facet %q", f)
		}
	}
	if class, ok := r.ClassFor("idea"); !ok || class != "semantic" {
		t.Errorf("ClassFor(idea) = %q, %v; the v2 contract routes idea to memory/semantic", class, ok)
	}
	for _, kind := range []string{"calendar-facet", "day-index", "calendar-review"} {
		if !r.IsRecordKind(kind) {
			t.Errorf("shipped contract is missing calendar record kind %q", kind)
		}
	}
}
