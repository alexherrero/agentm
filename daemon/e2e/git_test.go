package e2e

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
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

// TestGit_DriveStagingChurnNeverReachesHistory keeps Google Drive's scratch
// space out of the vault's record.
//
// Drive stages every upload through a `.tmp.driveupload` directory, so the vault
// is briefly full of files that are named like notes and are not notes. Two
// separate things currently keep them out: relevant() drops any path with a
// dotted segment, and the Create branch below it is what would otherwise hand a
// new directory to addDirs and start watching it. Remove either and this test
// fails with several dozen staging files committed to history — which is the
// point, because the two guards are one line apart and the second is easy to
// reorder above the first while refactoring.
//
// This is a composite guard, not the regression test for the base-name-only
// filter that preceded it; that distinction is pinned in the watch package,
// where relevant() can be asked directly.
func TestGit_DriveStagingChurnNeverReachesHistory(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)
	gitInit(t, env.vault)

	d := start(t, bin, env)
	defer d.kill(t)

	// A batch that persists, as during a bulk upload...
	for i := range 40 {
		env.write(t, fmt.Sprintf(".tmp.driveupload/%d.md", 3700+i), "in flight\n")
	}
	// ...and a batch that is gone again within a debounce window, which is what
	// the transient half of the churn actually looks like.
	for i := range 40 {
		rel := fmt.Sprintf("personal/2026/08/.tmp.driveupload/%d.md", 3800+i)
		env.write(t, rel, "in flight\n")
		if err := os.Remove(filepath.Join(env.vault, filepath.FromSlash(rel))); err != nil {
			t.Fatal(err)
		}
	}

	// A real note, so we know the pipeline is alive and several reconcile cycles
	// have run past the churn rather than merely not having got to it yet.
	rel := "personal/2026/08/a-real-note.md"
	env.write(t, rel, `---
type: idea
status: unfiled
captured: 2026-08-10T09:00:00Z
---
The note that is actually a note.
`)
	waitForCommit(t, env.vault, rel)

	if paths := committedPaths(t, env.vault); len(paths) > 0 {
		t.Errorf("the sync client's staging files were committed to the vault's "+
			"history: %v", paths)
	}
	if logs := d.logs(); strings.Contains(logs, "commit failed") {
		t.Errorf("transient staging files produced commit attempts.\n  logs: %s", logs)
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

// committedPaths is every path any commit has ever touched under a dot
// directory — the question being whether the sync client's scratch space ever
// entered the record, not whether it is there now.
func committedPaths(t *testing.T, dir string) []string {
	t.Helper()
	cmd := exec.Command("git", "log", "--all", "--pretty=format:", "--name-only")
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git log: %v\n%s", err, out)
	}
	var found []string
	for _, line := range strings.Split(string(out), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		for _, seg := range strings.Split(line, "/") {
			if strings.HasPrefix(seg, ".") {
				found = append(found, line)
				break
			}
		}
	}
	return found
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

// TestGit_NonMarkdownIsCommitted is the whole point of the committer/indexer
// split. The daemon used to commit markdown only, while `agentmd gate
// corpus-write` refused on anything git reported dirty, so every non-markdown
// file fell in the gap: written, never committed, permanently dirty, gate shut.
// The gate has no override, so a single config edit could hold it closed
// indefinitely — which is what it did during the stage-1 four-space migration,
// twice in one session.
func TestGit_NonMarkdownIsCommitted(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)
	gitInit(t, env.vault)

	d := start(t, bin, env)
	defer d.kill(t)

	// Not markdown, so it never enters the index — but git can see it, so the
	// gate can see it, so the daemon has to be able to clear it.
	rel := "_meta/repos.json"
	env.write(t, rel, "{\n  \"version\": 1,\n  \"repos\": []\n}\n")

	waitForCommit(t, env.vault, rel)
}

