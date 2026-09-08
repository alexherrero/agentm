package probe

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// A daemon that captures and finds its own note but files it in the wrong place
// is broken in the one way the probe existed to catch and could not: a routing
// bug put every probe under a second class tree for eight weeks, the index
// walked that path as readily as the real one, and the round trip passed on
// every run. These tests pin the placement check, which is the half that was
// missing — the round trip alone cannot see where it landed.

// fakeDaemon answers the two MCP calls a probe makes. `capturePath` is what it
// claims the capture wrote, which is the whole variable under test; search
// always finds that path, so a failure can only be about placement.
type fakeDaemon struct {
	capturePath string
	calls       []string
}

func (f *fakeDaemon) handler(t *testing.T) http.HandlerFunc {
	t.Helper()
	return func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Params struct {
				Name string `json:"name"`
			} `json:"params"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Errorf("undecodable request: %v", err)
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		f.calls = append(f.calls, req.Params.Name)

		var structured map[string]any
		switch req.Params.Name {
		case "memory_capture":
			structured = map[string]any{"path": f.capturePath}
		case "memory_search":
			structured = map[string]any{
				"results": []any{map[string]any{"path": f.capturePath}},
			}
		default:
			t.Errorf("the probe called an unexpected tool: %q", req.Params.Name)
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"jsonrpc": "2.0",
			"id":      1,
			"result":  map[string]any{"structuredContent": structured},
		})
	}
}

// newProbeHarness builds a runner against a vault whose memory space sits at
// `Agent/memory` — the live shape, and the one the resolution bug needed. The
// note the fake daemon claims to have written is created on disk, because the
// probe checks the file exists before it believes any search.
func newProbeHarness(t *testing.T, capturePath string) (*Runner, *fakeDaemon) {
	t.Helper()
	dir := t.TempDir()
	vault := filepath.Join(dir, "vault")
	if err := os.MkdirAll(filepath.Join(vault, "Agent", "memory"), 0o755); err != nil {
		t.Fatal(err)
	}
	abs := filepath.Join(vault, filepath.FromSlash(capturePath))
	if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(abs, []byte("---\ntitle: probe\n---\n\nbody\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	holder := rules.NewHolder("", time.Now())
	if _, err := holder.Get(); err != nil {
		t.Fatalf("the shipped filing contract does not resolve: %v", err)
	}
	cfg := &config.Config{
		VaultPath: vault,
		IndexPath: filepath.Join(dir, "index.db"),
		StateDir:  filepath.Join(dir, "state"),
		Rules:     holder,
		Spaces:    map[string]string{"memory": "Agent/memory"},
	}
	idx, err := index.Open(cfg.IndexPath, cfg.VaultPath, cfg.MemoryRoot, cfg.DecayEnabled)
	if err != nil {
		t.Fatalf("index.Open: %v", err)
	}
	t.Cleanup(func() { idx.Close() })

	fake := &fakeDaemon{capturePath: capturePath}
	srv := httptest.NewServer(fake.handler(t))
	t.Cleanup(srv.Close)

	return New(cfg, idx, srv.URL, nil), fake
}

// The exact path the live daemon wrote on 2026-09-07. Findable, on disk, and
// one directory deeper than it belongs.
const misrootedPath = "Agent/memory/memory/semantic/agentm-self-probe-2026-09-07t08-09-04z.md"

func TestProbeFailsWhenTheCaptureLandsOutsideItsClass(t *testing.T) {
	r, fake := newProbeHarness(t, misrootedPath)

	st, err := r.Run(time.Now())
	if err == nil {
		t.Fatal("a probe filed under a second class tree reported success — the " +
			"round trip passing is exactly how this hid for eight weeks")
	}
	if st.OK {
		t.Error("the recorded state says OK on a failed run")
	}
	if !strings.Contains(st.Detail, misrootedPath) {
		t.Errorf("the failure does not name the path it objected to: %q", st.Detail)
	}
	if !strings.Contains(st.Detail, "Agent/memory/semantic/") {
		t.Errorf("the failure does not say where the note belonged: %q", st.Detail)
	}
	// Placement is checked before the sideways questions: a note in the wrong
	// place is a finding whether or not the index can echo it back.
	for _, call := range fake.calls {
		if call == "memory_search" {
			t.Error("the probe went on to search after the placement check failed")
		}
	}
}

func TestProbeAcceptsTheCaptureInItsClass(t *testing.T) {
	const good = "Agent/memory/semantic/agentm-self-probe-2026-09-07t08-09-04z.md"
	r, _ := newProbeHarness(t, good)

	st, err := r.Run(time.Now())
	if err != nil {
		t.Fatalf("a correctly filed probe failed: %v", err)
	}
	if !st.OK {
		t.Errorf("the recorded state is not OK: %q", st.Detail)
	}
	if st.Path != good {
		t.Errorf("recorded path %q, want %q", st.Path, good)
	}
}

// The prefix is resolved through the contract rather than named here, so a
// routing change moves the probe's expectation with it instead of turning the
// probe into a second, staler copy of the routing table.
func TestClassPrefixTracksTheContractsRouting(t *testing.T) {
	r, _ := newProbeHarness(t, "Agent/memory/semantic/x.md")

	got, err := r.classPrefix()
	if err != nil {
		t.Fatal(err)
	}
	if got != "Agent/memory/semantic/" {
		t.Fatalf("classPrefix() = %q, want Agent/memory/semantic/", got)
	}

	contract, err := r.cfg.Rules.Get()
	if err != nil {
		t.Fatal(err)
	}
	if routed := contract.Routing[probeType]; !strings.HasSuffix(strings.Trim(got, "/"), routed) {
		t.Errorf("classPrefix %q does not end in the contract's routing for %q (%q)",
			got, probeType, routed)
	}
}
