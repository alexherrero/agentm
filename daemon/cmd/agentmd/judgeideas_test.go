package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/dreaming"
	"github.com/alexherrero/agentm/daemon/internal/ideajudge"
)

const ideaJudgeCard = "---\ntitle: \"Streaming, but not 4K Blu-ray yet\"\ntype: idea\narea: home-tech\nstatus: active\n" +
	"slug: streaming-but-not-4k-bluray-yet\n---\n\nStream the everyday catalogue, keep buying 4K discs.\n"

func judgeFixture(t *testing.T) *config.Config {
	t.Helper()
	vault := filepath.Join(t.TempDir(), "vault")
	for _, d := range []string{".obsidian", "agent/memory/episodic", "agent/inbox", "personal/ideas"} {
		if err := os.MkdirAll(filepath.Join(vault, d), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	trace := "---\ntitle: \"a session\"\nkind: session-trace\ncreated: 2026-10-16\nday: 2026-10-16\n" +
		"session: \"s1\"\nslug: s1\n---\n\n## Ideas\n\n```jsonl\n" +
		`{"remark": "Stream it, but revisit 4K once the bitrates catch up.", "message": "m1", "context": "operator: …"}` + "\n" +
		`{"remark": "We should also automate the PlayOn recordings later.", "message": "m2", "context": "operator: …"}` + "\n```\n"
	for rel, text := range map[string]string{"agent/memory/episodic/s1.md": trace,
		"personal/ideas/streaming-but-not-4k-bluray-yet.md": ideaJudgeCard} {
		if err := os.WriteFile(filepath.Join(vault, filepath.FromSlash(rel)), []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return &config.Config{VaultPath: vault, MemoryRoot: "agent", EngineStateDir: filepath.Join(t.TempDir(), "state")}
}

func judgeAnswers(prompt string) (string, error) {
	if strings.Contains(prompt, "revisit 4K") {
		return `{"verdict": "repeat", "slug": "streaming-but-not-4k-bluray-yet", "reason": "the streaming idea"}`, nil
	}
	return `{"verdict": "new", "title": "Automate the PlayOn recordings", "area": "home-tech", "reason": "a project"}`, nil
}

// The judge's writes go through the dreaming journal (task 192): a repeat
// added to its card in personal/ideas/, reached from the memory root by `../`,
// and a new idea created in the inbox; a written verdict is not planned again.
func TestTheJudgesWritesGoThroughTheJournal(t *testing.T) {
	cfg := judgeFixture(t)
	now := time.Date(2026, 10, 17, 9, 0, 0, 0, time.UTC)
	st := &ideajudge.State{}
	opt := ideajudge.Options{Vault: cfg.VaultPath, MemoryRoot: "agent", Now: now, Call: judgeAnswers}
	plan, err := ideajudge.Decide(opt, st)
	if err != nil {
		t.Fatal(err)
	}
	res, err := dreaming.ApplyIntents(cfg, ideajudge.Job, "judge-ideas: test", plan.Intents, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	if res.Applied != 2 || res.Skipped != 0 || res.RunID == "" {
		t.Fatalf("%+v", res)
	}
	card, _ := os.ReadFile(filepath.Join(cfg.VaultPath, "personal/ideas/streaming-but-not-4k-bluray-yet.md"))
	if !strings.HasPrefix(string(card), ideaJudgeCard) || !strings.Contains(string(card), "## Added by dreaming") {
		t.Errorf("the card:\n%s", card)
	}
	if _, err := os.Stat(filepath.Join(cfg.VaultPath, "agent/inbox/automate-the-playon-recordings.md")); err != nil {
		t.Errorf("the new idea is not in the inbox: %v", err)
	}
	journal, err := dreaming.OpenJournal(cfg.EngineStateDir)
	if err != nil {
		t.Fatal(err)
	}
	entries, _ := journal.Read()
	var applied, done int
	for _, e := range entries {
		if e.Kind == dreaming.KindApplied && e.Job == ideajudge.Job {
			applied++
		}
		if e.Kind == dreaming.KindRunDone && e.Outcome == "judge-ideas: test" {
			done++
		}
	}
	if applied != 2 || done != 1 {
		t.Errorf("the journal holds %d applied line(s) and %d run-done; want 2 and 1", applied, done)
	}
	ideajudge.MarkApplied(&plan, st, res.AppliedRels, now)
	again, err := ideajudge.Decide(ideajudge.Options{Vault: cfg.VaultPath, MemoryRoot: "agent", Now: now}, st)
	if err != nil {
		t.Fatal(err)
	}
	if len(again.Intents) != 0 {
		t.Errorf("a written verdict was planned again: %+v", again.Rows)
	}
}

// A card the operator edits between the plan and the write is skipped, never
// clobbered, and its verdict waits for the next night.
func TestACardChangedUnderThePlanIsSkipped(t *testing.T) {
	cfg := judgeFixture(t)
	now := time.Date(2026, 10, 17, 9, 0, 0, 0, time.UTC)
	st := &ideajudge.State{}
	plan, err := ideajudge.Decide(ideajudge.Options{Vault: cfg.VaultPath, MemoryRoot: "agent", Now: now,
		Call: judgeAnswers}, st)
	if err != nil {
		t.Fatal(err)
	}
	edited := ideaJudgeCard + "\nThe operator's note, written mid-run.\n"
	cardPath := filepath.Join(cfg.VaultPath, "personal/ideas/streaming-but-not-4k-bluray-yet.md")
	if err := os.WriteFile(cardPath, []byte(edited), 0o644); err != nil {
		t.Fatal(err)
	}
	res, err := dreaming.ApplyIntents(cfg, ideajudge.Job, "", plan.Intents, now, 0)
	if err != nil {
		t.Fatal(err)
	}
	if res.Applied != 1 || res.Skipped != 1 {
		t.Fatalf("%+v, want the inbox card applied and the card skipped", res)
	}
	if got, _ := os.ReadFile(cardPath); string(got) != edited {
		t.Errorf("the edited card was clobbered:\n%s", got)
	}
	ideajudge.MarkApplied(&plan, st, res.AppliedRels, now)
	next, _ := ideajudge.Decide(ideajudge.Options{Vault: cfg.VaultPath, MemoryRoot: "agent", Now: now}, st)
	if len(next.Intents) != 1 || !strings.HasSuffix(next.Intents[0].Rel, "streaming-but-not-4k-bluray-yet.md") {
		t.Errorf("the skipped repeat does not wait for the next night: %+v", next.Intents)
	}
}

// An intent cannot reach past the vault.
func TestAnIntentOutsideTheVaultIsRefused(t *testing.T) {
	cfg := judgeFixture(t)
	_, err := dreaming.ApplyIntents(cfg, ideajudge.Job, "", []dreaming.Intent{{Rel: "../../escape.md",
		After: []byte("x")}}, time.Now(), 0)
	if err == nil || !strings.Contains(err.Error(), "outside the vault") {
		t.Errorf("err = %v", err)
	}
}

// The configured vault root bounds the judge's writes, with or without an
// Obsidian folder at it: the repeat in `personal/ideas/` and the inbox card
// both land (the release review of 2026-10-22).
func TestTheWritesAreBoundedByTheConfiguredVault(t *testing.T) {
	cfg := judgeFixture(t)
	if err := os.RemoveAll(filepath.Join(cfg.VaultPath, ".obsidian")); err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 10, 17, 9, 0, 0, 0, time.UTC)
	plan, err := ideajudge.Decide(ideajudge.Options{Vault: cfg.VaultPath, MemoryRoot: "agent", Now: now,
		Call: judgeAnswers}, &ideajudge.State{})
	if err != nil {
		t.Fatal(err)
	}
	res, err := dreaming.ApplyIntents(cfg, ideajudge.Job, "", plan.Intents, now, 0)
	if err != nil || res.Applied != 2 {
		t.Errorf("ApplyIntents: %v, %+v; want both intents applied inside the configured vault", err, res)
	}
}
