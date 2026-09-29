package capture

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func noteFiles(t *testing.T, cp *Capturer) []string {
	t.Helper()
	var out []string
	_ = filepath.Walk(cp.cfg.VaultPath, func(p string, info os.FileInfo, err error) error {
		if err == nil && !info.IsDir() && strings.HasSuffix(p, ".md") {
			out = append(out, p)
		}
		return nil
	})
	return out
}

// The operator's ruling 6b: the same source captured again updates its note in
// place — the path and the links to it stay, and no file is added.
func TestTheSameSourceCapturedAgainUpdatesItsNote(t *testing.T) {
	cp := newHarness(t)
	first, err := cp.Do(Request{Text: "The first reading of the thread.", Title: "The retention thread",
		Type: "reference", Why: "kept for the ruling", SourceID: "gmail:thread-1234"})
	if err != nil {
		t.Fatal(err)
	}
	abs := filepath.Join(cp.cfg.VaultPath, filepath.FromSlash(first.Path))
	// The operator reads it and sets importance; enrichment stamped it.
	raw, _ := os.ReadFile(abs)
	edited := strings.Replace(string(raw), "slug: ", "importance: 9\nenriched_by: enrich/1\nslug: ", 1)
	if err := os.WriteFile(abs, []byte(edited), 0o644); err != nil {
		t.Fatal(err)
	}
	before := len(noteFiles(t, cp))

	second, err := cp.Do(Request{Text: "The thread, read again after the last reply.", Title: "The retention thread",
		Type: "reference", Why: "kept for the ruling", Importance: 4, SourceID: "gmail:thread-1234"})
	if err != nil {
		t.Fatal(err)
	}
	if !second.Updated || second.Path != first.Path || second.Slug != first.Slug {
		t.Fatalf("second capture: %+v, want an update of %s", second, first.Path)
	}
	if after := len(noteFiles(t, cp)); after != before {
		t.Errorf("files %d -> %d; an update in place creates none", before, after)
	}
	got := readNote(t, cp, first.Path)
	for _, want := range []string{"The thread, read again after the last reply.", "importance: 9\n",
		"enriched_by: enrich/1\n", "source_id: \"gmail:thread-1234\"\n"} {
		if !strings.Contains(got, want) {
			t.Errorf("missing %q after the update:\n%s", want, got)
		}
	}
	if strings.Contains(got, "The first reading") {
		t.Errorf("the body was not replaced:\n%s", got)
	}
	if strings.Contains(got, "importance: 4\n") {
		t.Errorf("the operator's importance was overwritten:\n%s", got)
	}
	if strings.Count(got, "\ncreated: ") != 1 || strings.Count(got, "\ntitle: ") != 1 {
		t.Errorf("a field was written twice:\n%s", got)
	}
}

func TestADifferentSourceWritesItsOwnNote(t *testing.T) {
	cp := newHarness(t)
	a, err := cp.Do(Request{Text: "One thread.", Title: "The retention thread", Type: "reference", SourceID: "gmail:thread-1"})
	if err != nil {
		t.Fatal(err)
	}
	b, err := cp.Do(Request{Text: "Another thread.", Title: "The retention thread", Type: "reference", SourceID: "gmail:thread-2"})
	if err != nil {
		t.Fatal(err)
	}
	if b.Updated || a.Path == b.Path {
		t.Errorf("a different source updated %s: %+v", a.Path, b)
	}
}

// One source yields several memories: an ingested article's atomic facts share
// its page address. Each is its own note, so the title is part of "the same".
func TestTwoMemoriesFromOneSourceStayTwoNotes(t *testing.T) {
	cp := newHarness(t)
	url := "https://example.com/always-on-agent"
	a, _ := cp.Do(Request{Text: "It consolidates on a timer.", Title: "It consolidates on a timer", Type: "reference", SourceURL: url})
	b, err := cp.Do(Request{Text: "Its query agent cites its sources.", Title: "Its query agent cites its sources", Type: "reference", SourceURL: url})
	if err != nil {
		t.Fatal(err)
	}
	if b.Updated || a.Path == b.Path {
		t.Errorf("a second fact from one source overwrote the first: %+v", b)
	}
	again, _ := cp.Do(Request{Text: "It consolidates every hour.", Title: "It consolidates on a timer", Type: "reference", SourceURL: url})
	if !again.Updated || again.Path != a.Path {
		t.Errorf("the same fact from the same page: %+v, want an update of %s", again, a.Path)
	}
}

// Neither a session id nor a bare word is a source identity: every card a
// session writes would share the first, and every crystallized lesson carries
// the second.
func TestASessionIDOrABareWordIsNoSourceIdentity(t *testing.T) {
	for _, id := range []string{"session:9b9d740e", "crystallize"} {
		cp := newHarness(t)
		a, _ := cp.Do(Request{Text: "First.", Title: "Same title", Type: "preference", SourceID: id})
		b, err := cp.Do(Request{Text: "Second.", Title: "Same title", Type: "preference", SourceID: id})
		if err != nil {
			t.Fatal(err)
		}
		if b.Updated || a.Path == b.Path {
			t.Errorf("%s: the second capture updated the first: %+v", id, b)
		}
	}
}

// A superseded note is settled; the capture that follows it is new.
func TestASupersededNoteIsNeverUpdated(t *testing.T) {
	cp := newHarness(t)
	a, _ := cp.Do(Request{Text: "Old.", Title: "The page", Type: "reference", SourceURL: "https://example.com/p"})
	abs := filepath.Join(cp.cfg.VaultPath, filepath.FromSlash(a.Path))
	raw, _ := os.ReadFile(abs)
	_ = os.WriteFile(abs, []byte(strings.Replace(string(raw), "lifecycle: active", "lifecycle: superseded", 1)), 0o644)
	b, err := cp.Do(Request{Text: "New.", Title: "The page", Type: "reference", SourceURL: "https://example.com/p"})
	if err != nil {
		t.Fatal(err)
	}
	if b.Updated || b.Path == a.Path {
		t.Errorf("a superseded note was updated: %+v", b)
	}
}
