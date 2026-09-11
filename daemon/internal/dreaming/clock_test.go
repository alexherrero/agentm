package dreaming

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// agentm-vault plan 04, task 4: the binary's clock moves only on a pass that
// changed the corpus, and promote writes candidates, never crystallized notes.

// A diagnostic run by hand in the afternoon does not push the next night's
// applying pass back: the next pass is due by the interval from the last
// applying pass, and the report pass has its own stamp.
func TestAReportPassDoesNotMoveTheClock(t *testing.T) {
	cfg, root := scratchConfig(t)
	night := time.Date(2026, 9, 12, 2, 30, 0, 0, time.UTC)
	writeNote(t, root, "memory/semantic/n.md", "active", 10, night, "")

	if _, err := Run(cfg, Options{Now: night, Force: true, Apply: true}); err != nil {
		t.Fatal(err)
	}
	afternoon := night.Add(11 * time.Hour)
	if _, err := Run(cfg, Options{Now: afternoon, Force: true}); err != nil {
		t.Fatal(err)
	}
	st, _ := LoadState(cfg.EngineStateDir)
	if !st.LastDone.Equal(night) {
		t.Errorf("a report pass moved LastDone to %v; it must stay at the applying pass's %v",
			st.LastDone, night)
	}
	if !st.LastReport.Equal(afternoon) {
		t.Errorf("the report pass did not record its own stamp: %v", st.LastReport)
	}

	// The next night, with activity since: due, measured from the applying pass.
	writeNote(t, root, "memory/semantic/fresh.md", "active", 0, night.Add(20*time.Hour), "")
	next, err := Run(cfg, Options{Now: night.Add(24 * time.Hour), Every: 12 * time.Hour, Apply: true})
	if err != nil {
		t.Fatal(err)
	}
	if !next.Decision.Due {
		t.Errorf("the next night's pass was not due: %s", next.Decision.Reason)
	}
}

// Promote run through an applying pass writes the semantic candidate and
// nothing under crystallized/.
func TestAnApplyingPassPromotesIntoSemanticOnly(t *testing.T) {
	cfg, root := scratchConfig(t)
	writeFixtureTraces(t, root)
	now := time.Date(2026, 9, 12, 2, 30, 0, 0, time.UTC)
	rep, err := Run(cfg, Options{Now: now, Force: true, Apply: true})
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.Promote.Promotions) != 1 {
		t.Fatalf("promotions = %+v", rep.Promote.Promotions)
	}
	if _, err := os.Stat(filepath.Join(root, rep.Promote.Promotions[0].Rel)); err != nil {
		t.Errorf("the candidate was not written: %v", err)
	}
	entries, _ := os.ReadDir(filepath.Join(root, "memory", "crystallized"))
	if len(entries) != 0 {
		t.Errorf("an applying pass wrote into crystallized/: %v", entries)
	}
	_ = filepath.Walk(root, func(p string, info os.FileInfo, err error) error {
		if err == nil && !info.IsDir() && strings.Contains(p, "zorbulax") {
			t.Errorf("a note for zorbulax exists: %s", p)
		}
		return nil
	})
}
