package main

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"

	"github.com/alexherrero/agentm/daemon/internal/index"
)

// agentm-vault part 13: `personal/ideas/` joins the night's queue as cards —
// the cards' tier, oldest first among the class directories' cards — and
// nothing else under `personal/` joins it. A recipe the operator keeps under
// `personal/Home` is never offered, and neither is a note in a subfolder of the
// ideas folder, a dotfile there, or anything that only starts with its name.
func TestTheQueueOffersIdeaCardsAsCardsAndNothingElseInPersonal(t *testing.T) {
	vault := t.TempDir()
	cfg := configOverRules(t, vault, "reference", "idea")
	cfg.MemoryRoot = "agent"

	card := func(created string) string {
		return "---\ntype: reference\nstatus: unfiled\ncreated: " + created + "\n---\n\nA thought.\n"
	}
	idea := func(created string) string {
		return "---\ntype: idea\narea: coding\nstatus: active\ncreated: " + created + "\n---\n\nAn idea.\n"
	}
	notes := map[string]string{
		"agent/inbox/dropped.md":                 card("2026-09-19"),
		"agent/memory/semantic/newer-card.md":    card("2026-09-15"),
		"agent/memory/semantic/older-card.md":    card("2026-05-01"),
		"personal/ideas/port-simcity-1989.md":    idea("2026-05-20"),
		"personal/ideas/doom-llm-npcs.md":        idea("2026-06-07"),
		"projects/agentm/decisions/keep-wall.md": "---\nkind: decision\ncreated: 2020-01-01\n---\n\nSettled.\n",
		// Never offered.
		"personal/Home/recipes/stew.md":   "---\ntitle: Stew\n---\n\nBrown the meat first.\n",
		"personal/ideas/drafts/unsure.md": idea("2026-01-01"),
		"personal/ideas/.scratch.md":      idea("2026-01-01"),
		"personal/ideas-archive/old.md":   idea("2026-01-01"),
	}
	x, err := index.Open(filepath.Join(t.TempDir(), "index.db"), vault, "agent", false)
	if err != nil {
		t.Fatal(err)
	}
	defer x.Close()
	for rel, body := range notes {
		putNote(t, x, vault, rel, body)
	}

	got, err := enrichServeOrder(cfg, x, true)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		"agent/inbox/dropped.md",
		// One tier: class cards and idea cards together, oldest first.
		"agent/memory/semantic/older-card.md",
		"personal/ideas/port-simcity-1989.md",
		"personal/ideas/doom-llm-npcs.md",
		"agent/memory/semantic/newer-card.md",
		"projects/agentm/decisions/keep-wall.md",
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("serve order\n got %v\nwant %v", got, want)
	}

	// The ledger's eligible population is the cards, so idea cards count in it
	// and the records do not.
	cards, err := enrichServeOrder(cfg, x, false)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(cards, want[:len(want)-1]) {
		t.Errorf("the eligible population is %v, want the cards and ideas only", cards)
	}
}

