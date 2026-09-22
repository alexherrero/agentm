package dreaming

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
)

// ideasVault is the live layout: an Obsidian vault with the memory root nested
// at `agent/`, the cards at the vault root's `personal/ideas/`, and `Ideas.md`
// at the vault root.
func ideasVault(t *testing.T) (*config.Config, string, string) {
	t.Helper()
	vault := t.TempDir()
	for _, d := range []string{".obsidian", "agent/memory/semantic", "personal/ideas", "personal/Home"} {
		if err := os.MkdirAll(filepath.Join(vault, filepath.FromSlash(d)), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	cfg := &config.Config{VaultPath: vault, MemoryRoot: "agent", EngineStateDir: filepath.Join(t.TempDir(), "state")}
	return cfg, vault, filepath.Join(vault, "agent")
}

func putIdea(t *testing.T, vault, slug, title, summary, area, dismissed string) {
	t.Helper()
	fm := "---\ntitle: " + title + "\ntype: idea\n"
	if area != "" {
		fm += "area: " + area + "\n"
	}
	if summary != "" {
		fm += "summary: " + summary + "\n"
	}
	fm += "status: active\n"
	if dismissed != "" {
		fm += "dismissed: " + dismissed + "\n"
	}
	fm += "---\n\nThe idea itself.\n"
	writeFile(t, vault, "personal/ideas/"+slug+".md", fm)
}

// The operator's introduction, with bytes a re-render would be tempted to
// normalize: a trailing space, a non-ASCII dash, a blank line inside, a line
// that looks like a heading.
const operatorHead = "# Ideas\n\n" + IdeasIntroStart + "\n\n" +
	"This file is rebuilt every night — from the idea notes. \n\n" +
	"## Not a group, just my words\n\n" + IdeasIntroEnd + "\n"

func ideasFile(t *testing.T, vault string) string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(vault, IdeasFile))
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func applyIdeas(t *testing.T, root string, plan IdeasPlan) {
	t.Helper()
	for _, in := range plan.Intents {
		if err := os.WriteFile(filepath.Join(root, filepath.FromSlash(in.Rel)), in.After, 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

// The file, rendered: one heading per group name the cards carry, in
// alphabetical order with the ungrouped last; each idea one line linking to its
// card with its title and summary, in title order; the dismissed ideas only in
// the collapsed callout at the end.
func TestTheListGroupsByAreaAndCollapsesTheDismissed(t *testing.T) {
	_, vault, root := ideasVault(t)
	writeFile(t, vault, IdeasFile, operatorHead+"\nstale hand-kept text\n")
	putIdea(t, vault, "port-simcity-1989", "Port the 1989 SimCity", "Micropolis makes it a build.", "coding", "")
	putIdea(t, vault, "mcp-server-for-plex", "MCP server for Plex", "Ask the library from anywhere.", "home-tech", "")
	putIdea(t, vault, "go-libraries-for-trading-exchanges", "Go libraries for exchanges", "", "coding", "")
	putIdea(t, vault, "blog-series-transition", "Move past the building series", "Write about improving it.", "blog", "")
	putIdea(t, vault, "unraid-github-actions-runner", "Unraid GitHub Actions runner", "Self-hosted CI.", "home-tech", "2026-05-24")
	putIdea(t, vault, "freshly-filed", "Something from the inbox", "Not grouped yet.", "", "")
	// Not cards: a subfolder, a dotfile, a non-markdown file, and a note
	// elsewhere under personal/.
	writeFile(t, vault, "personal/ideas/drafts/unsure.md", "---\ntitle: Draft\narea: coding\n---\n\nx\n")
	writeFile(t, vault, "personal/ideas/.scratch.md", "---\ntitle: Scratch\narea: coding\n---\n\nx\n")
	writeFile(t, vault, "personal/ideas/list.txt", "not a card\n")
	writeFile(t, vault, "personal/Home/recipe.md", "---\ntitle: Stew\narea: coding\n---\n\nx\n")

	plan, err := PlanIdeas(root, "")
	if err != nil {
		t.Fatal(err)
	}
	want := operatorHead +
		"\n## blog\n\n" +
		"- [[blog-series-transition|Move past the building series]] — Write about improving it.\n" +
		"\n## coding\n\n" +
		"- [[go-libraries-for-trading-exchanges|Go libraries for exchanges]]\n" +
		"- [[port-simcity-1989|Port the 1989 SimCity]] — Micropolis makes it a build.\n" +
		"\n## home-tech\n\n" +
		"- [[mcp-server-for-plex|MCP server for Plex]] — Ask the library from anywhere.\n" +
		"\n## no group yet\n\n" +
		"- [[freshly-filed|Something from the inbox]] — Not grouped yet.\n" +
		"\n> [!note]- Dismissed (1)\n>\n" +
		"> - [[unraid-github-actions-runner|Unraid GitHub Actions runner]] — Self-hosted CI. · dismissed 2026-05-24\n"
	if plan.Text != want {
		t.Errorf("rendering:\n%s\nwant:\n%s", plan.Text, want)
	}
	if plan.Cards != 6 || plan.Groups != 4 || !plan.Changed || len(plan.Intents) != 1 {
		t.Errorf("plan %+v", plan)
	}
	if strings.Count(plan.Text, "unraid-github-actions-runner") != 1 {
		t.Error("a dismissed idea appears outside the collapsed callout")
	}
	if plan.Intents[0].Rel != "../Ideas.md" {
		t.Errorf("the intent names %q, want the vault root's Ideas.md relative to the memory root", plan.Intents[0].Rel)
	}
}

// Idempotent, and the operator's half byte for byte: a rewrite over an
// unchanged folder plans nothing, and a rewrite after a card moved changes the
// list and not one byte above the end marker.
func TestARebuildKeepsTheIntroductionByteIdenticalAndIsIdempotent(t *testing.T) {
	_, vault, root := ideasVault(t)
	writeFile(t, vault, IdeasFile, operatorHead+"\nold list\n")
	putIdea(t, vault, "a", "Alpha", "First.", "coding", "")
	putIdea(t, vault, "b", "Beta", "Second.", "home-tech", "")

	first, err := PlanIdeas(root, "")
	if err != nil {
		t.Fatal(err)
	}
	applyIdeas(t, root, first)
	again, err := PlanIdeas(root, "")
	if err != nil {
		t.Fatal(err)
	}
	if again.Changed || len(again.Intents) != 0 {
		t.Fatalf("a rebuild over an unchanged folder planned a write: %+v", again)
	}

	// The operator regroups an idea by editing one word on its card.
	putIdea(t, vault, "b", "Beta", "Second.", "coding", "")
	moved, err := PlanIdeas(root, "")
	if err != nil {
		t.Fatal(err)
	}
	applyIdeas(t, root, moved)
	got := ideasFile(t, vault)
	if !strings.HasPrefix(got, operatorHead) {
		t.Fatalf("the operator's text is not byte-identical:\n%q", got[:len(operatorHead)])
	}
	if strings.Contains(got, "## home-tech") || !strings.Contains(got, "- [[b|Beta]] — Second.") {
		t.Errorf("the regrouped idea did not move headings:\n%s", got)
	}

	// Dismissing an idea moves it under the collapsed heading and nowhere else.
	putIdea(t, vault, "a", "Alpha", "First.", "coding", "2026-09-21")
	dismissed, err := PlanIdeas(root, "")
	if err != nil {
		t.Fatal(err)
	}
	applyIdeas(t, root, dismissed)
	got = ideasFile(t, vault)
	if strings.Count(got, "[[a|Alpha]]") != 1 || !strings.Contains(got, "> - [[a|Alpha]] — First. · dismissed 2026-09-21") {
		t.Errorf("the dismissed idea is not only under the collapsed heading:\n%s", got)
	}
	if !strings.HasPrefix(got, operatorHead) {
		t.Fatal("the operator's text moved when an idea was dismissed")
	}
}

// No markers, no write. The file the operator keeps today has none, and the
// night must not replace it before the one deliberate adoption.
func TestAFileWithoutMarkersIsNeverWritten(t *testing.T) {
	for name, body := range map[string]string{
		"the hand-kept file":   "# Ideas\n\n## 2026-05-20: Port SimCity\n\nA line.\n",
		"only a start marker":  "# Ideas\n\n" + IdeasIntroStart + "\n\ntext\n",
		"the markers reversed": "# Ideas\n\n" + IdeasIntroEnd + "\n\ntext\n\n" + IdeasIntroStart + "\n",
		"two end markers":      "# Ideas\n\n" + IdeasIntroStart + "\n" + IdeasIntroEnd + "\n" + IdeasIntroEnd + "\n",
	} {
		t.Run(name, func(t *testing.T) {
			_, vault, root := ideasVault(t)
			writeFile(t, vault, IdeasFile, body)
			putIdea(t, vault, "a", "Alpha", "First.", "coding", "")
			plan, err := PlanIdeas(root, "")
			if err != nil {
				t.Fatal(err)
			}
			if len(plan.Intents) != 0 || plan.NotWritten == "" {
				t.Errorf("planned a write to a file without a pair of markers: %+v", plan)
			}
			if got := ideasFile(t, vault); got != body {
				t.Error("the file changed")
			}
		})
	}
	// And no file at all is not an adoption either.
	_, _, root := ideasVault(t)
	plan, err := PlanIdeas(root, "")
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Intents) != 0 || plan.NotWritten != ErrIdeasNotAdopted.Error() {
		t.Errorf("a missing file was written: %+v", plan)
	}
	if _, err := IdeasHead("# Ideas\n"); !errors.Is(err, ErrIdeasNotAdopted) {
		t.Errorf("IdeasHead on an unmarked file: %v", err)
	}
}

// The adoption: the one write that replaces the hand-kept file, with the
// operator's approved introduction between the markers — after which the
// night keeps it, and a rebuild changes nothing.
func TestTheAdoptionWritesTheApprovedIntroductionOnceAndTheNightKeepsIt(t *testing.T) {
	cfg, vault, root := ideasVault(t)
	handKept := "# Ideas\n\n## 2026-05-20: Port SimCity\n\nA line.\n"
	writeFile(t, vault, IdeasFile, handKept)
	putIdea(t, vault, "port-simcity-1989", "Port the 1989 SimCity", "A build.", "coding", "")
	intro := "This file is rebuilt every night from the idea notes in `personal/ideas/`.\n\nTo retire an idea, give its note a `dismissed:` date."

	dry, err := Ideas(cfg, IdeasOptions{Intro: intro})
	if err != nil {
		t.Fatal(err)
	}
	if got := ideasFile(t, vault); got != handKept {
		t.Fatal("the dry run wrote the file")
	}
	wantHead := "# Ideas\n\n" + IdeasIntroStart + "\n\n" + intro + "\n\n" + IdeasIntroEnd + "\n"
	if !strings.HasPrefix(dry.Text, wantHead) {
		t.Fatalf("the adoption's head:\n%s", dry.Text)
	}

	written, err := Ideas(cfg, IdeasOptions{Intro: intro, Write: true, Now: time.Date(2026, 9, 21, 7, 0, 0, 0, time.UTC)})
	if err != nil {
		t.Fatal(err)
	}
	if got := ideasFile(t, vault); got != dry.Text || got != written.Text {
		t.Fatalf("the write is not the rendering the dry run showed:\n%s", got)
	}
	// Journaled like the night's writes: the intent and its application.
	journal, err := OpenJournal(cfg.EngineStateDir)
	if err != nil {
		t.Fatal(err)
	}
	entries, err := journal.Read()
	if err != nil {
		t.Fatal(err)
	}
	var applied bool
	for _, e := range entries {
		if e.Kind == KindApplied && e.Rel == "../Ideas.md" {
			applied = true
		}
	}
	if !applied {
		t.Errorf("the write is not in the journal: %+v", entries)
	}
	if runID, pending := Unfinished(entries); runID != "" {
		t.Errorf("the write left run %s unfinished: %v", runID, pending)
	}

	// From now on the nightly job keeps it, under the head it finds.
	night, err := PlanIdeas(root, "")
	if err != nil {
		t.Fatal(err)
	}
	if night.Changed {
		t.Errorf("the night would rewrite what the adoption just wrote: %+v", night)
	}
}

// Through the night itself: `Run` rebuilds Ideas.md with the maps when a card
// moved, and a second pass over the same folder writes nothing to it.
func TestTheNightRebuildsIdeasWithTheMaps(t *testing.T) {
	cfg, vault, root := ideasVault(t)
	writeFile(t, vault, IdeasFile, operatorHead)
	putIdea(t, vault, "a", "Alpha", "First.", "coding", "")
	now := time.Date(2026, 9, 21, 7, 0, 0, 0, time.UTC)
	rep, err := Run(cfg, Options{Now: now, Apply: true, Force: true})
	if err != nil {
		t.Fatal(err)
	}
	if !rep.Ideas.Changed {
		t.Fatalf("the night did not rebuild Ideas.md: %+v", rep.Ideas)
	}
	got := ideasFile(t, vault)
	if !strings.HasPrefix(got, operatorHead) || !strings.Contains(got, "- [[a|Alpha]] — First.") {
		t.Fatalf("Ideas.md after the night:\n%s", got)
	}
	again, err := Run(cfg, Options{Now: now.Add(time.Hour), Apply: true, Force: true})
	if err != nil {
		t.Fatal(err)
	}
	if again.Ideas.Changed || ideasFile(t, vault) != got {
		t.Errorf("a second night rewrote an unchanged list: %+v", again.Ideas)
	}
	_ = root
}
