package health

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

// backupInput is baseInput with a backup remote, pushed `pushed` ago, and
// `pending` commits waiting since `oldest` ago.
func backupInput(pushed time.Duration, pending int, oldest time.Duration) Input {
	in := baseInput()
	in.Thresholds.BackupStale = 8 * time.Hour
	in.Backup = BackupInput{Remote: "gitea", Configured: true, Tracked: true,
		LastPush: in.Now.Add(-pushed), Pending: pending}
	if pending > 0 {
		in.Backup.OldestPending = in.Now.Add(-oldest)
	}
	return in
}

func TestABackupPushedTwoHoursAgoIsCurrent(t *testing.T) {
	rep := Evaluate(backupInput(2*time.Hour, 12, 90*time.Minute))
	if rep.Red() || rep.Backup.State != BackupCurrent {
		t.Fatalf("level %q, backup %+v; a push two hours ago is a working job", rep.Level, rep.Backup)
	}
	if rep.Backup.LastPushAge != Duration(2*time.Hour) || rep.Backup.Pending != 12 {
		t.Errorf("backup = %+v, want the push age and the 12 waiting", rep.Backup)
	}
}

func TestABackupNineHoursBehindGoesRed(t *testing.T) {
	rep := Evaluate(backupInput(9*time.Hour, 140, 8*time.Hour+30*time.Minute))
	if !rep.Red() || rep.Backup.State != BackupStale {
		t.Fatalf("level %q, backup %+v; a commit waiting past two runs is a stopped job", rep.Level, rep.Backup)
	}
	var detail string
	for _, a := range rep.Alerts {
		if a.Code == AlertBackupStale {
			detail = a.Detail
		}
	}
	for _, want := range []string{"140 commit(s)", "`gitea`", "8h30m", "last push was 9h0m"} {
		if !strings.Contains(detail, want) {
			t.Errorf("the alert is missing %q: %q", want, detail)
		}
	}
	if !strings.Contains(rep.Backup.String(), "STALE") {
		t.Errorf("the status line does not say stale: %q", rep.Backup)
	}
}

// Nothing new to send is not a stopped job, however long ago the last push.
func TestAnOldPushWithNothingWaitingIsCurrent(t *testing.T) {
	rep := Evaluate(backupInput(30*time.Hour, 0, 0))
	if rep.Red() || rep.Backup.State != BackupCurrent {
		t.Fatalf("level %q, backup %+v", rep.Level, rep.Backup)
	}
}

func TestAVaultThatWasNeverPushedGoesRedOnceCommitsAge(t *testing.T) {
	in := backupInput(0, 3, 10*time.Hour)
	in.Backup.Tracked, in.Backup.LastPush = false, time.Time{}
	rep := Evaluate(in)
	if !rep.Red() || !strings.Contains(rep.Alerts[0].Detail, "never received a push") {
		t.Fatalf("level %q, alerts %+v", rep.Level, rep.Alerts)
	}
}

func TestNoBackupRemoteIsReportedNotAlerted(t *testing.T) {
	rep := Evaluate(baseInput())
	if rep.Red() || rep.Backup.State != BackupNone {
		t.Fatalf("level %q, backup %+v", rep.Level, rep.Backup)
	}
	if rep.Backup.String() != "no backup remote configured" {
		t.Errorf("line = %q", rep.Backup)
	}
}

func TestAnUnreadableBackupIsUnknownNotRed(t *testing.T) {
	in := backupInput(time.Hour, 0, 0)
	in.Backup.Err = "git could not list the commits the backup lacks"
	rep := Evaluate(in)
	if rep.Red() || rep.Backup.State != BackupUnknown {
		t.Fatalf("level %q, backup %+v", rep.Level, rep.Backup)
	}
}

// The doctor's backup-push row reads these names from `agentmd status --json`
// (scripts/test_backup_push_row.py builds its payloads from them).
func TestTheBackupFieldsOnTheWire(t *testing.T) {
	in := backupInput(9*time.Hour, 140, 8*time.Hour+30*time.Minute)
	in.Backup.LastPush = time.Date(2026, 10, 23, 8, 47, 0, 0, time.UTC)
	in.Now = in.Backup.LastPush.Add(9 * time.Hour)
	in.Backup.OldestPending = in.Now.Add(-(8*time.Hour + 30*time.Minute))
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
	want := map[string]any{
		"state":              "stale",
		"remote":             "gitea",
		"last_push_at":       "2026-10-23T08:47:00Z",
		"last_push_age":      "9h0m0s",
		"pending":            float64(140),
		"oldest_pending_age": "8h30m0s",
	}
	for k, v := range want {
		if wire.Backup[k] != v {
			t.Errorf("backup.%s = %v, want %v\n  %s", k, wire.Backup[k], v, blob)
		}
	}
}
