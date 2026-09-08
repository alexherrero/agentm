package capture

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// The synthetic cases above pin the resolution; this one checks it against the
// two live inputs that actually combined into the bug — this machine's
// configured memory space and this machine's filing contract. It is skipped
// wherever either is absent, so it costs CI nothing and is a real check on the
// machine the defect was found on.
//
// Reading them is the point: a fix verified only against values the test itself
// chose proves the resolver is self-consistent, not that it resolves *this*
// install's routing into *this* install's space.
func TestClassDirAgainstTheLiveConfiguredSpaceAndContract(t *testing.T) {
	home, err := os.UserHomeDir()
	if err != nil {
		t.Skip("no home directory")
	}
	blob, err := os.ReadFile(filepath.Join(home, ".claude", ".agentm-config.json"))
	if err != nil {
		t.Skip("no live agentm config on this machine")
	}
	var cfg map[string]any
	if err := json.Unmarshal(blob, &cfg); err != nil {
		t.Skipf("live config does not parse: %v", err)
	}
	spaces, _ := cfg["daemon.spaces"].(map[string]any)
	space, _ := spaces["memory"].(string)
	vault, _ := cfg["plugins.obsidian-vault.vault_path"].(string)
	if space == "" || vault == "" {
		t.Skip("live config names no memory space or vault path")
	}

	contract, err := rules.NewHolder(vault, time.Now()).Get()
	if err != nil {
		t.Skipf("live contract does not resolve: %v", err)
	}
	if len(contract.Routing) == 0 {
		t.Skip("live contract routes nothing")
	}

	for noteType, routed := range contract.Routing {
		got := classDir(contract, nil, noteType, space)
		if got == "" {
			t.Errorf("live contract routes %q to %q and classDir placed it nowhere",
				noteType, routed)
			continue
		}
		if got != space && !strings.HasPrefix(got, space+"/") {
			t.Errorf("%q -> %q resolved to %q, outside the live space %q",
				noteType, routed, got, space)
		}
		// The specific shape of the defect: the space's own last segment
		// repeated, e.g. Agent/memory/memory/semantic.
		if leaf := filepath.Base(space); strings.Contains(got, "/"+leaf+"/"+leaf+"/") {
			t.Errorf("%q -> %q resolved to %q — the space's own name is doubled",
				noteType, routed, got)
		}
	}
}
