package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The default `memory` space had no test at all, which is how it came to point
// at the one directory no background pass may read and stayed there through two
// vault moves.

// vaultWithContract writes a vault holding a filing contract whose
// model_exempt_spaces are the ones named, and returns its path.
func vaultWithContract(t *testing.T, exempt ...string) string {
	t.Helper()
	vault := t.TempDir()
	if err := os.MkdirAll(filepath.Join(vault, "standards"), 0o755); err != nil {
		t.Fatal(err)
	}
	list := ""
	for _, e := range exempt {
		list += "  - " + e + "\n"
	}
	if list == "" {
		list = "  []\n"
	}
	body := "# Storage rules\n\n```storage-rules\n" +
		"classes:\n  semantic: Facts.\n  procedural: How.\n  episodic: Traces.\n" +
		"  entities: Referents.\n  crystallized: Lessons.\n  mocs: Maps.\n" +
		"memory_types: [preference]\ndefault_type: preference\n" +
		"routing:\n  preference: memory/semantic\n" +
		"record_kinds: [brief]\ndeprecations: {}\n" +
		"model_exempt_spaces:\n" + list +
		"warrants: {}\nthresholds: {low_confidence: 0.65}\n```\n"
	if err := os.WriteFile(filepath.Join(vault, "standards", "storage-rules.md"),
		[]byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return vault
}

// loadWith writes a kernel config holding the given keys and loads it.
func loadWith(t *testing.T, vault string, keys map[string]any) (*Config, error) {
	t.Helper()
	t.Setenv("MEMORY_VAULT_PATH", "")
	os.Unsetenv("MEMORY_VAULT_PATH")
	t.Setenv("AGENTM_STORAGE_RULES", "")
	os.Unsetenv("AGENTM_STORAGE_RULES")

	if keys == nil {
		keys = map[string]any{}
	}
	keys["plugins.obsidian-vault.vault_path"] = vault
	blob, err := json.Marshal(keys)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(path, blob, 0o644); err != nil {
		t.Fatal(err)
	}
	return Load(Options{ConfigPath: path})
}

// The default memory space is not the space the contract forbids reading.
//
// Stated as the property rather than as a string comparison: "not personal"
// would pass the day somebody renames the private space, and the thing that
// matters is that capture does not write where no pass may read.
func TestTheDefaultMemorySpaceIsOneAPassMayRead(t *testing.T) {
	vault := vaultWithContract(t, "Personal")
	cfg, err := loadWith(t, vault, map[string]any{
		"plugins.obsidian-vault.memory_root": "Agent",
	})
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	loaded, err := cfg.Rules.Get()
	if err != nil {
		t.Fatalf("the test's own contract does not parse: %v", err)
	}
	dir := cfg.Spaces["memory"]
	if !loaded.MayReadWithModel(dir + "/a.md") {
		t.Errorf("captures default to %q, which the contract says no background "+
			"pass may read — every memory written there is invisible to "+
			"enrichment and dreaming", dir)
	}
}

// And it lands under the configured root, rather than where the vault used to
// keep it.
func TestTheDefaultSpacesFollowTheMemoryRoot(t *testing.T) {
	vault := vaultWithContract(t)
	cfg, err := loadWith(t, vault, map[string]any{
		"plugins.obsidian-vault.memory_root": "Agent",
	})
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	// Exactly what this operator's own hand-written config says, which is the
	// evidence that the derivation is the right one rather than merely a
	// different literal.
	if got := cfg.Spaces["memory"]; got != "Agent/memory" {
		t.Errorf("memory space = %q, want Agent/memory", got)
	}
	// The projects space is the one exception to "follow the memory root":
	// since filing-v2 2b it is the vault-root `Projects/`, a sibling of the
	// memory root, so the derivation names it unprefixed.
	if got := cfg.Spaces["projects"]; got != "Projects" {
		t.Errorf("projects space = %q, want Projects", got)
	}
}

// A vault with no memory root keeps the spaces at its top level rather than
// growing an empty leading segment.
func TestWithNoMemoryRootTheSpacesSitAtTheTop(t *testing.T) {
	vault := vaultWithContract(t)
	cfg, err := loadWith(t, vault, nil)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if got := cfg.Spaces["memory"]; got != "memory" {
		t.Errorf("memory space = %q, want memory", got)
	}
}

// A config that names its own spaces still wins.
func TestAConfiguredSpaceBeatsTheDerivedDefault(t *testing.T) {
	vault := vaultWithContract(t)
	cfg, err := loadWith(t, vault, map[string]any{
		"plugins.obsidian-vault.memory_root": "Agent",
		"daemon.spaces":                      map[string]any{"memory": "somewhere/else"},
	})
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if got := cfg.Spaces["memory"]; got != "somewhere/else" {
		t.Errorf("memory space = %q; the configured value was overwritten", got)
	}
}

// A config that names spaces but not `memory` is still refused, which is the
// check the derived default had to be careful not to disarm.
func TestSpacesWithoutAMemorySpaceAreStillRefused(t *testing.T) {
	vault := vaultWithContract(t)
	_, err := loadWith(t, vault, map[string]any{
		"daemon.spaces": map[string]any{"projects": "p"},
	})
	if err == nil {
		t.Fatal("a spaces map with no memory space loaded")
	}
	if !strings.Contains(err.Error(), "memory") {
		t.Errorf("error does not name the missing space: %v", err)
	}
}

// Pointing `memory` at a model-exempt space is refused rather than accepted
// quietly. This is the shape the old default had, and the reason it went
// unnoticed for two vault moves is that it works in every visible way.
func TestAMemorySpaceNoPassMayReadIsRefused(t *testing.T) {
	vault := vaultWithContract(t, "Personal")
	_, err := loadWith(t, vault, map[string]any{
		"daemon.spaces": map[string]any{"memory": "personal"},
	})
	if err == nil {
		t.Fatal("a memory space no background pass may read was accepted")
	}
	for _, want := range []string{"personal", "model_exempt_spaces", "invisible"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the error does not mention %q: %v", want, err)
		}
	}
}

