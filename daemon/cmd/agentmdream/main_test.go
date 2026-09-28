package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/config"
)

// A rehearsal against a copy of the vault must not write the live engine
// state: `-vault` moves the vault and nothing else, so the copy's moves would be
// journaled where the live night replays them.
func TestMoveTasksRefusesACopyOfTheVaultOnTheLiveState(t *testing.T) {
	live, copy := t.TempDir(), t.TempDir()
	cfgPath := filepath.Join(t.TempDir(), "config.json")
	blob, _ := json.Marshal(map[string]any{"plugins.obsidian-vault.vault_path": live})
	if err := os.WriteFile(cfgPath, blob, 0o644); err != nil {
		t.Fatal(err)
	}
	opts := config.Options{ConfigPath: cfgPath, VaultPath: copy}

	t.Setenv("AGENTM_STATE_DIR", "")
	err := refuseAStrangeVaultOnLiveState(opts, &config.Config{VaultPath: copy})
	if err == nil || !strings.Contains(err.Error(), "AGENTM_STATE_DIR") {
		t.Fatalf("a copy on the live state was not refused: %v", err)
	}
	if err := refuseAStrangeVaultOnLiveState(opts, &config.Config{VaultPath: live}); err != nil {
		t.Errorf("the configured vault was refused: %v", err)
	}
	t.Setenv("AGENTM_STATE_DIR", t.TempDir())
	if err := refuseAStrangeVaultOnLiveState(opts, &config.Config{VaultPath: copy}); err != nil {
		t.Errorf("a copy with its own state was refused: %v", err)
	}
}
