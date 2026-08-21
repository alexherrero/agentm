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

// Decay has to reach the ranking the daemon actually serves.
//
// This file exists because the curve spent its whole life in a module that
// almost never ran. `lifecycle.compute_decay_score` is applied in exactly one
// place — `recall.query`, the in-process fallback for when this daemon is absent
// or slow — so a design saying "a decay score governs rank" described a path
// that rarely executed. A unit test of the curve would have passed the entire
// time. Only a test that goes through Search can tell a ported curve from a
// wired one.

func indexDated(t *testing.T, idx *Index, rel, title, updated, body string) {
	t.Helper()
	raw := "---\ntitle: " + title + "\nstatus: active\nupdated: " + updated + "\n---\n\n" + body
	n := note.Parse(rel, raw, time.Now())
	if err := idx.Upsert(n, time.Now().UnixNano(), int64(len(raw))); err != nil {
		t.Fatalf("indexing %s: %v", rel, err)
	}
}

func resultPaths(rows []Result) []string {
	out := make([]string, len(rows))
	for i, r := range rows {
		out[i] = r.Path
	}
	return out
}

// writeLifecycle plants the recall-access sidecar the daemon reads.
func writeLifecycle(t *testing.T, vault string, byslug map[string]string) {
	t.Helper()
	entries := map[string]map[string]string{}
	for slug, date := range byslug {
		entries[slug] = map[string]string{"last_access": date}
	}
	blob, err := json.Marshal(map[string]any{"version": 1, "entries": entries})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(vault, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(vault, ".lifecycle.json"), blob, 0o644); err != nil {
		t.Fatal(err)
	}
}

// openScratchDecay is openScratch with age-based demotion turned on. The plain
// openScratch leaves it off, matching the shipped configuration — see
// TestDecayIsOffUnlessAskedFor, which is the assertion that keeps that claim
// honest.
func openScratchDecay(t *testing.T) *Index {
	t.Helper()
	dir := t.TempDir()
	idx, err := Open(filepath.Join(dir, "index.db"), filepath.Join(dir, "vault"), "", true)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	t.Cleanup(func() { idx.Close() })
	return idx
}

