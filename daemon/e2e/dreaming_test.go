package e2e

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"
)

// The dreaming binary, driven as the runner will drive it (filing v2 part 6,
// task 3): a pass killed with SIGKILL mid-way resumes on the next start
// without losing a mutation or applying one twice; a second start while the
// first holds the lock is refused; report-only decides without writing.

var (
	dreamerOnce sync.Once
	dreamerBin  string
	dreamerErr  error
)

func buildDreamer(t *testing.T) string {
	t.Helper()
	dreamerOnce.Do(func() {
		dir, err := os.MkdirTemp("", "agentmdream-e2e-")
		if err != nil {
			dreamerErr = err
			return
		}
		name := "agentmdream"
		if runtime.GOOS == "windows" {
			name += ".exe" // exec needs the suffix there; go build writes exactly -o
		}
		dreamerBin = filepath.Join(dir, name)
		cmd := exec.Command("go", "build", "-o", dreamerBin, "./cmd/agentmdream")
		cmd.Dir = repoRoot(t)
		cmd.Env = append(os.Environ(), "CGO_ENABLED=0")
		if out, err := cmd.CombinedOutput(); err != nil {
			dreamerErr = fmt.Errorf("go build ./cmd/agentmdream: %v\n%s", err, out)
		}
	})
	if dreamerErr != nil {
		t.Fatal(dreamerErr)
	}
	return dreamerBin
}

type dreamerEnv struct {
	*vaultEnv
	state string
}

func newDreamerEnv(t *testing.T) *dreamerEnv {
	t.Helper()
	env := &dreamerEnv{vaultEnv: newVault(t), state: filepath.Join(t.TempDir(), "state")}
	return env
}

func (d *dreamerEnv) silentNotes(t *testing.T, n int) []string {
	t.Helper()
	created := time.Now().UTC().Add(-500 * 24 * time.Hour).Format("2006-01-02")
	var rels []string
	for i := 0; i < n; i++ {
		rel := fmt.Sprintf("memory/semantic/note-%03d.md", i)
		d.write(t, rel, fmt.Sprintf("---\ntitle: note %03d\nkind: reference\nstatus: active\nslug: note-%03d\nlifecycle: active\ncreated: %s\n---\n\nA memory nobody has needed for a while, number %d.\n", i, i, created, i))
		rels = append(rels, rel)
	}
	return rels
}

func (d *dreamerEnv) command(bin string, args ...string) *exec.Cmd {
	cmd := exec.Command(bin, append([]string{"run", "--config", d.config}, args...)...)
	cmd.Env = append(os.Environ(),
		"AGENTM_STATE_DIR="+d.state,
		"AGENTM_RECALL_HISTORY="+filepath.Join(d.state, "no-recalls.jsonl"),
		"XDG_CACHE_HOME="+filepath.Join(d.state, "cache"),
	)
	return cmd
}

func (d *dreamerEnv) journalLines(t *testing.T) []map[string]any {
	t.Helper()
	blob, err := os.ReadFile(filepath.Join(d.state, "dreaming", "journal.jsonl"))
	if err != nil {
		return nil
	}
	var out []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(string(blob)), "\n") {
		var m map[string]any
		if json.Unmarshal([]byte(line), &m) == nil {
			out = append(out, m)
		}
	}
	return out
}

func (d *dreamerEnv) lifecycleOf(t *testing.T, rel string) (state string, sinceLines int) {
	t.Helper()
	blob, err := os.ReadFile(filepath.Join(d.vault, rel))
	if err != nil {
		t.Fatal(err)
	}
	state = "active"
	for _, l := range strings.Split(string(blob), "\n") {
		if strings.HasPrefix(l, "lifecycle: ") {
			state = strings.TrimPrefix(l, "lifecycle: ")
		}
		if strings.HasPrefix(l, "lifecycle_since: ") {
			sinceLines++
		}
	}
	return
}

func exitCode(err error) int {
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		return ee.ExitCode()
	}
	if err == nil {
		return 0
	}
	return -1
}

