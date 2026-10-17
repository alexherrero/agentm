package capture

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const streamingCard = "---\ntitle: \"Streaming, but not 4K Blu-ray yet\"\ntype: idea\narea: home-tech\nstatus: active\n" +
	"slug: streaming-but-not-4k-bluray-yet\n---\n\nStream the everyday catalogue, keep buying 4K discs for the " +
	"films worth the bitrate, and revisit once streaming bitrates catch up.\n"

func ideaHarness(t *testing.T) (*Capturer, string) {
	t.Helper()
	cp := newHarness(t)
	dir := filepath.Join(cp.cfg.VaultPath, "personal", "ideas")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	card := filepath.Join(dir, "streaming-but-not-4k-bluray-yet.md")
	if err := os.WriteFile(card, []byte(streamingCard), 0o644); err != nil {
		t.Fatal(err)
	}
	return cp, card
}

func memoryNotes(t *testing.T, cp *Capturer) []string {
	t.Helper()
	var out []string
	_ = filepath.Walk(cp.cfg.VaultPath, func(p string, info os.FileInfo, err error) error {
		if err == nil && !info.IsDir() && strings.HasSuffix(p, ".md") && !strings.Contains(p, "personal") {
			out = append(out, p)
		}
		return nil
	})
	return out
}

func TestAnIdeaThatHasACardIsAddedToIt(t *testing.T) {
	cp, card := ideaHarness(t)
	res, err := cp.Do(Request{Type: "idea", Source: "conversation", Text: "Stream the everyday catalogue, keep " +
		"buying 4K discs for the films worth the bitrate, and revisit once streaming bitrates catch up. The " +
		"Apple TV handles Dolby Vision now."})
	if err != nil {
		t.Fatal(err)
	}
	if !res.Appended || res.Path != "personal/ideas/streaming-but-not-4k-bluray-yet.md" {
		t.Fatalf("result %+v, want an append to the card", res)
	}
	if got := memoryNotes(t, cp); len(got) != 0 {
		t.Errorf("a carded idea wrote notes: %v", got)
	}
	raw, _ := os.ReadFile(card)
	text := string(raw)
	if !strings.HasPrefix(text, strings.TrimRight(streamingCard, "\n")) {
		t.Errorf("the card above the section changed:\n%s", text)
	}
	if !strings.Contains(text, "\n## Added by capture\n") || !strings.Contains(text, " · conversation**") ||
		!strings.Contains(text, "Dolby Vision") {
		t.Errorf("the addition is not under a dated, sourced heading:\n%s", text)
	}
	again, err := cp.Do(Request{Type: "idea", Source: "conversation", Text: "The Apple TV handles Dolby Vision now."})
	if err != nil {
		t.Fatal(err)
	}
	raw, _ = os.ReadFile(card)
	if again.Appended || strings.Count(string(raw), "Dolby Vision") != 1 {
		t.Errorf("the same words were added twice: %+v\n%s", again, raw)
	}
}

// A new idea lands in the inbox, the operator's words as they were given and
// its source kept, `unfiled` until the review pass files it into
// personal/ideas/ (task 192, #676). Not memory/semantic/, where the miner's
// unread idea cards piled up.
func TestANewIdeaLandsInTheInbox(t *testing.T) {
	cp, card := ideaHarness(t)
	words := "Drip lines on a timer for the tomato beds this spring."
	res, err := cp.Do(Request{Type: "idea", Source: "conversation", Text: words})
	if err != nil {
		t.Fatal(err)
	}
	want := inboxRel(cp.cfg.MemoryRoot)
	if res.Appended || filepath.ToSlash(filepath.Dir(res.Path)) != want {
		t.Fatalf("a new idea: %+v, want a card in %s", res, want)
	}
	raw, err := os.ReadFile(filepath.Join(cp.cfg.VaultPath, filepath.FromSlash(res.Path)))
	if err != nil {
		t.Fatal(err)
	}
	text := string(raw)
	for _, line := range []string{"type: idea\n", "status: unfiled\n", "source: conversation\n"} {
		if !strings.Contains(text, line) {
			t.Errorf("the inbox card lacks %q:\n%s", strings.TrimSpace(line), text)
		}
	}
	if !strings.Contains(text, words) {
		t.Errorf("the words are not on the card verbatim:\n%s", text)
	}
	if !strings.Contains(res.Note, "inbox") {
		t.Errorf("the result does not say where the idea went: %q", res.Note)
	}
	if raw, _ := os.ReadFile(card); string(raw) != streamingCard {
		t.Error("an unrelated card was written")
	}
	if semantic, _ := filepath.Glob(filepath.Join(cp.cfg.VaultPath, "*", "memory", "semantic", "*.md")); len(semantic) != 0 {
		t.Errorf("a new idea wrote into the semantic class: %v", semantic)
	}
}

// A writer that knew the type and gave a why still leaves an idea `unfiled` in
// the inbox: nothing there is filed until the operator reads it.
func TestAnIdeaWithAWhyStillWaitsForReview(t *testing.T) {
	cp, _ := ideaHarness(t)
	res, err := cp.Do(Request{Type: "idea", Why: "it would save an hour a week",
		Text: "Automate the PlayOn recordings, as that's tedious by hand."})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "unfiled" || filepath.ToSlash(filepath.Dir(res.Path)) != inboxRel(cp.cfg.MemoryRoot) {
		t.Errorf("%+v, want unfiled in the inbox", res)
	}
}

func TestANonIdeaNeverReachesTheCards(t *testing.T) {
	cp, card := ideaHarness(t)
	if _, err := cp.Do(Request{Type: "reference", Text: "Stream the everyday catalogue, keep buying 4K discs " +
		"for the films worth the bitrate, and revisit once streaming bitrates catch up."}); err != nil {
		t.Fatal(err)
	}
	if raw, _ := os.ReadFile(card); string(raw) != streamingCard {
		t.Error("a reference was added to an idea card")
	}
}
