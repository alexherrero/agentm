package e2e

import (
	"os/exec"
	"strings"
	"testing"
	"time"
)

// The daemon is the only thing that runs git, and every change it sees is
// committed with attribution. None of the tests in roundtrip_test.go exercise
// that, because a throwaway vault is not a repository — so without this file the
// commit path would ship entirely unverified, which is the shape of failure this
// whole build exists to end.
//
// These tests initialize a real repository in the temp vault, which is also the
// only honest way to test it: the operator's own vault is not a git repository
// yet, and the daemon must never make it one on its own initiative.

func TestGit_CaptureIsCommittedWithAttribution(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)
	gitInit(t, env.vault)

	d := start(t, bin, env)
	defer d.kill(t)

	res := d.capture(t, captureArgs{
		Title:  "Git is the undo story",
		Text:   "A private repository is sync, backup, and history in one, which is what makes a bad write revertible by construction.",
		Type:   "convention",
		Status: "active",
	})
	path := res.str(t, "path")

	subject, body := waitForCommit(t, env.vault, path)
	if !strings.Contains(body, "origin: capture") {
		t.Errorf("commit for a capture is not attributed to capture.\n  subject: %s\n  body: %s",
			subject, body)
	}
	if !strings.Contains(subject, "memory: capture") {
		t.Errorf("commit subject %q does not name what happened", subject)
	}
}

func TestGit_OutsideEditIsAttributedToItsOrigin(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)
	gitInit(t, env.vault)

	d := start(t, bin, env)
	defer d.kill(t)

	rel := "personal/2026/08/edited-in-obsidian.md"
	env.write(t, rel, `---
type: idea
status: unfiled
captured: 2026-08-08T07:30:00Z
---
An edit the daemon did not make itself.
`)

	_, body := waitForCommit(t, env.vault, rel)
	if !strings.Contains(body, "origin: local-edit") {
		t.Errorf("an edit the daemon did not make was not attributed as one.\n  body: %s", body)
	}
}

// TestGit_PhoneEditIsMarkedPhoneOriginated covers the requirement directly: edits
// arriving from the phone are noted as phone-originated. The trigger is a
// configured sync path, which is empty until Syncthing lands — so the mechanism is
// tested here with the path configured, rather than tested when it is too late to
// find out it never worked.
func TestGit_PhoneEditIsMarkedPhoneOriginated(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)
	gitInit(t, env.vault)
	env.setConfigKey(t, "daemon.phone_paths", []string{"Calendar"})

	d := start(t, bin, env)
	defer d.kill(t)

	rel := "Calendar/2026-08-08.md"
	env.write(t, rel, `---
type: idea
status: unfiled
captured: 2026-08-08T07:30:00Z
---
Captured in the daily note on the phone.
`)

	_, body := waitForCommit(t, env.vault, rel)
	if !strings.Contains(body, "origin: phone") {
		t.Errorf("an edit under the phone's sync set was not marked phone-originated.\n  body: %s", body)
	}
}

// TestGit_MissingRepositoryDegradesLoudly is principle 4 as a test: a capability
// the daemon does not have must say so. The operator's vault is not a repository
// today, so this is the path that actually runs on his machine.
func TestGit_MissingRepositoryDegradesLoudly(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t) // deliberately not a git repo

	d := start(t, bin, env)
	defer d.kill(t)

	// Capture still works — a missing undo story does not block remembering.
	res := d.capture(t, captureArgs{
		Text:   "Capture does not depend on git being available.",
		Type:   "convention",
		Status: "active",
	})
	if res.str(t, "path") == "" {
		t.Fatal("capture failed with no git repository present")
	}

	logs := d.logs()
	if !strings.Contains(logs, "git DEGRADED") {
		t.Errorf("a missing git repository was not reported loudly at startup.\n  logs: %s", logs)
	}
	// And it must not have quietly created one.
	if isGitRepo(env.vault) {
		t.Error("the daemon initialized a git repository in the vault on its own " +
			"initiative; the git-transport migration is deliberately a later, " +
			"operator-run step")
	}
}

// ---------------------------------------------------------------------------

func gitInit(t *testing.T, dir string) {
	t.Helper()
	for _, args := range [][]string{
		{"init", "--initial-branch=main"},
		{"config", "user.email", "test@example.com"},
		{"config", "user.name", "test"},
	} {
		cmd := exec.Command("git", args...)
		cmd.Dir = dir
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v\n%s", args, err, out)
		}
	}
}

func isGitRepo(dir string) bool {
	cmd := exec.Command("git", "rev-parse", "--git-dir")
	cmd.Dir = dir
	return cmd.Run() == nil
}

// waitForCommit polls until `path` appears in a commit, then returns that commit's
// subject and full message.
func waitForCommit(t *testing.T, dir, path string) (subject, body string) {
	t.Helper()
	deadline := time.Now().Add(30 * time.Second)
	for {
		cmd := exec.Command("git", "log", "--format=%H%x00%s%x00%B%x01", "--", path)
		cmd.Dir = dir
		out, err := cmd.CombinedOutput()
		if err == nil && len(strings.TrimSpace(string(out))) > 0 {
			entry := strings.Split(string(out), "\x01")[0]
			parts := strings.Split(entry, "\x00")
			if len(parts) >= 3 {
				return parts[1], parts[2]
			}
		}
		if time.Now().After(deadline) {
			cmd := exec.Command("git", "log", "--oneline", "--all")
			cmd.Dir = dir
			all, _ := cmd.CombinedOutput()
			t.Fatalf("no commit ever touched %s.\n  git log: %s", path, all)
		}
		time.Sleep(250 * time.Millisecond)
	}
}
