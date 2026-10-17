package dreaming

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// The idea judge's night is on the facet (task 192): its counts, each card it
// wrote, and that a reporting night wrote nothing.
func TestTheFacetReportsTheIdeaJudgesNight(t *testing.T) {
	vault := t.TempDir()
	root := filepath.Join(vault, "agent")
	for _, d := range []string{root, filepath.Join(vault, ".obsidian"), filepath.Join(vault, "calendar")} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	now := time.Date(2026, 10, 17, 3, 0, 0, 0, time.UTC)
	rep := &Report{IdeaJudge: &IdeaJudgeSummary{At: now, Mode: "apply", Remarks: 9, Judged: 6, Steering: 3,
		Repeats: 1, New: 2, Removed: 1, Deferred: 2,
		Written: []string{"agent/inbox/automate-the-playon-recordings.md", "personal/ideas/streaming.md"}}}
	plan := PlanDreamingFacet(root, axisContract(t), rep, now)
	if len(plan.Intents) != 1 {
		t.Fatalf("%d intents; skipped %q", len(plan.Intents), plan.Skipped)
	}
	body := string(plan.Intents[0].After)
	for _, want := range []string{"## Ideas judged", "6 remark(s) judged: 3 steering, 1 repeat(s), 2 new",
		"1 you had removed, dropped without a call", "2 past the night's cap", "agent/inbox/automate-the-playon-recordings.md"} {
		if !strings.Contains(body, want) {
			t.Errorf("the facet lacks %q:\n%s", want, body)
		}
	}
	rep.IdeaJudge.Mode = "report"
	body = string(PlanDreamingFacet(root, axisContract(t), rep, now).Intents[0].After)
	if !strings.Contains(body, "reporting only, nothing written") {
		t.Errorf("a reporting night does not say so:\n%s", body)
	}
}

// Only the day's summary is the night's: yesterday's counts are not tonight's.
func TestTheJudgesSummaryIsReadForItsOwnDayOnly(t *testing.T) {
	state := t.TempDir()
	at := time.Date(2026, 10, 17, 2, 30, 0, 0, time.UTC)
	if err := WriteIdeaJudgeSummary(state, IdeaJudgeSummary{At: at, Mode: "apply", Judged: 4}); err != nil {
		t.Fatal(err)
	}
	if s := ReadIdeaJudgeSummary(state, at.Add(time.Hour)); s == nil || s.Judged != 4 {
		t.Errorf("the day's summary: %+v", s)
	}
	if s := ReadIdeaJudgeSummary(state, at.Add(24*time.Hour)); s != nil {
		t.Errorf("yesterday's summary was read as tonight's: %+v", s)
	}
	if s := ReadIdeaJudgeSummary(t.TempDir(), at); s != nil {
		t.Errorf("no summary read as %+v", s)
	}
}
