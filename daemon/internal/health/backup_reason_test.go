package health

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

// The four backup-stale alerts of 2026-10-24 to 11-01, replayed against the
// push job's log (task 198). Each used to say only "its log says why".

func staleDetail(t *testing.T, rep Report) string {
	t.Helper()
	for _, a := range rep.Alerts {
		if a.Code == AlertBackupStale {
			return a.Detail
		}
	}
	return ""
}

func withLog(in Input, lastRun time.Duration, ok bool, text string, failing int) Input {
	in.Thresholds.BackupStale = 12 * time.Hour
	in.Backup.LogPath, in.Backup.LogFound = "/logs/vault-backup.log", true
	in.Backup.LastRun, in.Backup.LastRunOK = in.Now.Add(-lastRun), ok
	in.Backup.LastRunText, in.Backup.Failing = text, failing
	return in
}

// 10-24: Gitea's SSH refused the 01:47 and 05:47 runs. Two failed runs page at
// once, with the refusal, though the oldest commit is under the threshold.
func TestTwoFailedRunsPageWithTheirReason(t *testing.T) {
	refused := "skip: fetch from gitea failed — ssh: connect to host nas port 2222: Connection refused"
	rep := Evaluate(withLog(backupInput(8*time.Hour, 65, 8*time.Hour), 8*time.Minute, false, refused, 2))
	if !rep.Red() || rep.Backup.State != BackupStale {
		t.Fatalf("level %q, backup %+v; two failed runs with commits waiting is a failure", rep.Level, rep.Backup)
	}
	d := staleDetail(t, rep)
	for _, want := range []string{"last 2 runs failed", "Connection refused", "65 commit(s)"} {
		if !strings.Contains(d, want) {
			t.Errorf("the alert is missing %q: %q", want, d)
		}
	}
	if rep.Backup.Reason == "" || rep.Backup.Failing != 2 {
		t.Errorf("backup = %+v, want the reason and the failing count on the wire", rep.Backup)
	}
}

// 10-30: one run failed ("Host is down") and the next pushed. One failure
// with the oldest commit 8h33m old is not a page.
func TestOneFailedRunUnderTheThresholdIsNotAPage(t *testing.T) {
	rep := Evaluate(withLog(backupInput(8*time.Hour, 30, 8*time.Hour+33*time.Minute),
		3*time.Hour+41*time.Minute, false, "skip: fetch from gitea failed — Host is down", 1))
	if rep.Red() {
		t.Fatalf("one failed run paged: %+v", rep.Alerts)
	}
	if rep.Backup.Failing != 1 || !strings.Contains(rep.Backup.LastRun, "Host is down") {
		t.Errorf("backup = %+v, want the failed run reported", rep.Backup)
	}
}

// 10-25: the Mac slept; launchd held the job and nothing failed. Past the
// threshold it is red, and says no run failed.
func TestALateJobWithNoFailureSaysSo(t *testing.T) {
	rep := Evaluate(withLog(backupInput(16*time.Hour, 51, 15*time.Hour+59*time.Minute),
		16*time.Hour, true, "pushed 49 commit(s) -> 860ed5b8f, verified on gitea", 0))
	d := staleDetail(t, rep)
	for _, want := range []string{"no run has failed", "awake time", "pushed 49 commit(s)"} {
		if !strings.Contains(d, want) {
			t.Errorf("the alert is missing %q: %q", want, d)
		}
	}
}

// 11-01: the same, at 9h — under three runs, so no page at all.
func TestNineHoursWithNoFailureIsNotAPage(t *testing.T) {
	rep := Evaluate(withLog(backupInput(11*time.Hour, 35, 9*time.Hour),
		11*time.Hour, true, "pushed 13 commit(s) -> ac99bf8ca, verified on gitea", 0))
	if rep.Red() {
		t.Fatalf("a job held by a sleeping machine paged at 9h: %+v", rep.Alerts)
	}
}

func TestOneFailedRunPastTheThresholdQuotesIt(t *testing.T) {
	rep := Evaluate(withLog(backupInput(13*time.Hour, 5, 13*time.Hour), time.Hour, false,
		"FAILED after 5 commit(s) pending: — remote rejected", 1))
	if d := staleDetail(t, rep); !strings.Contains(d, "its last run failed 1h0m0s ago: FAILED after 5") {
		t.Errorf("alert = %q", d)
	}
}

func TestNoLogSaysThereIsNoLog(t *testing.T) {
	in := backupInput(13*time.Hour, 5, 13*time.Hour)
	in.Thresholds.BackupStale = 12 * time.Hour
	in.Backup.LogPath = "/logs/vault-backup.log"
	if d := staleDetail(t, Evaluate(in)); !strings.Contains(d, "left no log at /logs/vault-backup.log") {
		t.Errorf("alert = %q", d)
	}
	in.Backup.LogPath = ""
	if d := staleDetail(t, Evaluate(in)); !strings.Contains(d, "`daemon.backup_log`") {
		t.Errorf("alert = %q", d)
	}
}

// Two failed runs with nothing waiting is nothing to back up: no page.
func TestFailedRunsWithNothingWaitingAreNotAPage(t *testing.T) {
	rep := Evaluate(withLog(backupInput(9*time.Hour, 0, 0), time.Hour, false, "skip: fetch failed", 2))
	if rep.Red() {
		t.Fatalf("paged with nothing waiting: %+v", rep.Alerts)
	}
}

// The fields the doctor's backup-push row reads (scripts/test_backup_push_row.py).
func TestTheBackupReasonOnTheWire(t *testing.T) {
	in := withLog(backupInput(8*time.Hour, 65, 8*time.Hour), 8*time.Minute, false, "skip: fetch failed", 2)
	blob, err := json.Marshal(Evaluate(in))
	if err != nil {
		t.Fatal(err)
	}
	var wire struct {
		Backup map[string]any `json:"backup"`
	}
	if err := json.Unmarshal(blob, &wire); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"reason", "last_run", "last_run_at"} {
		if s, _ := wire.Backup[k].(string); s == "" {
			t.Errorf("backup.%s missing\n  %s", k, blob)
		}
	}
	if wire.Backup["failing"] != float64(2) {
		t.Errorf("backup.failing = %v\n  %s", wire.Backup["failing"], blob)
	}
}