// The dry run names the idea cards as their own population, in the folder they
// are in — and a vault without the folder reads exactly as it always has.
func TestTheDryRunNamesTheIdeaCardsApart(t *testing.T) {
	state := t.TempDir()
	t.Setenv("AGENTM_STATE_DIR", state)
	t.Setenv("AGENTM_STORAGE_RULES", "")
	os.Unsetenv("AGENTM_STORAGE_RULES")
	vault := t.TempDir()
	writeRules(t, vault, "reference", "idea")
	kernel := filepath.Join(t.TempDir(), "agentm-config.json")
	if err := os.WriteFile(kernel, []byte(`{"plugins.obsidian-vault.memory_root": "agent"}`), 0o644); err != nil {
		t.Fatal(err)
	}
	idxPath := filepath.Join(t.TempDir(), "index.db")
	x, err := index.Open(idxPath, vault, "agent", false)
	if err != nil {
		t.Fatal(err)
	}
	putNote(t, x, vault, "agent/memory/semantic/a-card.md", "---\ntype: reference\n---\n\nA.\n")
	putNote(t, x, vault, "personal/ideas/an-idea.md", "---\ntype: idea\narea: coding\nstatus: active\n---\n\nI.\n")
	putNote(t, x, vault, "personal/Home/recipe.md", "---\ntitle: r\n---\n\nR.\n")
	x.Close()

	out := captureStdout(t, func() error {
		return cmdEnrich([]string{"-config", kernel, "-vault", vault, "-index", idxPath, "-page-size", "10", "-dry-run"})
	})
	want := "dry run: 0 inbox card(s) under agent/inbox/, 1 card(s) under agent/memory/semantic/, " +
		"1 idea card(s) under personal/ideas/ and 0 project record(s), served in that order and oldest first\n"
	if !strings.Contains(out, want) {
		t.Errorf("the summary line is not %q:\n%s", strings.TrimSuffix(want, "\n"), out)
	}
	if strings.Contains(out, "personal/Home") {
		t.Errorf("the dry run offered a note from personal/Home:\n%s", out)
	}
}

// buildNightStub compiles the model stub once for a test and puts it first on
// PATH, so the night's `claude` is the stub and never the real model.
func buildNightStub(t *testing.T) {
	t.Helper()
	stub := t.TempDir()
	src := filepath.Join(stub, "main.go")
	if err := os.WriteFile(src, []byte(nightStubSource), 0o644); err != nil {
		t.Fatal(err)
	}
	bin := filepath.Join(stub, "claude")
	if runtime.GOOS == "windows" {
		bin += ".exe"
	}
	if out, err := exec.Command("go", "build", "-o", bin, src).CombinedOutput(); err != nil {
		t.Fatalf("building the model stub: %v\n%s", err, out)
	}
	t.Setenv("PATH", stub+string(os.PathListSeparator)+os.Getenv("PATH"))
	if found, err := exec.LookPath("claude"); err != nil || filepath.Dir(found) != stub {
		t.Fatalf("claude resolves to %q (%v), not to the stub", found, err)
	}
}

