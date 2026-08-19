package rules

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

// A broken contract has to be recoverable without a restart. Without the
// re-read, "fix the block and the next cycle picks up where this one stopped" is
// false for the daemon — the halt stops being a pause and becomes an outage that
// outlives the fix.
func TestHolderPicksUpAFixWithoutARestart(t *testing.T) {
	clearEnv(t)
	vault := t.TempDir()
	standards := filepath.Join(vault, "standards")
	if err := os.MkdirAll(standards, 0o755); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(standards, "storage-rules.md")

	write := func(block string) {
		if err := os.WriteFile(path, []byte(rulesFile(block)), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	write("memory_types: [unclosed\n")
	h := NewHolder(vault, time.Unix(0, 0))

	if _, err := h.Get(); err == nil {
		t.Fatal("a broken contract read as healthy")
	}

	write(validBlock)

	// Still broken to a reader that has not refreshed: the holder is a snapshot
	// between refreshes on purpose, because capture reads it on a sub-100ms
	// budget and must never pay for a parse.
	if _, err := h.Get(); err == nil {
		t.Fatal("the holder re-read the file on Get; capture would be paying for a parse")
	}

	if _, err := h.Refresh(time.Unix(60, 0)); err != nil {
		t.Fatalf("Refresh after the fix: %v", err)
	}
	loaded, err := h.Get()
	if err != nil {
		t.Fatalf("Get after Refresh: %v", err)
	}
	if len(loaded.MemoryTypes) != 6 {
		t.Errorf("recovered contract has %d types", len(loaded.MemoryTypes))
	}
	if got := h.ResolvedAt(); !got.Equal(time.Unix(60, 0)) {
		t.Errorf("ResolvedAt = %v; a status has to say whether it is about now", got)
	}
}

// The reverse direction matters too: a contract that was fine and then broke has
// to stop being reported as fine.
func TestHolderNoticesABreak(t *testing.T) {
	clearEnv(t)
	vault := t.TempDir()
	standards := filepath.Join(vault, "standards")
	if err := os.MkdirAll(standards, 0o755); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(standards, "storage-rules.md")

	if err := os.WriteFile(path, []byte(rulesFile(validBlock)), 0o644); err != nil {
		t.Fatal(err)
	}
	h := NewHolder(vault, time.Unix(0, 0))
	if _, err := h.Get(); err != nil {
		t.Fatalf("a valid contract read as broken: %v", err)
	}

	if err := os.WriteFile(path, []byte(rulesFile("memory_types: [unclosed\n")), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := h.Refresh(time.Unix(60, 0)); err == nil {
		t.Fatal("a contract that broke still reads as healthy")
	}
	if _, err := h.Get(); err == nil {
		t.Fatal("Get did not carry the break forward")
	}
}

// A daemon must not refuse to start over a misplaced colon: that takes the whole
// memory down to protect one field.
func TestNewHolderHoldsTheErrorRatherThanFailing(t *testing.T) {
	clearEnv(t)
	vault := t.TempDir()
	standards := filepath.Join(vault, "standards")
	if err := os.MkdirAll(standards, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(standards, "storage-rules.md"),
		[]byte(rulesFile("memory_types: [unclosed\n")), 0o644); err != nil {
		t.Fatal(err)
	}

	h := NewHolder(vault, time.Unix(0, 0))
	if h == nil {
		t.Fatal("NewHolder returned nil on a broken contract")
	}
	loaded, err := h.Get()
	if err == nil {
		t.Fatal("expected the held error")
	}
	if loaded != nil {
		t.Error("a broken contract produced a usable Rules; a caller would file against it")
	}
}
