package vcs

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

// backupGit runs git isolated from the machine's global config, which a
// scratch repository otherwise inherits (its hooksPath included).
func backupGit(t *testing.T, dir string, args ...string) {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "GIT_CONFIG_GLOBAL="+os.DevNull, "GIT_CONFIG_NOSYSTEM=1",
		"GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@example.com",
		"GIT_COMMITTER_NAME=t", "GIT_COMMITTER_EMAIL=t@example.com")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
}

func backupCommit(t *testing.T, dir, rel string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(dir, rel), []byte(rel), 0o644); err != nil {
		t.Fatal(err)
	}
	backupGit(t, dir, "add", "-A")
	backupGit(t, dir, "commit", "-q", "-m", rel)
}

func TestBackupReadsThePushAndWhatIsPending(t *testing.T) {
	vault := t.TempDir()
	remote := t.TempDir()
	backupGit(t, remote, "init", "-q", "--bare")
	backupGit(t, vault, "init", "-q", "-b", "main")
	backupCommit(t, vault, "a.md")
	backupGit(t, vault, "remote", "add", "nas", remote)

	r := Open(vault)
	if got := r.Backup("nas"); !got.Configured || got.Tracked || got.Pending != 1 || !got.LastPush.IsZero() {
		t.Fatalf("before any push: %+v, want configured, untracked, 1 pending", got)
	}

	before := time.Now().Add(-time.Second)
	backupGit(t, vault, "push", "-q", "nas", "main")
	got := r.Backup("nas")
	if !got.Tracked || got.Pending != 0 || got.LastPush.Before(before) || got.Err != "" {
		t.Fatalf("after a push: %+v, want tracked, nothing pending, a push time", got)
	}

	backupCommit(t, vault, "b.md")
	backupCommit(t, vault, "c.md")
	got = r.Backup("nas")
	if got.Pending != 2 || got.OldestPending.IsZero() {
		t.Fatalf("after two commits: %+v, want 2 pending with an oldest time", got)
	}
}

func TestBackupWithoutTheRemoteIsNotConfigured(t *testing.T) {
	vault := t.TempDir()
	backupGit(t, vault, "init", "-q", "-b", "main")
	backupCommit(t, vault, "a.md")
	if got := Open(vault).Backup("gitea"); got.Configured {
		t.Fatalf("got %+v, want not configured", got)
	}
}

func TestLastPushIgnoresFetchLines(t *testing.T) {
	p := filepath.Join(t.TempDir(), "main")
	body := "0000 1111 n <e> 1700000000 -0700\tupdate by push\n" +
		"1111 2222 n <e> 1700009999 -0700\tfetch nas: fast-forward\n" +
		"2222 3333 n <e> 1700005000 -0700\tupdate by push\n"
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := lastPush(p); got.Unix() != 1700005000 {
		t.Fatalf("lastPush = %d, want the newest push line, 1700005000", got.Unix())
	}
}
