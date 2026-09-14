package dreaming

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
)

// Run hands the root map the year maps the calendar job plans the same night
// (agentm-vault plan 07 review, finding 6). On a year's first night its map is
// planned and not yet on disk, so a root map planned from the disk alone leaves
// it out until the night after, and every other test stays green.
func TestTonightsRootMapListsTheYearMapPlannedTonight(t *testing.T) {
	_, vault := rootMapVault(t)
	writeAt(t, vault, "Calendar/2026/2026-09-01-docs.md", facetBody)
	cfg := &config.Config{VaultPath: vault, MemoryRoot: "Agent", EngineStateDir: filepath.Join(t.TempDir(), "state")}

	rep, err := Run(cfg, Options{Now: time.Date(2026, 9, 13, 9, 0, 0, 0, time.UTC)})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(vault, "Calendar", "moc-calendar-2026.md")); !os.IsNotExist(err) {
		t.Fatalf("a report-only run wrote the year map, so the map is on disk rather than only planned")
	}
	if text := rootMapText(rep.Mocs); !strings.Contains(text, "## Calendar\n\n- [[moc-calendar-2026]]\n") {
		t.Errorf("tonight's root map does not list the year map planned tonight:\n%s", text)
	}
}
