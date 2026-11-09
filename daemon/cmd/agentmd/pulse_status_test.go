package main

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/health"
	"github.com/alexherrero/agentm/daemon/internal/pulse"
)

// Task 198: a runner whose last-cycle.json was aged by hand reads stopped in
// `agentmd status`, goes red with the startup error, and the alert subject
// names it. The reading goes through pulse.ReadRunner on a scratch directory,
// as main.go's report does.
func TestStatusShowsAStoppedRunner(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "runner")
	for _, d := range []string{dir, filepath.Join(root, "telemetry")} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	now := time.Now()
	aged := strconv.FormatInt(now.Add(-12*time.Hour).Unix(), 10)
	if err := os.WriteFile(filepath.Join(dir, "last-cycle.json"), []byte(`{"at": `+aged+`}`), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "telemetry", "runner-launchd.log"), []byte(
		"Traceback (most recent call last):\n"+
			"runner.manifest.ManifestError: PyYAML not available — cannot parse job manifests\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	rn := pulse.ReadRunner(dir)
	rep := health.Evaluate(health.Input{
		Now: now, GitAvailable: true, Thresholds: thresholds(&config.Config{}),
		Runner: health.RunnerInput{Found: rn.Found, LastCycle: rn.LastCycle,
			StartupError: rn.StartupError, Err: rn.Err},
	})
	out := renderReport(rep, &config.Config{VaultPath: "/v"})
	for _, want := range []string{"  runner   the last cycle began 12h0m", "PyYAML not available", "runner-stale"} {
		if !strings.Contains(out, want) {
			t.Errorf("status is missing %q:\n%s", want, out)
		}
	}
	if !strings.Contains(alertSubject(rep), "runner-stale") {
		t.Errorf("subject %q does not name the runner", alertSubject(rep))
	}
}
