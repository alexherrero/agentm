package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The per-invocation override names the MEMORY root — one name, one meaning
// (operator ruling 2026-09-11) — and the vault root is derived from it. Until
// then the daemon read the same value as the vault root, so under the runner,
// which exports the memory root, every class directory was sought one level
// too deep and the nightly batch found nothing to judge.

// loadUnderEnv writes a kernel config holding the given keys and loads it with
// exactly the given environment (both override names cleared first).
func loadUnderEnv(t *testing.T, keys map[string]any, env map[string]string) (*Config, error) {
	t.Helper()
	for _, n := range []string{"MEMORY_ROOT", "MEMORY_VAULT_PATH", "AGENTM_STORAGE_RULES"} {
		t.Setenv(n, "")
		os.Unsetenv(n)
	}
	for k, v := range env {
		t.Setenv(k, v)
	}
	if keys == nil {
		keys = map[string]any{}
	}
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

// nestedVault lays out <tmp>/Vault/Agent and returns both directories.
func nestedVault(t *testing.T) (vault, memory string) {
	t.Helper()
	vault = filepath.Join(t.TempDir(), "Vault")
	memory = filepath.Join(vault, "Agent")
	if err := os.MkdirAll(memory, 0o755); err != nil {
		t.Fatal(err)
	}
	return vault, memory
}

func TestTheMemoryRootExportYieldsTheVaultAboveIt(t *testing.T) {
	vault, memory := nestedVault(t)
	elsewhere := t.TempDir()
	cfg, err := loadUnderEnv(t, map[string]any{
		"plugins.obsidian-vault.vault_path":  elsewhere,
		"plugins.obsidian-vault.memory_root": "Agent",
	}, map[string]string{"MEMORY_ROOT": memory})
	if err != nil {
		t.Fatal(err)
	}
	if cfg.VaultPath != vault {
		t.Fatalf("vault = %q, want the directory above the export, %q", cfg.VaultPath, vault)
	}
	if cfg.MemoryRoot != "Agent" {
		t.Fatalf("memory root = %q, want the configured prefix kept", cfg.MemoryRoot)
	}
	if got := cfg.Spaces["memory"]; got != "Agent/memory" {
		t.Fatalf("memory space = %q, want it under the prefix", got)
	}
	if !strings.Contains(cfg.VaultSource, "$MEMORY_ROOT") || !strings.Contains(cfg.VaultSource, "memory root") {
		t.Fatalf("source %q does not say the export was read as the memory root", cfg.VaultSource)
	}
}

func TestAFlatExportIsBothRootsAtOnce(t *testing.T) {
	scratch := t.TempDir()
	cfg, err := loadUnderEnv(t, map[string]any{
		"plugins.obsidian-vault.memory_root": "Agent",
	}, map[string]string{"MEMORY_ROOT": scratch})
	if err != nil {
		t.Fatal(err)
	}
	if cfg.VaultPath != filepath.Clean(scratch) {
		t.Fatalf("vault = %q, want the export itself, %q", cfg.VaultPath, scratch)
	}
	if cfg.MemoryRoot != "" {
		t.Fatalf("memory root = %q, want empty: the export does not end in the prefix, "+
			"so walking <export>/Agent would find nothing", cfg.MemoryRoot)
	}
	if got := cfg.Spaces["memory"]; got != "memory" {
		t.Fatalf("memory space = %q, want it at the top of the flat layout", got)
	}
	if !strings.Contains(cfg.VaultSource, "flat") {
		t.Fatalf("source %q does not say the layout is flat", cfg.VaultSource)
	}
}

func TestTheDeprecatedNameStillMeansTheMemoryRoot(t *testing.T) {
	vault, memory := nestedVault(t)
	cfg, err := loadUnderEnv(t, map[string]any{
		"plugins.obsidian-vault.memory_root": "Agent",
	}, map[string]string{"MEMORY_VAULT_PATH": memory})
	if err != nil {
		t.Fatal(err)
	}
	if cfg.VaultPath != vault || cfg.MemoryRoot != "Agent" {
		t.Fatalf("vault = %q, memory root = %q; the alias must resolve exactly as the new name",
			cfg.VaultPath, cfg.MemoryRoot)
	}
	if !strings.Contains(cfg.VaultSource, "$MEMORY_VAULT_PATH") {
		t.Fatalf("source %q does not name the variable that actually spoke", cfg.VaultSource)
	}
}

func TestTheNewNameWinsWhenBothAreSet(t *testing.T) {
	vault, memory := nestedVault(t)
	_, stale := nestedVault(t)
	cfg, err := loadUnderEnv(t, map[string]any{
		"plugins.obsidian-vault.memory_root": "Agent",
	}, map[string]string{"MEMORY_ROOT": memory, "MEMORY_VAULT_PATH": stale})
	if err != nil {
		t.Fatal(err)
	}
	if cfg.VaultPath != vault {
		t.Fatalf("vault = %q, want the one under $MEMORY_ROOT, %q", cfg.VaultPath, vault)
	}
}

func TestTheVaultFlagBeatsTheExport(t *testing.T) {
	_, memory := nestedVault(t)
	flagged := t.TempDir()
	for _, n := range []string{"MEMORY_ROOT", "MEMORY_VAULT_PATH", "AGENTM_STORAGE_RULES"} {
		t.Setenv(n, "")
		os.Unsetenv(n)
	}
	t.Setenv("MEMORY_ROOT", memory)
	blob, _ := json.Marshal(map[string]any{"plugins.obsidian-vault.memory_root": "Agent"})
	path := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(path, blob, 0o644); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(Options{ConfigPath: path, VaultPath: flagged})
	if err != nil {
		t.Fatal(err)
	}
	if cfg.VaultPath != filepath.Clean(flagged) || cfg.VaultSource != "--vault flag" {
		t.Fatalf("vault = %q from %q; the flag must win outright", cfg.VaultPath, cfg.VaultSource)
	}
	if cfg.MemoryRoot != "Agent" {
		t.Fatalf("memory root = %q; the configured prefix stands when the flag is given", cfg.MemoryRoot)
	}
}

func TestWithNoConfiguredPrefixTheExportIsTheVault(t *testing.T) {
	_, memory := nestedVault(t)
	cfg, err := loadUnderEnv(t, nil, map[string]string{"MEMORY_ROOT": memory})
	if err != nil {
		t.Fatal(err)
	}
	if cfg.VaultPath != memory || cfg.MemoryRoot != "" {
		t.Fatalf("vault = %q, memory root = %q; with no prefix to take off, "+
			"the export is the vault", cfg.VaultPath, cfg.MemoryRoot)
	}
}

func TestSplitMemoryRootNeverStripsToNothing(t *testing.T) {
	vault, rel := splitMemoryRoot(string(filepath.Separator)+"Agent", "Agent")
	if rel != "" || vault != string(filepath.Separator)+"Agent" {
		t.Fatalf("got %q / %q; an export that IS the prefix has nothing above it", vault, rel)
	}
}