// TestGit_AnIgnoredFileIsNeverCommitted holds the other side of the same line.
// With the committer asking git rather than filtering by extension, `.gitignore`
// becomes the policy surface — so a file it excludes must stay excluded, and the
// daemon must not decide on its own that a rebuildable cache belongs in history.
func TestGit_AnIgnoredFileIsNeverCommitted(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)
	gitInit(t, env.vault)
	env.write(t, ".gitignore", "*.cache\n")

	d := start(t, bin, env)
	defer d.kill(t)

	env.write(t, "personal/rebuildable.cache", "regenerate me\n")

	// A real note afterwards, so the pipeline has demonstrably run past the
	// ignored file rather than merely not having reached it yet.
	rel := "personal/2026/08/a-real-note.md"
	env.write(t, rel, `---
type: idea
status: unfiled
captured: 2026-08-10T09:00:00Z
---
The note that is actually a note.
`)
	waitForCommit(t, env.vault, rel)

	cmd := exec.Command("git", "log", "--all", "--pretty=format:", "--name-only")
	cmd.Dir = env.vault
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git log: %v\n%s", err, out)
	}
	if strings.Contains(string(out), "rebuildable.cache") {
		t.Errorf("a gitignored file was committed; .gitignore is supposed to be "+
			"the policy surface.\n  history: %s", out)
	}
}

// TestGit_AnUntrackedDotDirectoryFileIsNeverCommitted is the narrow rule that
// keeps the committer's new reach from re-opening the cutover's worst failure.
// Drive stages every upload through `.tmp.driveupload`, and a vault whose
// .gitignore is missing or wrong would otherwise write that churn — above 1,400
// files at its peak — into history, which is the one thing here with no undo.
// The test deliberately runs without a .gitignore, because the daemon's own
// guard is what is under test.
func TestGit_AnUntrackedDotDirectoryFileIsNeverCommitted(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)
	gitInit(t, env.vault)

	d := start(t, bin, env)
	defer d.kill(t)

	env.write(t, ".tmp.driveupload/9001.md", "in flight\n")

	rel := "personal/2026/08/a-real-note.md"
	env.write(t, rel, `---
type: idea
status: unfiled
captured: 2026-08-10T09:00:00Z
---
The note that is actually a note.
`)
	waitForCommit(t, env.vault, rel)

	if paths := committedPaths(t, env.vault); len(paths) > 0 {
		t.Errorf("untracked sync-client staging reached history: %v", paths)
	}
}

// TestGit_ATrackedDotDirectoryFileIsMaintained is the other half of that rule.
// Trackedness is the test rather than a list of directory names: `.obsidian/`
// holds both `workspace.json`, which churns per focus change and is ignored, and
// `app.json`, which the operator deliberately versions. Once a file there is
// tracked, the daemon keeps it up to date — otherwise an Obsidian settings edit
// would sit dirty forever and hold the gate shut, which is exactly the bug.
func TestGit_ATrackedDotDirectoryFileIsMaintained(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)
	gitInit(t, env.vault)

	// Tracked by hand first, the way a real vault's .obsidian/app.json got there.
	env.write(t, ".obsidian/app.json", "{\"alwaysUpdateLinks\": true}\n")
	for _, args := range [][]string{
		{"add", ".obsidian/app.json"},
		{"commit", "-m", "track the Obsidian config"},
	} {
		cmd := exec.Command("git", args...)
		cmd.Dir = env.vault
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v\n%s", args, err, out)
		}
	}

	d := start(t, bin, env)
	defer d.kill(t)

	env.write(t, ".obsidian/app.json",
		"{\"alwaysUpdateLinks\": true, \"userIgnoreFilters\": [\"Agent/personal\"]}\n")

	// Nothing wakes on a dot directory — a long Drive sync would keep resetting
	// the debounce and starve the commit — so this arrives on the reconcile
	// tick's commit instead. That is the floor the rule depends on.
	deadline := time.Now().Add(60 * time.Second)
	for {
		cmd := exec.Command("git", "status", "--porcelain", "--", ".obsidian/app.json")
		cmd.Dir = env.vault
		out, err := cmd.CombinedOutput()
		if err == nil && len(strings.TrimSpace(string(out))) == 0 {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("a tracked file under a dot directory stayed dirty, so the "+
				"gate would stay shut on it.\n  git status: %s", out)
		}
		time.Sleep(500 * time.Millisecond)
	}
}
