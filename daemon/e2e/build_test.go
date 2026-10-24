package e2e

import (
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// The build commit, stamped at build time and read back (#819).
//
// `agentmd status` used to print `0.1.0-dev` whatever was built, so a daemon
// behind main looked current. This builds the real binary with the flags
// scripts/daemon-ldflags.sh prints, against a scratch clone one daemon/ commit
// ahead of the stamped commit, and reads the stamp back from /status's JSON and
// the comparison from the status text.
func TestStatusNamesTheStampedBuildCommit(t *testing.T) {
	clone := t.TempDir()
	scratchGit(t, clone, "init", "-q")
	writeFile(t, filepath.Join(clone, "daemon", "a.go"), "package a\n")
	scratchGit(t, clone, "add", "-A")
	scratchGit(t, clone, "commit", "-q", "-m", "first")
	built := strings.TrimSpace(scratchGit(t, clone, "rev-parse", "HEAD"))
	writeFile(t, filepath.Join(clone, "daemon", "b.go"), "package a\n")
	scratchGit(t, clone, "add", "-A")
	scratchGit(t, clone, "commit", "-q", "-m", "second, touching daemon/")
	head := strings.TrimSpace(scratchGit(t, clone, "rev-parse", "HEAD"))

	pkg := "github.com/alexherrero/agentm/daemon/internal/buildinfo"
	bin := buildStamped(t, "-X "+pkg+".commit="+built+" -X "+pkg+".modified=false -X main.version=v0.0.0-e2e")

	env := newVault(t)
	env.setConfigKey(t, "source_clones", map[string]any{"agentm": clone})
	d := start(t, bin, env)
	defer d.kill(t)

	var payload struct {
		Version string `json:"version"`
		Build   struct {
			Commit   string `json:"commit"`
			Modified bool   `json:"modified"`
			Stamp    string `json:"stamp"`
		} `json:"build"`
	}
	blob := d.httpGet(t, "/status")
	if err := json.Unmarshal(blob, &payload); err != nil {
		t.Fatalf("undecodable status: %v\n%s", err, blob)
	}
	if payload.Build.Commit != built || payload.Build.Stamp != "build-script" || payload.Build.Modified {
		t.Fatalf("status build = %+v, want commit %s stamped by the build script, unmodified", payload.Build, built)
	}
	if payload.Version != "v0.0.0-e2e" {
		t.Errorf("status version = %q, want the stamped v0.0.0-e2e", payload.Version)
	}

	out := statusText(t, bin, env, d)
	for _, want := range []string{
		"agentmd v0.0.0-e2e",
		"running " + built[:8],
		"primary clone " + head[:8],
		"the primary clone has 1 commit(s) touching daemon/ that the running daemon lacks",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("`agentmd status` is missing %q:\n%s", want, out)
		}
	}
}

// buildStamped compiles agentmd with the given -ldflags, as the deploy script
// does. Not cached with buildDaemon: the stamp is the point.
func buildStamped(t *testing.T, ldflags string) string {
	t.Helper()
	name := "agentmd"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	out := filepath.Join(t.TempDir(), name)
	cmd := exec.Command("go", "build", "-ldflags", ldflags, "-o", out, "./cmd/agentmd")
	cmd.Dir = repoRoot(t)
	cmd.Env = append(os.Environ(), "CGO_ENABLED=0")
	if msg, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("go build: %v\n%s", err, msg)
	}
	return out
}

// statusText runs `agentmd status` whatever its exit code: a scratch vault's
// health is not what this test asks about.
func statusText(t *testing.T, bin string, env *vaultEnv, d *proc) string {
	t.Helper()
	out, err := exec.Command(bin, "status", "--config", env.config, "--port", d.port(t)).CombinedOutput()
	var exit *exec.ExitError
	if err != nil && !errors.As(err, &exit) {
		t.Fatalf("agentmd status: %v\n%s", err, out)
	}
	return string(out)
}

// scratchGit runs git in a scratch repository, isolated from the machine's
// global config: scratch repos otherwise inherit its hooksPath.
func scratchGit(t *testing.T, dir string, args ...string) string {
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

func writeFile(t *testing.T, path, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}
