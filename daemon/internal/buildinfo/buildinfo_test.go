package buildinfo

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime/debug"
	"strings"
	"testing"
)

func TestFromSettingsReadsGoStamp(t *testing.T) {
	got := fromSettings([]debug.BuildSetting{
		{Key: "vcs", Value: "git"},
		{Key: "vcs.revision", Value: "abc123def4567890"},
		{Key: "vcs.modified", Value: "true"},
	})
	want := Info{Commit: "abc123def4567890", Modified: true, Stamp: StampGo}
	if got != want {
		t.Fatalf("got %+v, want %+v", got, want)
	}
	if got.Short() != "abc123de+modified" {
		t.Errorf("Short = %q", got.Short())
	}
}

func TestFromSettingsWithoutVCSIsNone(t *testing.T) {
	got := fromSettings([]debug.BuildSetting{{Key: "vcs.modified", Value: "true"}})
	if got != (Info{Stamp: StampNone}) {
		t.Fatalf("got %+v", got)
	}
	if got.Short() != "unknown" {
		t.Errorf("Short = %q", got.Short())
	}
}

func TestReadPrefersTheScriptStamp(t *testing.T) {
	commit, modified = "feedfacecafebeef", "false"
	defer func() { commit, modified = "", "" }()
	if got := Read(); got != (Info{Commit: "feedfacecafebeef", Stamp: StampScript}) {
		t.Fatalf("got %+v", got)
	}
}

func TestCompareCloneCountsDaemonCommitsOnly(t *testing.T) {
	dir := t.TempDir()
	run(t, dir, "init", "-q")
	commitFile(t, dir, "daemon/a.go", "one")
	base := strings.TrimSpace(run(t, dir, "rev-parse", "HEAD"))
	commitFile(t, dir, "harness/x.py", "two")
	commitFile(t, dir, "daemon/b.go", "three")

	got := CompareClone(dir, base)
	if got.Err != "" || got.Ahead != 2 || got.DaemonAhead != 1 || got.Behind != 0 {
		t.Fatalf("got %+v, want 2 ahead, 1 touching daemon/", got)
	}

	level := CompareClone(dir, got.Head)
	if level.Err != "" || level.Ahead != 0 || level.Behind != 0 {
		t.Fatalf("level: got %+v", level)
	}
}

func TestCompareCloneUnknownCommitIsAnErrorNotAZero(t *testing.T) {
	dir := t.TempDir()
	run(t, dir, "init", "-q")
	commitFile(t, dir, "daemon/a.go", "one")
	got := CompareClone(dir, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
	if !strings.Contains(got.Err, "not in this clone") {
		t.Fatalf("got %+v", got)
	}
}

func TestCompareCloneNotAClone(t *testing.T) {
	if got := CompareClone(t.TempDir(), "abc"); got.Err == "" {
		t.Fatalf("got %+v", got)
	}
}

// run is git in a scratch repository, isolated from the machine's global
// config: scratch repos otherwise inherit its hooksPath.
func run(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "GIT_CONFIG_GLOBAL="+os.DevNull, "GIT_CONFIG_NOSYSTEM=1",
		"GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@example.com",
		"GIT_COMMITTER_NAME=t", "GIT_COMMITTER_EMAIL=t@example.com")
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return string(out)
}

func commitFile(t *testing.T, dir, rel, body string) {
	t.Helper()
	p := filepath.Join(dir, rel)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	run(t, dir, "add", "-A")
	run(t, dir, "commit", "-q", "-m", rel)
}
