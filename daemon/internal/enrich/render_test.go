package enrich

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// The rendered note has to parse as YAML, or the repository's own frontmatter
// gate rejects the whole corpus enrichment touches.
func TestARenderedNoteParsesAsYAML(t *testing.T) {
	for _, tc := range []struct {
		name string
		r    Response
	}{
		{"ordinary", Response{
			Title: "The staging gate", Type: "convention",
			Body: "It runs first.", Confidence: 0.9,
			Tags: []string{"ci", "deploy"}, Aliases: []string{"staging gate"},
		}},
		{"a title that is a mapping", Response{
			Title: "recall: how it ranks", Type: "fact",
			Body: "b", Confidence: 0.9,
		}},
		{"a title that is a comment", Response{
			Title: "#hashtag conventions", Type: "fact",
			Body: "b", Confidence: 0.9,
		}},
		{"a title that reads as a bool", Response{
			Title: "true", Type: "fact",
			Body: "b", Confidence: 0.9,
		}},
		{"a title that reads as a number", Response{
			Title: "2026", Type: "fact",
			Body: "b", Confidence: 0.9,
		}},
		{"a summary with a colon", Response{
			Title: "T", Type: "fact", Body: "b",
			Confidence: 0.9, Summary: "the rule: nothing moves",
		}},
		{"an alias with a quote", Response{
			Title: "T", Type: "fact", Body: "b",
			Confidence: 0.9, Aliases: []string{`the "gate"`},
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			out := RenderNote(tc.r, Stamp{})
			head, _, ok := strings.Cut(strings.TrimPrefix(out, "---\n"), "---\n")
			if !ok {
				t.Fatalf("no frontmatter block:\n%s", out)
			}
			var m map[string]any
			if err := yaml.Unmarshal([]byte(head), &m); err != nil {
				t.Fatalf("the frontmatter does not parse: %v\n%s", err, head)
			}
			if got := m["title"]; got == nil {
				t.Errorf("title missing after a round trip: %v", m)
			}
		})
	}
}

// Field order is fixed rather than map-iteration order. Two enrichments
// differing only in key order would show as a diff in git and change any hash
// of the file, making every review of the corpus's history noisier for nothing.
func TestFieldOrderIsStable(t *testing.T) {
	r := Response{
		Title: "T", Type: "fact", Body: "b",
		Confidence: 0.9, Tags: []string{"a", "b"}, Aliases: []string{"c"},
	}
	first := RenderNote(r, Stamp{})
	for i := 0; i < 20; i++ {
		if got := RenderNote(r, Stamp{}); got != first {
			t.Fatalf("render %d differs from the first:\n%s\n---\n%s", i, first, got)
		}
	}
	// And the order is the one declared, not whatever happened to come out.
	head := first[:strings.Index(first, "---\n\n")]
	// No `altitude:` — the deep pass drops it (agentm-vault plan 04).
	want := []string{"title:", "type:", "status:", "confidence:", "filing_confidence:"}
	last := -1
	for _, k := range want {
		i := strings.Index(head, k)
		if i < 0 {
			t.Fatalf("%s missing from the frontmatter:\n%s", k, head)
		}
		if i < last {
			t.Errorf("%s appears out of order:\n%s", k, head)
		}
		last = i
	}
}

// The review queue is a query, not a directory. A low-confidence enrichment
// lands in its class folder carrying the number that made it low.
func TestLowConfidenceLandsUnfiledWithItsNumber(t *testing.T) {
	low := RenderNote(Response{
		Title: "T", Type: "fact", Body: "b", Confidence: 0.2,
	}, Stamp{})
	if !strings.Contains(low, "status: unfiled") {
		t.Errorf("a low-confidence note was not filed for review:\n%s", low)
	}
	if !strings.Contains(low, "confidence: 0.20") {
		t.Errorf("the note does not carry the number that made it low:\n%s", low)
	}

	high := RenderNote(Response{
		Title: "T", Type: "fact", Body: "b", Confidence: 0.95,
	}, Stamp{})
	if !strings.Contains(high, "status: active") {
		t.Errorf("a confident enrichment was queued for review anyway:\n%s", high)
	}
}

func TestStatusForStraddlesTheFloor(t *testing.T) {
	if StatusFor(DefaultConfidenceFloor, 0) != "active" {
		t.Error("a note exactly at the floor was queued for review")
	}
	if StatusFor(DefaultConfidenceFloor-0.01, 0) != "unfiled" {
		t.Error("a note below the floor was marked active")
	}
}

// The note says which pass wrote it. Without that, a corpus half-enriched by two
// prompt versions is indistinguishable from one enriched consistently.
func TestTheNoteRecordsWhichPassWroteIt(t *testing.T) {
	out := RenderNote(Response{
		Title: "T", Type: "fact", Body: "b", Confidence: 0.9,
	}, Stamp{})
	if !strings.Contains(out, PassVersion) {
		t.Errorf("the note does not record the pass version:\n%s", out)
	}
}

// --- the journal on disk ----------------------------------------------------

func TestTheFileJournalAppendsOnePerWrite(t *testing.T) {
	dir := t.TempDir()
	j := NewFileJournal(dir)

	for i := 0; i < 3; i++ {
		if err := j.Record(nil, JournalEntry{
			Rel: "Agent/memory/n.md", Previous: "old", Next: "new",
		}); err != nil {
			t.Fatal(err)
		}
	}
	blob, err := os.ReadFile(filepath.Join(dir, "enrichment-journal.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(string(blob)), "\n")
	if len(lines) != 3 {
		t.Errorf("%d lines for 3 writes — the journal overwrites rather than "+
			"appends", len(lines))
	}
	for i, l := range lines {
		if !strings.Contains(l, `"previous":"old"`) {
			t.Errorf("line %d lost what was replaced: %s", i, l)
		}
	}
}

// The design wants a threshold moved by editing the contract, not by a
// release. The floor rides on the Stamp, and a Stamp with none falls back to
// the packaged default rather than to zero — a floor of zero would file
// everything active, which is the one reading silence must never produce.
func TestTheFloorComesFromTheContract(t *testing.T) {
	for name, tc := range map[string]struct {
		floor      float64
		confidence float64
		status     string
	}{
		"a stricter contract files a middling score for review": {0.8, 0.7, "unfiled"},
		"a looser contract files the same score active":         {0.5, 0.7, "active"},
		"exactly at the contract's floor is active":             {0.8, 0.8, "active"},
		"no contract falls back to the packaged default":        {0, DefaultConfidenceFloor, "active"},
		"no contract does not mean no floor":                    {0, DefaultConfidenceFloor - 0.01, "unfiled"},
		"a negative floor is not a floor":                       {-1, DefaultConfidenceFloor - 0.01, "unfiled"},
	} {
		t.Run(name, func(t *testing.T) {
			if got := StatusFor(tc.confidence, tc.floor); got != tc.status {
				t.Errorf("StatusFor(%v, floor %v) = %q, want %q",
					tc.confidence, tc.floor, got, tc.status)
			}
		})
	}

	// And the rendered note carries what the stamp said.
	body := RenderNote(Response{Title: "T", Type: "reference", Confidence: 0.7},
		Stamp{ConfidenceFloor: 0.8})
	if !strings.Contains(body, "status: unfiled") || !strings.Contains(body, "filing_confidence: low") {
		t.Errorf("the stamp's floor did not reach the note:\n%s", body)
	}
}
