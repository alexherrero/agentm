package vcs

import (
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"
)

// The daemon owns the vault repository's packing: the repository's own config
// turns git's automatic maintenance off for every client, and the daemon
// repacks once a day into the one pack shape go-git reads. See maintain.go.

func TestOwnMaintenance_SetsTwoKeysInTheRepoAndNothingElse(t *testing.T) {
	dir, _ := newCLIRepo(t)
	// A global config that says the opposite, so "untouched" is a visible
	// claim and "the repository's value wins" is too.
	global := os.Getenv("GIT_CONFIG_GLOBAL")
	globalBody := "[gc]\n\tauto = 6700\n[maintenance]\n\tauto = true\n"
	if err := os.WriteFile(global, []byte(globalBody), 0o644); err != nil {
		t.Fatal(err)
	}
	localBefore := configList(t, dir, "--local")

	r := Open(dir)
	logs := captureLog(r)
	if err := r.OwnMaintenance(); err != nil {
		t.Fatalf("OwnMaintenance: %v", err)
	}

	for key, want := range map[string]string{"gc.auto": "0", "maintenance.auto": "false"} {
		if got := gitCLI(t, dir, "config", "--local", "--get", key); got != want {
			t.Errorf("repo-local %s = %q, want %q", key, got, want)
		}
		if got := gitCLI(t, dir, "config", "--get", key); got != want {
			t.Errorf("effective %s = %q, want the repository's %q over the global", key, got, want)
		}
	}
	if got, err := os.ReadFile(global); err != nil || string(got) != globalBody {
		t.Fatalf("the global config changed: %q, %v", got, err)
	}
	var added []string
	for _, line := range configList(t, dir, "--local") {
		if !contains(localBefore, line) {
			added = append(added, line)
		}
	}
	sort.Strings(added)
	if want := []string{"gc.auto=0", "maintenance.auto=false"}; strings.Join(added, ",") != strings.Join(want, ",") {
		t.Fatalf("the repository config gained %v, want exactly %v", added, want)
	}
	for _, line := range localBefore {
		if !contains(configList(t, dir, "--local"), line) {
			t.Errorf("the repository config lost %q", line)
		}
	}
	if n := strings.Count(logs.String(), "set repo-local git config"); n != 2 {
		t.Fatalf("logged %d changes, want 2:\n%s", n, logs)
	}

	// Already owned: nothing to change, nothing to say.
	logs.Reset()
	if err := r.OwnMaintenance(); err != nil {
		t.Fatal(err)
	}
	if logs.Len() != 0 {
		t.Fatalf("a second call changed something:\n%s", logs)
	}
}

func TestMaintainIfDue_FoldsEverythingIntoOnePackAndKeepsEveryObject(t *testing.T) {
	dir, gitDir := newCLIRepo(t)
	gitCLI(t, dir, "gc", "-q")
	commitCLI(t, dir, "personal/kept.md", note+"Second.\n")
	gitCLI(t, dir, "repack", "-q") // a second pack beside the first
	commitCLI(t, dir, "personal/kept.md", note+"Third.\n")
	orphan := hashObject(t, dir, "an orphan nothing refers to\n")
	if packs := packNames(t, gitDir); len(packs) != 2 {
		t.Fatalf("fixture: want two packs, have %v", packs)
	}
	if looseObjectCount(t, gitDir) == 0 {
		t.Fatalf("fixture: want loose objects")
	}
	before := allObjects(t, dir)

	r := Open(dir)
	logs := captureLog(r)
	now := time.Now().Add(MaintenanceEvery + time.Hour)
	ran, err := r.MaintainIfDue(now)
	if err != nil || !ran {
		t.Fatalf("MaintainIfDue = %v, %v\n%s", ran, err, logs)
	}

	after := allObjects(t, dir)
	for _, h := range before {
		if !contains(after, h) {
			t.Errorf("object %s did not survive the daily repack", h)
		}
	}
	if !contains(after, orphan) {
		t.Errorf("the unreachable object %s was dropped", orphan)
	}
	packs := packNames(t, gitDir)
	if len(packs) != 1 || !strings.HasPrefix(packs[0], "pack-") {
		t.Fatalf("after the daily repack the packs are %v, want one pack-", packs)
	}
	if n := looseObjectCount(t, gitDir); n != 0 {
		t.Fatalf("%d loose objects survived the daily repack", n)
	}
	if !strings.Contains(logs.String(), `msg="daily repack"`) {
		t.Errorf("the repack did not log itself:\n%s", logs)
	}

	if again, err := r.MaintainIfDue(now.Add(time.Hour)); again || err != nil {
		t.Fatalf("a second repack ran an hour later (%v, %v)", again, err)
	}
	mustWrite(t, filepath.Join(dir, "personal", "kept.md"), note+"After the repack.\n")
	if hash, err := r.Commit(OriginLocal, []string{"personal/kept.md"}); err != nil || hash == "" {
		t.Fatalf("the daemon could not commit after its own repack: %q, %v", hash, err)
	}
}

