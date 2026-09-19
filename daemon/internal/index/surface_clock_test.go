package index

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// The clock, and who is allowed to move it (agentm-vault § Surfaces).
//
// Before this the Go arm read `.lifecycle.json` and never wrote it, so a memory
// reached only over MCP or the CLI aged as if unread — the design's "a surface
// that can write writes the clock" was true of the Python hooks and of nothing
// else.
//
// The hazard that arrives with the fix is the opposite one. The retrieval gate
// runs 64 questions a night, the scorecards and the probe and the verify scripts
// and enrichment's neighbour search all query, and none of them is anybody
// reading. Counted, they would hold about three hundred notes at day zero
// forever and the axis would read healthy while measuring nothing. `measure` is
// the surface that never touches a clock, and these are the tests that hold both
// halves.

func sidecar(t *testing.T, x *Index) map[string]any {
	t.Helper()
	p := x.accessLog().SidecarPath()
	blob, err := os.ReadFile(p)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		t.Fatalf("read sidecar: %v", err)
	}
	var out map[string]any
	if err := json.Unmarshal(blob, &out); err != nil {
		t.Fatalf("parse sidecar: %v", err)
	}
	return out
}

func entries(t *testing.T, x *Index) map[string]any {
	t.Helper()
	m := sidecar(t, x)
	if m == nil {
		return map[string]any{}
	}
	e, _ := m["entries"].(map[string]any)
	if e == nil {
		return map[string]any{}
	}
	return e
}

func seedOne(t *testing.T, x *Index) string {
	t.Helper()
	rel := "agent/memory/semantic/zorbulax.md"
	writeNote(t, x.vault, rel,
		"---\ntitle: Zorbulax\n---\n\nThe zorbulax subsystem is a thing.\n")
	if _, err := x.Reconcile(); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	return rel
}

func TestAnMCPHitMovesTheClock(t *testing.T) {
	x := newTestIndex(t)
	rel := seedOne(t, x)

	out, err := x.Search(Query{Text: "zorbulax", K: 5, Surface: "mcp:claude-desktop"})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) != 1 {
		t.Fatalf("the fixture never indexed: %v", resultPaths(out.Results))
	}
	got := entries(t, x)
	if _, ok := got[rel]; !ok {
		t.Fatalf("no clock written for %q; sidecar holds %v", rel, keysOf(got))
	}
}

func TestACLIHitMovesTheClock(t *testing.T) {
	x := newTestIndex(t)
	rel := seedOne(t, x)

	if _, err := x.Search(Query{Text: "zorbulax", K: 5, Surface: note.SurfaceCLI}); err != nil {
		t.Fatalf("search: %v", err)
	}
	if _, ok := entries(t, x)[rel]; !ok {
		t.Fatal("a CLI hit left the clock alone")
	}
}

// The empty surface is the CLI: it is the one caller that can reach the search
// path without saying who it is, and a person in a terminal is reading.
func TestAnUnnamedSurfaceIsTheCLI(t *testing.T) {
	x := newTestIndex(t)
	rel := seedOne(t, x)

	if _, err := x.Search(Query{Text: "zorbulax", K: 5}); err != nil {
		t.Fatalf("search: %v", err)
	}
	if _, ok := entries(t, x)[rel]; !ok {
		t.Fatal("an unlabelled search left the clock alone")
	}
}

// The operator's own criterion for this task, and the one that matters most: a
// gate run moves no clock. Sixty-four questions is what the retrieval gate
// actually runs each night.
func TestAGateRunMovesNoClock(t *testing.T) {
	x := newTestIndex(t)
	seedOne(t, x)

	for i := 0; i < 64; i++ {
		if _, err := x.Search(Query{Text: "zorbulax", K: 5, Surface: note.SurfaceMeasure}); err != nil {
			t.Fatalf("search %d: %v", i, err)
		}
	}
	if got := entries(t, x); len(got) != 0 {
		t.Fatalf("64 measurement queries wrote %d clock(s): %v", len(got), keysOf(got))
	}
	if _, err := os.Stat(x.accessLog().SidecarPath()); err == nil {
		t.Error("a measurement run created the sidecar; it should not have touched it")
	}
}

func TestAGateRunWritesNoLedgerRow(t *testing.T) {
	x := newTestIndex(t)
	seedOne(t, x)
	ledger := filepath.Join(t.TempDir(), "recall-history.jsonl")
	t.Setenv("AGENTM_RECALL_HISTORY", ledger)

	if _, err := x.Search(Query{Text: "zorbulax", K: 5, Surface: note.SurfaceMeasure}); err != nil {
		t.Fatalf("search: %v", err)
	}
	if _, err := os.Stat(ledger); err == nil {
		t.Error("a measurement query wrote a recall row")
	}
}

