package index

import (
	"path/filepath"
	"testing"
)

// The memory-root trims (agentm-vault plan 05) moved the recall-access sidecar
// into the engine state directory. An Index opened with the sidecar dir reads
// it from there first and from the memory root only while no copy exists in
// the engine directory — so a vault on either side of the move ranks off the
// same anchors, and a stale copy left behind in the vault is not the one read.
func TestOpenWithSidecarReadsTheEngineDirFirst(t *testing.T) {
	dir := t.TempDir()
	vault := filepath.Join(dir, "vault")
	engine := filepath.Join(dir, "engine")
	writeLifecycle(t, vault, map[string]string{"legacy-slug": "2026-09-01"})
	writeLifecycle(t, engine, map[string]string{"engine-slug": "2026-09-10"})

	idx, err := OpenWithSidecar(filepath.Join(dir, "index.db"), vault, "", engine, true)
	if err != nil {
		t.Fatalf("OpenWithSidecar: %v", err)
	}
	defer idx.Close()
	log := idx.accessLog()
	if got := log.SidecarPath(); got != filepath.Join(engine, ".lifecycle.json") {
		t.Fatalf("sidecar read from %s, want the engine directory", got)
	}
	if _, ok := log.LastAccess("engine-slug"); !ok {
		t.Fatal("the engine copy's entry is missing")
	}
	if _, ok := log.LastAccess("legacy-slug"); ok {
		t.Fatal("the stale vault copy was read")
	}
}

func TestOpenWithSidecarFallsBackToTheMemoryRoot(t *testing.T) {
	dir := t.TempDir()
	vault := filepath.Join(dir, "vault")
	engine := filepath.Join(dir, "engine")
	writeLifecycle(t, filepath.Join(vault, "Agent"), map[string]string{"legacy-slug": "2026-09-01"})

	idx, err := OpenWithSidecar(filepath.Join(dir, "index.db"), vault, "Agent", engine, true)
	if err != nil {
		t.Fatalf("OpenWithSidecar: %v", err)
	}
	defer idx.Close()
	log := idx.accessLog()
	if got := log.SidecarPath(); got != filepath.Join(vault, "Agent", ".lifecycle.json") {
		t.Fatalf("sidecar read from %s, want the memory root while the engine dir holds none", got)
	}
	if _, ok := log.LastAccess("legacy-slug"); !ok {
		t.Fatal("the pre-move copy was not read")
	}
}
