package pulse

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// A log in the push job's own shape: a timestamped line per run, the reason
// indented beneath.
const backupLog = `2026-10-23 21:47:55  pushed 155 commit(s) -> 98678c6fd, verified on gitea
2026-10-24 01:47:55  skip: fetch from gitea failed —
          ssh: connect to host nas port 2222: Connection refused
          fatal: Could not read from remote repository.
2026-10-24 05:47:55  skip: fetch from gitea failed —
          ssh: connect to host nas port 2222: Connection refused
          fatal: Could not read from remote repository.
`

func write(t *testing.T, dir, name, body string) string {
	t.Helper()
	p := filepath.Join(dir, name)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestReadBackupLogCountsTheFailuresInARow(t *testing.T) {
	p := write(t, t.TempDir(), "vault-backup.log", backupLog)
	got := ReadBackupLog(p)
	if !got.Found || got.Failing != 2 || got.LastRunOK {
		t.Fatalf("got %+v, want two failed runs in a row", got)
	}
	want := time.Date(2026, 10, 24, 5, 47, 55, 0, time.Local)
	if !got.LastRun.Equal(want) {
		t.Errorf("last run %s, want %s", got.LastRun, want)
	}
	if got.LastRunText != "skip: fetch from gitea failed — ssh: connect to host nas port 2222: Connection refused" {
		t.Errorf("last run text %q", got.LastRunText)
	}
}

func TestReadBackupLogASuccessEndsTheRun(t *testing.T) {
	p := write(t, t.TempDir(), "vault-backup.log",
		backupLog+"2026-10-24 13:48:06  pushed 152 commit(s) -> ba941e575, verified on gitea\n")
	got := ReadBackupLog(p)
	if got.Failing != 0 || !got.LastRunOK || !strings.HasPrefix(got.LastRunText, "pushed 152") {
		t.Fatalf("got %+v", got)
	}
}

// A run that logs several lines under one timestamp is one run.
func TestReadBackupLogOneRunManyLines(t *testing.T) {
	p := write(t, t.TempDir(), "vault-backup.log", "2026-10-01 10:00:00  ok: already current\n"+
		"2026-10-01 14:00:00  REFUSING: diverged — 3 ahead, 1 behind.\n"+
		"2026-10-01 14:00:00            The backup has commits the vault does not.\n")
	got := ReadBackupLog(p)
	if got.Failing != 1 || !strings.HasPrefix(got.LastRunText, "REFUSING: diverged") {
		t.Fatalf("got %+v", got)
	}
}

func TestReadBackupLogAbsent(t *testing.T) {
	if got := ReadBackupLog(filepath.Join(t.TempDir(), "none.log")); got.Found {
		t.Fatalf("got %+v", got)
	}
	if got := ReadBackupLog(""); got.Found {
		t.Fatalf("got %+v", got)
	}
}

func TestReadRunner(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "runner")
	write(t, dir, "last-cycle.json", `{"at": 1800000000.5, "outcomes": []}`)
	write(t, dir, "dream.json", `{"status": "done", "last_run": 1800000000}`)
	write(t, dir, "enrich-nightly.json", `{"status": "start", "started_at": 1800000600}`)
	write(t, dir, "enrich-nightly.watchdog.json", `{"status": "start", "started_at": 1800009999}`)
	write(t, root, "telemetry/runner-launchd.log", "{\n}\nTraceback (most recent call last):\n"+
		"runner.manifest.ManifestError: PyYAML not available — cannot parse job manifests\n")
	got := ReadRunner(dir)
	if !got.Found || got.LastCycle.Unix() != 1800000000 {
		t.Fatalf("got %+v", got)
	}
	if got.Running != "enrich-nightly" || got.RunningSince.Unix() != 1800000600 {
		t.Errorf("running %q since %s", got.Running, got.RunningSince)
	}
	if !strings.HasPrefix(got.StartupError, "runner.manifest.ManifestError: PyYAML") {
		t.Errorf("startup error %q", got.StartupError)
	}
}

func TestReadRunnerALogEndingInACycleIsNoError(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "runner")
	write(t, dir, "last-cycle.json", `{"at": 1800000000}`)
	write(t, root, "telemetry/runner-launchd.log", "{\n  \"skipped_reason\": \"not available\"\n}\n")
	if got := ReadRunner(dir); got.StartupError != "" || got.Running != "" {
		t.Fatalf("got %+v", got)
	}
}

func TestReadRunnerNoCycle(t *testing.T) {
	if got := ReadRunner(t.TempDir()); got.Found {
		t.Fatalf("got %+v", got)
	}
}

func TestLastWakeIsNotInTheFuture(t *testing.T) {
	if w := LastWake(); w.After(time.Now()) {
		t.Fatalf("last wake %s is in the future", w)
	}
}