// Ruling 6 of 2026-09-20, driven through the night itself: a real card in
// `personal/ideas/`, the command the runner calls, and a model that answers
// both above and below the floor while trying to re-grade the idea — another
// kind, another title, another name. The card keeps its standing, its name, its
// kind, its group and its dismissal, gains no lifecycle, and gains the night's
// section under its own heading with the operator's text above it byte for
// byte. A recipe under `personal/Home` sits beside it through the whole night
// and is not written.
func TestAFixtureNightThinksAnIdeaThroughAndNeverRegradesIt(t *testing.T) {
	buildNightStub(t)
	const (
		ideaRel   = "personal/ideas/port-simcity-1989.md"
		recipeRel = "personal/Home/recipes/stew.md"
		operator  = "Rebuild the original in a modern engine, keeping the simulation rules.\n"
	)
	idea := "---\ntitle: Port the 1989 SimCity to a modern target\ntype: idea\narea: coding\n" +
		"summary: The operator's line.\nimportance: 4\nstatus: active\ndismissed: 2026-05-24\n" +
		"filing_confidence: high\nsource: operator-direct\ntrust: trusted\ncreated: 2026-05-20\n" +
		"updated: 2026-05-20\ntags: [games]\n---\n\n" + operator
	recipe := "---\ntitle: Stew\ntags: [recipes]\n---\n\nBrown the meat first, then deglaze.\n"

	for _, tc := range []struct {
		name       string
		confidence string
	}{
		{"above the floor", "0.95"},
		{"below the floor", "0.10"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("AGENTMD_STUB_ANSWER", `{"title": "SimCity port feasibility", `+
				`"slug": "simcity-port-feasibility", "type": "reference", `+
				`"summary": "Micropolis makes the port a build.", "tags": ["games", "retro"], `+
				`"confidence": `+tc.confidence+`, "importance_proposed": 7, `+
				`"body": "The 1989 source was released in 2008 as Micropolis."}`)
			t.Setenv("AGENTMD_STUB_VERDICT", `{"grounded": true}`)
			state := t.TempDir()
			t.Setenv("AGENTM_STATE_DIR", state)
			t.Setenv("AGENTM_STORAGE_RULES", "")
			os.Unsetenv("AGENTM_STORAGE_RULES")
			vault := t.TempDir()
			writeRules(t, vault, "reference", "idea")
			kernel := filepath.Join(t.TempDir(), "agentm-config.json")
			if err := os.WriteFile(kernel, []byte(`{"plugins.obsidian-vault.memory_root": "agent", `+
				`"daemon.embedder_url": "http://127.0.0.1:1"}`), 0o644); err != nil {
				t.Fatal(err)
			}
			idxPath := filepath.Join(t.TempDir(), "index.db")
			x, err := index.Open(idxPath, vault, "agent", false)
			if err != nil {
				t.Fatal(err)
			}
			putNote(t, x, vault, ideaRel, idea)
			putNote(t, x, vault, recipeRel, recipe)
			x.Close()

			if err := cmdEnrich([]string{"-config", kernel, "-vault", vault, "-index", idxPath, "-yes"}); err != nil {
				t.Fatalf("the night: %v", err)
			}

			raw, err := os.ReadFile(filepath.Join(state, "enrich-runs.jsonl"))
			if err != nil {
				t.Fatal(err)
			}
			var run enrichRun
			if err := json.Unmarshal(raw, &run); err != nil {
				t.Fatal(err)
			}
			if run.Enriched != 1 || run.Failed != 0 {
				t.Fatalf("the night enriched %d and failed %d, want 1 and 0: %v", run.Enriched, run.Failed, run.Errors)
			}
			if run.Verdicts.Ideas != 1 || run.Verdicts.Active != 0 || run.Verdicts.BelowFloor != 0 || run.Verdicts.Sank != 0 {
				t.Errorf("verdicts %+v: the idea was counted as filed or judged", run.Verdicts)
			}

			if _, err := os.Stat(filepath.Join(vault, "personal/ideas/simcity-port-feasibility.md")); err == nil {
				t.Error("the night renamed an idea card")
			}
			got, err := os.ReadFile(filepath.Join(vault, filepath.FromSlash(ideaRel)))
			if err != nil {
				t.Fatalf("the idea card left its path: %v", err)
			}
			card := string(got)
			for _, want := range []string{
				"title: Port the 1989 SimCity to a modern target", "type: idea", "area: coding",
				"status: active", "dismissed: 2026-05-24", "filing_confidence: high",
				"summary: Micropolis makes the port a build.", "importance_proposed: 7",
			} {
				if !strings.Contains(card, "\n"+want+"\n") {
					t.Errorf("the card lacks %q:\n%s", want, card)
				}
			}
			for _, never := range []string{"\nlifecycle:", "\nlifecycle_since:", "type: reference",
				"status: unfiled", "filing_confidence: low", "SimCity port feasibility"} {
				if strings.Contains(card, never) {
					t.Errorf("the card carries %q:\n%s", never, card)
				}
			}
			end := strings.Index(card[4:], "\n---\n")
			if end < 0 {
				t.Fatalf("no frontmatter:\n%s", card)
			}
			body := card[4+end+len("\n---\n"):]
			if !strings.HasPrefix(strings.TrimLeft(body, "\n"), operator) {
				t.Errorf("the operator's text is not first and byte-identical:\n%s", body)
			}
			if !strings.Contains(body, "\n## Added by dreaming (") ||
				!strings.Contains(body, "The 1989 source was released in 2008 as Micropolis.") {
				t.Errorf("the night did not think the idea through under its heading:\n%s", body)
			}

			after, err := os.ReadFile(filepath.Join(vault, filepath.FromSlash(recipeRel)))
			if err != nil {
				t.Fatal(err)
			}
			if string(after) != recipe {
				t.Errorf("the recipe under personal/Home was written:\n%s", after)
			}
		})
	}
}