// Landed off is a claim, so it gets a test. A ranking feature that is present in
// the binary and silent in the shipped configuration is exactly the shape this
// project keeps mistaking for a working one.
func TestDecayIsOffUnlessAskedFor(t *testing.T) {
	idx := openScratch(t)
	old := time.Now().AddDate(-4, 0, 0).Format("2006-01-02")
	body := "The staging gate runs before the deployment finishes.\n"
	indexDated(t, idx, "Agent/memory/semantic/stale.md", "Gate", old, body)

	out, err := idx.Search(Query{Text: "staging gate deployment", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) != 1 {
		t.Fatalf("expected the note, got %v", resultPaths(out.Results))
	}
	if out.Results[0].Decay != 0 {
		t.Errorf("a four-year-old note was decayed to %v with decay disabled",
			out.Results[0].Decay)
	}
}

// The property the whole port exists for: same words, different age, different
// rank. If decay is ported but not wired, both rows score identically and the
// order is whatever SQLite happened to return.
func TestAStaleNoteRanksBelowAFreshOneWithTheSameWords(t *testing.T) {
	idx := openScratchDecay(t)
	body := "The staging gate runs before the deployment finishes.\n"
	old := time.Now().AddDate(-4, 0, 0).Format("2006-01-02")
	indexDated(t, idx, "Agent/memory/semantic/stale.md", "Gate", old, body)
	indexDated(t, idx, "Agent/memory/semantic/fresh.md", "Gate",
		time.Now().Format("2006-01-02"), body)

	out, err := idx.Search(Query{Text: "staging gate deployment", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) < 2 {
		t.Fatalf("expected both notes, got %v — the fixture cannot show an ordering",
			resultPaths(out.Results))
	}
	if !strings.HasSuffix(out.Results[0].Path, "fresh.md") {
		t.Errorf("a four-year-old note outranked an identical one written today: %v",
			resultPaths(out.Results))
	}
	// And the demotion is visible rather than inferred from a number moving.
	for _, r := range out.Results {
		if strings.HasSuffix(r.Path, "stale.md") && r.Decay != note.DecayFloor {
			t.Errorf("stale.md carries decay %v, want the floor %v — the row is ranked "+
				"by an age it does not report", r.Decay, note.DecayFloor)
		}
	}
}

// Demote, never exclude — the same promise the class penalties make. A memory
// nobody has needed in four years is cold, not deleted.
func TestAStaleNoteIsStillTheAnswerWhenItIsTheOnlyOne(t *testing.T) {
	idx := openScratchDecay(t)
	old := time.Now().AddDate(-6, 0, 0).Format("2006-01-02")
	indexDated(t, idx, "Agent/memory/semantic/ancient.md", "Turkey", old,
		"Brine the turkey overnight before roasting.\n")
	indexDated(t, idx, "Agent/memory/semantic/other.md", "Other",
		time.Now().Format("2006-01-02"), "Filing is a frontmatter edit.\n")

	out, err := idx.Search(Query{Text: "brine turkey roasting", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) == 0 || !strings.HasSuffix(out.Results[0].Path, "ancient.md") {
		t.Errorf("a distinctive match aged out of reach entirely: %v",
			resultPaths(out.Results))
	}
}

// A durable note does not age, by any of the four routes into durability. The
// whole value of an incident record is being there on the one day, years later,
// when the same failure recurs — so the exemption has to survive the trip
// through Search, not just through IsDecayExempt.
//
// All four routes, because three of them are how the corpus actually expresses
// durability and only one is a frontmatter field anybody types: there is no
// `kind: decision` here, so decisions are recognised by their directory, and a
// space the contract does not govern is exempt wholesale.
func TestADurableNoteDoesNotAge(t *testing.T) {
	before := note.DecayExemptSpaces()
	note.SetDecayExemptSpaces([]string{"Personal"})
	t.Cleanup(func() { note.SetDecayExemptSpaces(before) })

	old := time.Now().AddDate(-4, 0, 0).Format("2006-01-02")
	for _, tc := range []struct {
		route string
		rel   string
		extra string
	}{
		{"kind", "Agent/memory/episodic/incident.md", "kind: failure-incident\n"},
		{"lifecycle_tier", "Agent/memory/semantic/tagged.md", "lifecycle_tier: durable\n"},
		{"decisions/ segment", "Agent/desk/projects/x/decisions/adr.md", ""},
		{"contract-exempt space", "Personal/Church/lesson.md", ""},
	} {
		t.Run(tc.route, func(t *testing.T) {
			idx := openScratchDecay(t)
			raw := "---\ntitle: Gate\nstatus: active\nupdated: " + old + "\n" + tc.extra +
				"---\n\nThe staging gate runs before deployment.\n"
			n := note.Parse(tc.rel, raw, time.Now())
			if !note.IsDecayExempt(n.Flags) {
				t.Fatalf("the fixture is not durable at all (flags %v), so this test "+
					"would pass for the wrong reason", n.Flags)
			}
			if err := idx.Upsert(n, time.Now().UnixNano(), int64(len(raw))); err != nil {
				t.Fatal(err)
			}

			out, err := idx.Search(Query{Text: "staging gate deployment", K: 5})
			if err != nil {
				t.Fatalf("search: %v", err)
			}
			if len(out.Results) != 1 {
				t.Fatalf("expected the note, got %v", resultPaths(out.Results))
			}
			if out.Results[0].Decay != 0 {
				t.Errorf("a four-year-old durable note was decayed to %v; exempt "+
					"means exempt", out.Results[0].Decay)
			}
		})
	}
}

// A genuine recall resets the clock. The sidecar is what separates "someone
// needed this last week" from "a lint walk touched it", and it has to outrank
// the file's own stamp or the fallback is doing all the work.
func TestARecordedRecallOutranksTheFileStamp(t *testing.T) {
	idx := openScratchDecay(t)
	old := time.Now().AddDate(-4, 0, 0).Format("2006-01-02")
	recent := time.Now().AddDate(0, -1, 0).Format("2006-01-02")
	writeLifecycle(t, idx.vault, map[string]string{"recalled": recent})

	body := "The staging gate runs before the deployment finishes.\n"
	indexDated(t, idx, "Agent/memory/semantic/recalled.md", "Gate", old, body)
	indexDated(t, idx, "Agent/memory/semantic/forgotten.md", "Gate", old, body)

	out, err := idx.Search(Query{Text: "staging gate deployment", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) < 2 {
		t.Fatalf("expected both notes, got %v", resultPaths(out.Results))
	}
	if !strings.HasSuffix(out.Results[0].Path, "recalled.md") {
		t.Errorf("two notes carry the same four-year-old stamp and one was recalled "+
			"last month; the recall did not move it: %v", resultPaths(out.Results))
	}
}

// The sidecar lives under the memory root, not the vault root, and the two are
// different directories in the shipped layout: the vault is `~/Vault` and the
// memory is `~/Vault/Agent`. An Index rooted at the vault finds no sidecar,
// raises nothing, and ranks the entire corpus off its fallback anchor — a
// failure with no symptom, which is why it gets a test rather than a comment.
func TestTheSidecarIsReadFromTheMemoryRootNotTheVaultRoot(t *testing.T) {
	dir := t.TempDir()
	vault := filepath.Join(dir, "vault")
	idx, err := Open(filepath.Join(dir, "index.db"), vault, "Agent", true)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { idx.Close() })

	// Planted where the shipped layout puts it.
	writeLifecycle(t, filepath.Join(vault, "Agent"), map[string]string{
		"recalled": time.Now().AddDate(0, -1, 0).Format("2006-01-02"),
	})

	old := time.Now().AddDate(-4, 0, 0).Format("2006-01-02")
	body := "The staging gate runs before the deployment finishes.\n"
	indexDated(t, idx, "Agent/memory/semantic/recalled.md", "Gate", old, body)
	indexDated(t, idx, "Agent/memory/semantic/forgotten.md", "Gate", old, body)

	out, err := idx.Search(Query{Text: "staging gate deployment", K: 5})
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(out.Results) < 2 {
		t.Fatalf("expected both notes, got %v", resultPaths(out.Results))
	}
	if !strings.HasSuffix(out.Results[0].Path, "recalled.md") {
		t.Errorf("the sidecar under the memory root was not read: %v",
			resultPaths(out.Results))
	}
}