// The day is counted from the newest pack's index, so a restart doesn't reset
// it and a pack written an hour ago isn't repacked again.
func TestMaintainIfDue_WaitsADayFromTheNewestPack(t *testing.T) {
	dir, gitDir := newCLIRepo(t)
	gitCLI(t, dir, "gc", "-q")
	hashObject(t, dir, "a loose object to fold\n")
	packs := packNames(t, gitDir)

	r := Open(dir)
	if ran, err := r.MaintainIfDue(time.Now()); ran || err != nil {
		t.Fatalf("repacked a pack written moments ago (%v, %v)", ran, err)
	}
	if got := packNames(t, gitDir); strings.Join(got, ",") != strings.Join(packs, ",") || looseObjectCount(t, gitDir) != 1 {
		t.Fatalf("something was repacked before it was due: %v", got)
	}
	if ran, err := r.MaintainIfDue(time.Now().Add(MaintenanceEvery + time.Minute)); !ran || err != nil {
		t.Fatalf("the repack did not run once due (%v, %v)", ran, err)
	}
}

// A repository that has never been packed waits a day from the first check
// rather than repacking on a freshly started daemon's first tick.
func TestMaintainIfDue_ANeverPackedRepositoryWaitsADayFromTheFirstCheck(t *testing.T) {
	dir, gitDir := newCLIRepo(t)
	if packs := packNames(t, gitDir); len(packs) != 0 || looseObjectCount(t, gitDir) == 0 {
		t.Fatalf("fixture: want loose objects and no pack, have %v", packs)
	}
	r := Open(dir)
	start := time.Now()
	if ran, err := r.MaintainIfDue(start); ran || err != nil {
		t.Fatalf("repacked on the first check (%v, %v)", ran, err)
	}
	if ran, err := r.MaintainIfDue(start.Add(MaintenanceEvery - time.Minute)); ran || err != nil {
		t.Fatalf("repacked inside the first day (%v, %v)", ran, err)
	}
	if ran, err := r.MaintainIfDue(start.Add(MaintenanceEvery)); !ran || err != nil {
		t.Fatalf("did not repack a day after the first check (%v, %v)", ran, err)
	}
}

func TestMaintainIfDue_NothingToFoldIsNotARepack(t *testing.T) {
	dir, gitDir := newCLIRepo(t)
	gitCLI(t, dir, "gc", "-q")
	idx, _ := filepath.Glob(filepath.Join(gitDir, "objects", "pack", "pack-*.idx"))
	if len(idx) != 1 || looseObjectCount(t, gitDir) != 0 {
		t.Fatalf("fixture: want one pack and no loose objects, have %v", idx)
	}
	before, err := os.Stat(idx[0])
	if err != nil {
		t.Fatal(err)
	}

	r := Open(dir)
	logs := captureLog(r)
	if ran, err := r.MaintainIfDue(time.Now().Add(MaintenanceEvery + time.Hour)); ran || err != nil {
		t.Fatalf("repacked a repository with nothing to fold (%v, %v)", ran, err)
	}
	// The log is the check that can fail: git names a pack by its content, so
	// an identical repack would leave the same file in place.
	if strings.Contains(logs.String(), "daily repack") {
		t.Fatalf("a repack ran with nothing to fold:\n%s", logs)
	}
	if after, err := os.Stat(idx[0]); err != nil || !after.ModTime().Equal(before.ModTime()) {
		t.Fatalf("the only pack was rewritten: %v", err)
	}
}

// ---------------------------------------------------------------------------

func configList(t *testing.T, dir string, scope string) []string {
	t.Helper()
	return strings.Split(gitCLI(t, dir, "config", scope, "--list"), "\n")
}

func commitCLI(t *testing.T, dir, rel, body string) {
	t.Helper()
	mustWrite(t, filepath.Join(dir, filepath.FromSlash(rel)), body)
	gitCLI(t, dir, "add", rel)
	gitCLI(t, dir, "commit", "-q", "-m", "edit "+rel)
}