func TestAKilledPassResumesWithoutLossOrDoubleApplication(t *testing.T) {
	bin := buildDreamer(t)
	env := newDreamerEnv(t)
	rels := env.silentNotes(t, 30)

	// Pass 1, paced so a kill lands mid-way. Wait until the journal shows a
	// few intents, then SIGKILL — no cleanup, no lock release.
	first := env.command(bin, "-apply", "-force", "-pace", "60ms")
	var out bytes.Buffer
	first.Stdout, first.Stderr = &out, &out
	if err := first.Start(); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(20 * time.Second)
	for {
		intents := 0
		for _, e := range env.journalLines(t) {
			if e["kind"] == "intent" {
				intents++
			}
		}
		if intents >= 6 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("the paced pass never journaled six intents; output so far:\n%s", out.String())
		}
		time.Sleep(20 * time.Millisecond)
	}
	if err := first.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	_ = first.Wait()
	before := env.journalLines(t)
	var beforeApplied int
	for _, e := range before {
		if e["kind"] == "applied" {
			beforeApplied++
		}
	}
	if beforeApplied >= 30 {
		t.Fatalf("the kill landed after the pass finished (%d applied); the fixture does not test a resume", beforeApplied)
	}

	// Pass 2: resumes what the first left, then — forced, since a day has not
	// passed — runs its own pass over the remainder.
	second := env.command(bin, "-apply", "-force", "-json")
	blob, err := second.Output()
	if err != nil {
		t.Fatalf("second pass: %v\n%s", err, blob)
	}
	var rep struct {
		Resumed int    `json:"resumed"`
		Applied int    `json:"applied"`
		Skipped int    `json:"skipped"`
		Outcome string `json:"outcome"`
	}
	if err := json.Unmarshal(blob, &rep); err != nil {
		t.Fatalf("report JSON: %v\n%s", err, blob)
	}
	if rep.Outcome != "applied" || rep.Skipped != 0 {
		t.Errorf("second pass: %+v", rep)
	}

	// Every note dormant, dated exactly once.
	for _, rel := range rels {
		state, since := env.lifecycleOf(t, rel)
		if state != "dormant" {
			t.Errorf("%s: %s, want dormant — a mutation was lost", rel, state)
		}
		if since != 1 {
			t.Errorf("%s: %d lifecycle_since lines — applied twice", rel, since)
		}
	}
	// The journal: each note applied exactly once across both passes, the
	// first pass closed by the resume, no intent left pending.
	appliedBy := map[string]int{}
	runsDone := 0
	for _, e := range env.journalLines(t) {
		switch e["kind"] {
		case "applied":
			appliedBy[e["rel"].(string)]++
		case "run-done":
			runsDone++
		}
	}
	if len(appliedBy) != 30 {
		t.Errorf("%d notes applied in the journal, want 30", len(appliedBy))
	}
	for rel, n := range appliedBy {
		if n != 1 {
			t.Errorf("%s applied %d times", rel, n)
		}
	}
	if runsDone != 2 {
		t.Errorf("run-done lines = %d, want 2 (the resumed first pass and the second)", runsDone)
	}
	if beforeApplied+rep.Resumed+rep.Applied < 30 {
		t.Errorf("first pass applied %d, resume settled %d, second pass applied %d — the notes were not covered", beforeApplied, rep.Resumed, rep.Applied)
	}
	// The governance journal has one line per note, none twice.
	gov, err := os.ReadFile(filepath.Join(env.state, "lifecycle-journal.jsonl"))
	if err != nil {
		t.Fatal("no lifecycle journal written")
	}
	seen := map[string]int{}
	for _, line := range strings.Split(strings.TrimSpace(string(gov)), "\n") {
		var m map[string]any
		if json.Unmarshal([]byte(line), &m) == nil {
			seen[m["rel"].(string)]++
		}
	}
	for rel, n := range seen {
		if n != 1 {
			t.Errorf("lifecycle journal has %s %d times", rel, n)
		}
	}
	// Notes the resume settled (journaled by the first pass before the kill,
	// written by the second) are recorded by the resume, not the governance
	// journal — the intent's own line is the record. So the governance count
	// is at most 30 and covers what the two applying passes wrote.
	if len(seen) > 30 || len(seen) < 30-rep.Resumed {
		t.Errorf("lifecycle journal covers %d notes; want between %d and 30", len(seen), 30-rep.Resumed)
	}
}

func TestASecondStartIsRefusedWithExitThree(t *testing.T) {
	bin := buildDreamer(t)
	env := newDreamerEnv(t)
	env.silentNotes(t, 20)
	first := env.command(bin, "-apply", "-force", "-pace", "150ms")
	var out bytes.Buffer
	first.Stdout, first.Stderr = &out, &out
	if err := first.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = first.Process.Kill(); _ = first.Wait() }()
	deadline := time.Now().Add(20 * time.Second)
	for len(env.journalLines(t)) < 2 {
		if time.Now().After(deadline) {
			t.Fatalf("the first pass never started; output:\n%s", out.String())
		}
		time.Sleep(20 * time.Millisecond)
	}
	second := env.command(bin, "-apply", "-force")
	blob, err := second.CombinedOutput()
	if code := exitCode(err); code != 3 {
		t.Fatalf("second start exit code %d, want 3 (refused); output:\n%s", code, blob)
	}
	if !strings.Contains(string(blob), "refused") || !strings.Contains(string(blob), "held") {
		t.Errorf("the refusal should name the held lock:\n%s", blob)
	}
}

func TestReportOnlyEmitsDecisionsWithoutWrites(t *testing.T) {
	bin := buildDreamer(t)
	env := newDreamerEnv(t)
	rels := env.silentNotes(t, 5)
	blob, err := env.command(bin, "-force").CombinedOutput()
	if err != nil {
		t.Fatalf("report-only run: %v\n%s", err, blob)
	}
	if !strings.Contains(string(blob), "would sink 5") {
		t.Errorf("report-only should say what it would do:\n%s", blob)
	}
	for _, rel := range rels {
		if state, since := env.lifecycleOf(t, rel); state != "active" || since != 0 {
			t.Errorf("%s changed under report-only: %s (%d since lines)", rel, state, since)
		}
	}
	if _, err := os.Stat(filepath.Join(env.state, "lifecycle-journal.jsonl")); !os.IsNotExist(err) {
		t.Errorf("report-only wrote the lifecycle journal")
	}
	for _, e := range env.journalLines(t) {
		if e["kind"] == "intent" {
			t.Errorf("report-only journaled an intent: %v", e)
		}
	}
	// Not due without -force: the pass just reported, so the interval holds.
	blob, err = env.command(bin).CombinedOutput()
	if err != nil || !strings.Contains(string(blob), "not due") {
		t.Errorf("a second run inside the interval should say not due: err=%v\n%s", err, blob)
	}
}