func TestAServedHitWritesItsSurfaceToTheLedger(t *testing.T) {
	x := newTestIndex(t)
	seedOne(t, x)
	ledger := filepath.Join(t.TempDir(), "recall-history.jsonl")
	t.Setenv("AGENTM_RECALL_HISTORY", ledger)

	// A query whose words are not any note's slug, so "is the query text in the
	// row" is a question about the query rather than about `hit_slugs` — which
	// legitimately carries the slug of every note served.
	const secret = "quixotic"
	writeNote(t, x.vault, "agent/memory/semantic/zorbulax-two.md",
		"---\ntitle: Two\n---\n\nThe quixotic subsystem.\n")
	if _, err := x.Reconcile(); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	for _, surface := range []string{"mcp:claude-code", note.SurfaceCLI} {
		if _, err := x.Search(Query{Text: secret, K: 5, Surface: surface}); err != nil {
			t.Fatalf("search %s: %v", surface, err)
		}
	}
	blob, err := os.ReadFile(ledger)
	if err != nil {
		t.Fatalf("no ledger written: %v", err)
	}
	lines := strings.Split(strings.TrimSpace(string(blob)), "\n")
	if len(lines) != 2 {
		t.Fatalf("expected two rows, got %d: %s", len(lines), blob)
	}
	want := []string{"mcp:claude-code", "cli"}
	for i, line := range lines {
		var row map[string]any
		if err := json.Unmarshal([]byte(line), &row); err != nil {
			t.Fatalf("row %d is not JSON: %v", i, err)
		}
		if row["surface"] != want[i] {
			t.Errorf("row %d surface = %v, want %q", i, row["surface"], want[i])
		}
		// The standing contract of this file: the query is hashed, never stored.
		// `hit_slugs` carries slugs and is meant to; the *query* must not be
		// anywhere in the row.
		if s, _ := row["query_hash"].(string); s == "" {
			t.Errorf("row %d carries no query_hash: %s", i, line)
		}
		if strings.Contains(line, secret) {
			t.Errorf("row %d leaked the query text: %s", i, line)
		}
		if row["hit_count"].(float64) < 1 {
			t.Errorf("row %d hit_count = %v, want at least 1", i, row["hit_count"])
		}
	}
}

// A note served twice on one day is one day's worth of recall. The sidecar's
// resolution is a date, so a second write would change nothing and rewriting the
// whole file for every hit of every search would be a real cost for no
// information.
func TestASecondHitTheSameDayRewritesNothing(t *testing.T) {
	x := newTestIndex(t)
	seedOne(t, x)

	if _, err := x.Search(Query{Text: "zorbulax", K: 5, Surface: note.SurfaceCLI}); err != nil {
		t.Fatalf("first search: %v", err)
	}
	p := x.accessLog().SidecarPath()
	first, err := os.Stat(p)
	if err != nil {
		t.Fatalf("stat: %v", err)
	}
	// A stat's mtime resolution is coarse enough that "unchanged" needs the
	// bytes, not the timestamp.
	before, _ := os.ReadFile(p)
	if _, err := x.Search(Query{Text: "zorbulax", K: 5, Surface: note.SurfaceCLI}); err != nil {
		t.Fatalf("second search: %v", err)
	}
	after, _ := os.ReadFile(p)
	if string(before) != string(after) {
		t.Errorf("the same day's second recall rewrote the sidecar:\n%s\n%s", before, after)
	}
	_ = first
}

// The bug that would have shipped silently: the reader parses `fingerprint` and
// drops it, so a writer that did not carry it forward would erase every one on
// its first hit — and the reconcile step that follows a moved note to its new
// path has nothing else to follow it by.
func TestAWritePreservesFingerprints(t *testing.T) {
	x := newTestIndex(t)
	rel := seedOne(t, x)
	other := "agent/memory/semantic/elsewhere.md"

	p := x.accessLog().SidecarPath()
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	seed := `{"version":2,"entries":{"` + other +
		`":{"last_access":"2026-01-01","fingerprint":"deadbeef"}}}`
	if err := os.WriteFile(p, []byte(seed), 0o644); err != nil {
		t.Fatal(err)
	}
	x.accessLog().Refresh()

	if _, err := x.Search(Query{Text: "zorbulax", K: 5, Surface: note.SurfaceCLI}); err != nil {
		t.Fatalf("search: %v", err)
	}
	got := entries(t, x)
	kept, ok := got[other].(map[string]any)
	if !ok {
		t.Fatalf("the untouched entry is gone: %v", keysOf(got))
	}
	if kept["fingerprint"] != "deadbeef" {
		t.Errorf("fingerprint = %v, want deadbeef — a write erased it", kept["fingerprint"])
	}
	if _, ok := got[rel]; !ok {
		t.Error("the served note got no clock")
	}
}

func TestTheWrittenSidecarIsVersionTwo(t *testing.T) {
	x := newTestIndex(t)
	seedOne(t, x)
	if _, err := x.Search(Query{Text: "zorbulax", K: 5, Surface: note.SurfaceCLI}); err != nil {
		t.Fatalf("search: %v", err)
	}
	if v := sidecar(t, x)["version"]; v != float64(2) {
		t.Errorf("version = %v, want 2 — version 1 keys on a slug, and 26 slugs "+
			"in the live file matched more than one file", v)
	}
}

// A note the wall removed was not served, so its clock must not move: the design
// says the clock resets on any hit *served* to any surface, and a row that was
// dropped before the caller saw it is not one.
func TestAWalledNoteGetsNoClock(t *testing.T) {
	x := newTestIndex(t)
	withWall(t, []string{"personal/Home/Important Docs"})
	walled := "personal/Home/Important Docs/Recovery Codes.md"
	writeNote(t, x.vault, walled,
		"---\ntitle: Codes\n---\n\nThe zorbulax recovery codes.\n")
	seedOne(t, x)

	if _, err := x.Search(Query{Text: "zorbulax", K: 5, Surface: note.SurfaceCLI}); err != nil {
		t.Fatalf("search: %v", err)
	}
	if _, ok := entries(t, x)[walled]; ok {
		t.Error("a walled note got a clock; it was never served")
	}
}

func keysOf(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

var _ = time.Now