// Case does not get a pass, in either direction. macOS treats `Personal/` and
// `personal/` as one directory, so a case-sensitive check here would be a hazard
// rather than a precision.
//
// Both directions, because one is not the test it looks like: a lower-case
// contract against a mixed-case config passes whether or not the *contract* side
// is folded, and a battery run showed exactly that.
func TestTheRefusalIsCaseInsensitive(t *testing.T) {
	for _, tc := range []struct{ contract, configured string }{
		{contract: "personal", configured: "Personal"},
		{contract: "Personal", configured: "personal"},
		{contract: "PERSONAL", configured: "personal"},
	} {
		vault := vaultWithContract(t, tc.contract)
		if _, err := loadWith(t, vault, map[string]any{
			"daemon.spaces": map[string]any{"memory": tc.configured},
		}); err == nil {
			t.Errorf("contract %q against configured %q was accepted; they are "+
				"one directory on this filesystem", tc.contract, tc.configured)
		}
	}
}

// A memory space nested *inside* an exempt one is caught too — the exemption is
// on the top-level space, and everything under it inherits.
func TestANestedMemorySpaceInsideAnExemptOneIsRefused(t *testing.T) {
	vault := vaultWithContract(t, "Personal")
	if _, err := loadWith(t, vault, map[string]any{
		"daemon.spaces": map[string]any{"memory": "Personal/notes"},
	}); err == nil {
		t.Error("a memory space beneath an exempt space was accepted")
	}
}

// A contract that will not parse leaves the check silent. Guessing at
// exemptions from a file that would not load would take the whole memory down
// over a misplaced colon, which is the trade loadRules explicitly refuses.
func TestABrokenContractDoesNotBlockStartup(t *testing.T) {
	vault := t.TempDir()
	if err := os.MkdirAll(filepath.Join(vault, "standards"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(vault, "standards", "storage-rules.md"),
		[]byte("# Storage rules\n\n```storage-rules\nmemory_types: [unclosed\n```\n"),
		0o644); err != nil {
		t.Fatal(err)
	}
	cfg, err := loadWith(t, vault, map[string]any{
		"daemon.spaces": map[string]any{"memory": "personal"},
	})
	if err != nil {
		t.Fatalf("a broken contract stopped the daemon starting: %v", err)
	}
	// And the contract really is broken, so this is not passing because the
	// fixture accidentally parsed.
	if _, rerr := cfg.Rules.Get(); rerr == nil {
		t.Fatal("the fixture's contract parsed; this test proves nothing")
	}
}

// A contract that exempts nothing accepts any memory space, so the refusal is
// driven by the contract rather than by a name baked into the check.
func TestWithNoExemptSpacesAnyMemorySpaceLoads(t *testing.T) {
	vault := vaultWithContract(t)
	if _, err := loadWith(t, vault, map[string]any{
		"daemon.spaces": map[string]any{"memory": "personal"},
	}); err != nil {
		t.Errorf("a memory space no contract forbids was refused: %v", err)
	}
}
