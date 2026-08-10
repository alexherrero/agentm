// Week 4: the loud queue, the self-probe, and the corpus-write gate.
//
// The probe tests below are the round trip stated one level up. The suite's
// existing round-trip test proves a fact captured through the MCP surface comes
// back to a fresh process asking sideways; these prove the *daemon* checks that
// for itself, every day, without anyone running a test — and that when it stops
// being true, the daemon says so instead of continuing to look fine.
package e2e

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// The self-probe
// ---------------------------------------------------------------------------

// TestProbe_RoundTripAgainstARealDaemon is principle 3 running as a live
// process. The daemon writes a synthetic memory over its own HTTP surface, asks
// for it back in two words the note's prose does not contain, and reports how
// long the whole thing took. Then the test does what no resident process can do
// for itself: kills it, starts a different one, and asks again.
func TestProbe_RoundTripAgainstARealDaemon(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)

	a := start(t, bin, env)
	res := a.probe(t)

	if !res.OK {
		t.Fatalf("the self-probe failed on a healthy daemon: %s", res.Detail)
	}
	if len(res.Result.Queries) != 2 {
		t.Fatalf("the probe asked %d questions, want 2 (alias and body)",
			len(res.Result.Queries))
	}
	var aliasNonce, bodyNonce string
	for _, q := range res.Result.Queries {
		if !q.Found {
			t.Errorf("the %s query %q did not return the note the probe had just written",
				q.Kind, q.Query)
		}
		switch q.Kind {
		case "alias":
			aliasNonce = q.Query
		case "body":
			bodyNonce = q.Query
		}
	}
	if aliasNonce == "" || bodyNonce == "" {
		t.Fatalf("the probe did not ask both an alias and a body question: %+v", res.Result.Queries)
	}

	rel := res.Result.Path
	if rel == "" {
		t.Fatal("the probe reported no path")
	}
	raw, err := os.ReadFile(filepath.Join(env.vault, rel))
	if err != nil {
		t.Fatalf("the probe reported %s but nothing is on disk: %v", rel, err)
	}
	note := string(raw)

	// The sideways property, checked against the file rather than trusted. The
	// alias nonce must appear in the frontmatter and nowhere in the prose — if
	// it leaked into the body, the query would be answerable from the body
	// column and the probe would prove less than it claims.
	head, body, found := strings.Cut(strings.TrimPrefix(note, "---\n"), "\n---\n")
	if !found {
		t.Fatalf("the probe note has no frontmatter:\n%s", note)
	}
	if !strings.Contains(head, aliasNonce) {
		t.Errorf("the alias nonce %q is not in the note's frontmatter:\n%s", aliasNonce, head)
	}
	if strings.Contains(body, aliasNonce) {
		t.Errorf("the alias nonce %q appears in the note's prose, so the alias query "+
			"is not asking sideways:\n%s", aliasNonce, body)
	}
	if !strings.Contains(body, bodyNonce) {
		t.Errorf("the body nonce %q is not in the note's prose:\n%s", bodyNonce, body)
	}

	// --- a genuinely fresh process -------------------------------------------
	a.kill(t)
	b := start(t, bin, env)
	defer b.kill(t)

	if hits := b.search(t, aliasNonce, 5); !hits.contains(rel) {
		t.Errorf("a fresh process could not find the probe note by its alias\n  got: %s",
			hits.summary())
	}
}

// TestProbe_MarksItselfSyntheticByFrontmatterNotByPath pins the exclusion rule.
//
// Capture shards by date, so a probe written on the last day of a month and one
// written the next morning live in different directories. Anything that
// excluded probes by location would quietly stop excluding them, and a synthetic
// note counted in a measurement is a measurement that is wrong in the direction
// nobody checks.
func TestProbe_MarksItselfSyntheticByFrontmatterNotByPath(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)

	d := start(t, bin, env)
	defer d.kill(t)

	// A real memory alongside it, so "excluded" can be distinguished from
	// "nothing was counted".
	real := d.capture(t, captureArgs{
		Title:  "Wake on the check-suite",
		Text:   "The full matrix triggers on pull_request rather than on a push to main.",
		Type:   "workflow",
		Status: "active",
	}).str(t, "path")

	res := d.probe(t)
	if !res.OK {
		t.Fatalf("probe failed: %s", res.Detail)
	}

	raw, err := os.ReadFile(filepath.Join(env.vault, res.Result.Path))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), "probe: self-probe") {
		t.Errorf("the probe note carries no marker:\n%s", raw)
	}

	// The classifier is what every downstream measurement reads, so the marker
	// has to survive the trip through it.
	out := runCLI(t, bin, 0, "classify", "--config", env.config, "--index", env.index, "--json")
	var probes, reals int
	for _, line := range strings.Split(strings.TrimSpace(out), "\n") {
		if line == "" {
			continue
		}
		var row struct {
			Path  string `json:"path"`
			Probe bool   `json:"probe"`
		}
		if err := json.Unmarshal([]byte(line), &row); err != nil {
			t.Fatalf("classify emitted unreadable JSON: %v\n%s", err, line)
		}
		switch row.Path {
		case res.Result.Path:
			probes++
			if !row.Probe {
				t.Error("classify did not report the probe note as synthetic, so every " +
					"scorecard downstream of it would count a fake memory")
			}
		case real:
			reals++
			if row.Probe {
				t.Errorf("classify marked a real memory %s as a probe", real)
			}
		}
	}
	if probes != 1 || reals != 1 {
		t.Errorf("classify reported %d probe notes and %d real notes, want 1 and 1", probes, reals)
	}

	// And the summary the operator reads reports them apart rather than folding
	// synthetic notes into the population.
	summary := runCLI(t, bin, 0, "classify", "--config", env.config, "--index", env.index)
	if !strings.Contains(summary, "self-probe") {
		t.Errorf("the classify summary does not account for probe notes:\n%s", summary)
	}
}

// TestProbe_RetiresThePreviousNote: one probe note at a time. The current one
// stays so the round trip has an artifact anyone can go and look at; the ones
// before it are exhaust and the daemon deletes them without ceremony.
func TestProbe_RetiresThePreviousNote(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)

	d := start(t, bin, env)
	defer d.kill(t)

	first := d.probe(t)
	if !first.OK {
		t.Fatalf("first probe failed: %s", first.Detail)
	}
	second := d.probe(t)
	if !second.OK {
		t.Fatalf("second probe failed: %s", second.Detail)
	}
	if first.Result.Path == second.Result.Path {
		t.Fatalf("both probes wrote %s; each run must write its own note", first.Result.Path)
	}

	if _, err := os.Stat(filepath.Join(env.vault, second.Result.Path)); err != nil {
		t.Errorf("the current probe note is missing: %v", err)
	}
	deadline := time.Now().Add(10 * time.Second)
	for {
		if _, err := os.Stat(filepath.Join(env.vault, first.Result.Path)); os.IsNotExist(err) {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("the previous probe note %s was never retired", first.Result.Path)
		}
		time.Sleep(100 * time.Millisecond)
	}
}

// TestProbe_ABrokenRoundTripTurnsTheStatusRed is the point of the whole
// mechanism. A probe that failed and reported nothing would be worse than no
// probe, because it would look like evidence.
func TestProbe_ABrokenRoundTripTurnsTheStatusRed(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)
	// A budget nothing can meet. The round trip still happens — this exercises
	// the real failure path, "it worked but not inside its expected time",
	// rather than a mock.
	env.setConfigKey(t, "daemon.probe_budget", "1ns")

	d := start(t, bin, env)
	defer d.kill(t)

	res := d.probe(t)
	if res.OK {
		t.Fatal("the probe passed with a 1ns budget")
	}
	if !strings.Contains(res.Detail, "budget") {
		t.Errorf("the failure did not name the budget: %q", res.Detail)
	}

	rep := d.health(t)
	if rep.Level != "red" {
		t.Fatalf("a failed self-probe left the status %q", rep.Level)
	}
	if !hasAlert(rep, "probe-failed") {
		t.Errorf("no probe-failed alert after a failed probe: %+v", rep.Alerts)
	}

	// The CLI is the surface the operator actually reads, and it must be
	// non-zero so a shell can ask without parsing.
	out := runCLI(t, bin, 3, "status", "--config", env.config, "--port", d.port(t))
	if !strings.Contains(out, "FAILED") {
		t.Errorf("`agentmd status` did not report the failed probe:\n%s", out)
	}
}

// ---------------------------------------------------------------------------
// The loud queue
// ---------------------------------------------------------------------------

// TestStatus_QueueIsWhatIsWaitingNotWhatIsRetired.
//
// `superseded` and `expired` are rank-penalized for the same reason `unfiled`
// is, which makes them easy to lump together — and lumping them together puts
// the oldest retired note in the corpus at the head of the filing queue, leaving
// the age threshold red on day one. An alert that is always red is an alert
// nobody reads.
func TestStatus_QueueIsWhatIsWaitingNotWhatIsRetired(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)

	env.write(t, "personal/2020/01/long-superseded.md", `---
type: preference
status: superseded
captured: 2020-01-04T09:00:00Z
---
A preference that was replaced years ago. It is not waiting for anything.
`)
	env.write(t, "personal/2020/01/long-expired.md", `---
type: preference
status: expired
captured: 2020-01-05T09:00:00Z
---
A preference that aged out. Also not waiting for anything.
`)
	env.write(t, "personal/2026/08/waiting.md", `---
type: idea
status: unfiled
captured: `+time.Now().UTC().Add(-2*time.Hour).Format("2006-01-02T15:04:05Z")+`
---
Captured unattended and genuinely waiting to be filed.
`)
	// Everything here counts against the thresholds, so the split between
	// inherited backlog and current queue is not what this test is measuring.
	env.setConfigKey(t, "daemon.queue_baseline", "2019-01-01")

	d := start(t, bin, env)
	defer d.kill(t)

	rep := waitForQueue(t, d, 1)
	if rep.Queue.Unfiled != 1 {
		t.Fatalf("the queue counted %d items; only the unfiled note is waiting", rep.Queue.Unfiled)
	}
	if rep.Level != "ok" {
		t.Errorf("two notes retired years ago turned the queue red: %+v", rep.Alerts)
	}
}

// TestStatus_TheInheritedBacklogIsReportedAndDoesNotPage.
//
// The first status read against the real vault was 4,349 unfiled items with the
// oldest 29 days old — a pile the design already decided to rank-penalize and
// let dreaming drain later. Paging on it daily would have made this alert
// useless before it ever carried a signal. It stays on the surface; it does not
// page.
func TestStatus_TheInheritedBacklogIsReportedAndDoesNotPage(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)

	for i := 0; i < 3; i++ {
		env.write(t, fmt.Sprintf("personal/2026/06/backlog-%02d.md", i), `---
type: idea
status: unfiled
captured: 2026-06-01T09:00:00Z
---
Part of the pile this daemon inherited.
`)
	}
	env.setConfigKey(t, "daemon.queue_baseline", "2026-07-01")

	d := start(t, bin, env)
	defer d.kill(t)

	rep := waitForQueue(t, d, 3)
	if rep.Level != "ok" {
		t.Fatalf("the inherited backlog paged: %+v", rep.Alerts)
	}
	if rep.Queue.Inherited != 3 {
		t.Errorf("inherited = %d, want 3", rep.Queue.Inherited)
	}

	out := runCLI(t, bin, 0, "status", "--config", env.config, "--port", d.port(t))
	if !strings.Contains(out, "inherited") {
		t.Errorf("`agentmd status` does not report the inherited backlog, so it can "+
			"be forgotten:\n%s", out)
	}

	// And a stall after the baseline still pages, which is what keeps the split
	// honest rather than a mute button.
	env.write(t, "personal/2026/08/stalled-after-baseline.md", `---
type: idea
status: unfiled
captured: `+time.Now().UTC().Add(-96*time.Hour).Format("2006-01-02T15:04:05Z")+`
---
Captured after the baseline and never filed.
`)
	deadline := time.Now().Add(30 * time.Second)
	for {
		rep = d.health(t)
		if hasAlert(rep, "queue-age") {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("a four-day-old item captured after the baseline never paged: %+v", rep)
		}
		time.Sleep(250 * time.Millisecond)
	}
}

// TestStatus_AStalledQueueGoesRed: the two numbers, and the threshold that pages
// on the older of them.
func TestStatus_AStalledQueueGoesRed(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)

	env.setConfigKey(t, "daemon.queue_baseline", "2019-01-01")

	// Fifty fresh items — an ordinary Tuesday under a standing ingest.
	for i := 0; i < 50; i++ {
		env.write(t, fmt.Sprintf("personal/2026/08/fresh-%02d.md", i), `---
type: idea
status: unfiled
captured: `+time.Now().UTC().Add(-time.Hour).Format("2006-01-02T15:04:05Z")+`
---
Captured this morning by ambient mining, waiting for tonight's filing pass.
`)
	}

	d := start(t, bin, env)
	defer d.kill(t)

	rep := waitForQueue(t, d, 50)
	if rep.Level != "ok" {
		t.Fatalf("fifty fresh unfiled items paged the operator: %+v", rep.Alerts)
	}

	// One four-day-old item. Same queue, stalled pipeline.
	env.write(t, "personal/2026/08/stalled.md", `---
type: idea
status: unfiled
captured: `+time.Now().UTC().Add(-96*time.Hour).Format("2006-01-02T15:04:05Z")+`
---
Captured four days ago and never filed.
`)

	deadline := time.Now().Add(30 * time.Second)
	for {
		rep = d.health(t)
		if rep.Level == "red" && hasAlert(rep, "queue-age") {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("a four-day-old unfiled item never turned the status red: %+v", rep)
		}
		time.Sleep(250 * time.Millisecond)
	}
	if rep.Queue.OldestAge == "" {
		t.Error("the status reports no age for the oldest unfiled item")
	}

	// Through the CLI, which reads the daemon's own JSON back. A four-day age
	// renders as "4d…" for the person reading it, and that is exactly the report
	// the surface exists to show — so it has to survive being parsed again.
	out := runCLI(t, bin, 3, "status", "--config", env.config, "--port", d.port(t))
	if !strings.Contains(out, "queue-age") {
		t.Errorf("`agentmd status` did not report the stalled queue:\n%s", out)
	}
	if !strings.Contains(out, "unfiled") {
		t.Errorf("`agentmd status` did not report the queue's two numbers:\n%s", out)
	}
}

// TestStatus_ReportsDegradedGitWithoutPaging pins the deliberate split between
// what the status *says* and what pages. The vault is not a repository until the
// git-transport migration runs; that is reported everywhere and it blocks the
// gate, but it is not news at three in the morning.
func TestStatus_ReportsDegradedGitWithoutPaging(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t) // deliberately not a repository

	d := start(t, bin, env)
	defer d.kill(t)

	rep := d.health(t)
	if rep.Git.State != "degraded" {
		t.Errorf("git state = %q, want degraded", rep.Git.State)
	}
	if rep.Git.Detail != "not a repository" {
		t.Errorf("git detail = %q, want %q", rep.Git.Detail, "not a repository")
	}
	if rep.Level != "ok" {
		t.Errorf("a known, deliberate, deferred migration paged: %+v", rep.Alerts)
	}

	out := runCLI(t, bin, 0, "status", "--config", env.config, "--port", d.port(t))
	if !strings.Contains(out, "degraded: not a repository") {
		t.Errorf("`agentmd status` did not report the degraded git state:\n%s", out)
	}
	if !strings.Contains(out, "gate corpus-write") {
		t.Errorf("`agentmd status` did not connect degraded git to the gate it blocks:\n%s", out)
	}
}

// ---------------------------------------------------------------------------
// The gate
// ---------------------------------------------------------------------------

// TestGate_RefusesWithoutARepository is the contract this whole gate exists for.
// The alias backfill rewrote 1,930 notes with a homemade journal as its only
// undo. Dreaming's drain is bigger, and it does not get to repeat that.
func TestGate_RefusesWithoutARepository(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)

	out := runCLI(t, bin, 3, "gate", "--config", env.config, "--index", env.index,
		"--json", "corpus-write")
	res := decodeGate(t, out)

	if res.Pass {
		t.Fatal("the gate passed with no repository, so a corpus-wide job would run with no undo")
	}
	if len(res.Reasons) == 0 || res.Reasons[0].Code != "git-degraded" {
		t.Fatalf("expected a git-degraded refusal, got %+v", res.Reasons)
	}
	if res.Reasons[0].Remedy == "" {
		t.Error("the refusal does not say how to satisfy it, so it will be worked around")
	}
}

func TestGate_PassesOnACleanRepositoryAndHandsBackTheUndoPoint(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)
	gitInit(t, env.vault)
	env.write(t, "personal/2026/08/already-here.md", "---\ntype: idea\nstatus: active\n---\nA note.\n")
	gitCommitAll(t, env.vault, "seed")

	out := runCLI(t, bin, 0, "gate", "--config", env.config, "--index", env.index,
		"--json", "corpus-write")
	res := decodeGate(t, out)

	if !res.Pass {
		t.Fatalf("the gate refused a clean repository: %+v", res.Reasons)
	}
	if len(res.Head) != 40 {
		t.Errorf("head = %q; a job that has to be undone needs a point to be undone to", res.Head)
	}
}

// TestGate_RefusesADirtyWorktree is the second half of "is there an undo". With
// unrelated edits already in the worktree, reverting the job and reverting the
// operator's afternoon are the same command.
func TestGate_RefusesADirtyWorktree(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)
	gitInit(t, env.vault)
	env.write(t, "personal/2026/08/already-here.md", "---\ntype: idea\nstatus: active\n---\nA note.\n")
	gitCommitAll(t, env.vault, "seed")
	env.write(t, "personal/2026/08/mid-edit.md", "---\ntype: idea\nstatus: active\n---\nHalf-written.\n")

	out := runCLI(t, bin, 3, "gate", "--config", env.config, "--index", env.index,
		"--json", "corpus-write")
	res := decodeGate(t, out)

	if res.Pass {
		t.Fatal("the gate passed with uncommitted changes in the worktree")
	}
	if res.Reasons[0].Code != "uncommitted-changes" {
		t.Fatalf("expected an uncommitted-changes refusal, got %+v", res.Reasons)
	}
	if !strings.Contains(res.Reasons[0].Detail, "mid-edit") {
		t.Errorf("the refusal does not name what is dirty: %q", res.Reasons[0].Detail)
	}
}

// TestGate_UnknownGatesAreRefusedNotAssumed: a job script asking for a gate this
// binary does not define must not read as permission. Exit 1 rather than 3 —
// "the gate could not decide" is a different fact from "the gate says no", and
// both are non-zero.
func TestGate_UnknownGatesAreRefusedNotAssumed(t *testing.T) {
	bin := buildDaemon(t)
	env := newVault(t)
	runCLI(t, bin, 1, "gate", "--config", env.config, "--index", env.index, "invented-gate")
	runCLI(t, bin, 1, "gate", "--config", env.config, "--index", env.index)
}

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

type probeQuery struct {
	Kind  string `json:"kind"`
	Query string `json:"query"`
	Found bool   `json:"found"`
	Rank  int    `json:"rank"`
}

type probeResponse struct {
	OK     bool   `json:"ok"`
	Detail string `json:"detail"`
	Result struct {
		OK      bool         `json:"ok"`
		Elapsed string       `json:"elapsed"`
		Detail  string       `json:"detail"`
		Path    string       `json:"path"`
		Queries []probeQuery `json:"queries"`
	} `json:"result"`
}

// probe runs one self-probe through the daemon's own endpoint — the same code
// path the daily schedule takes.
func (p *proc) probe(t *testing.T) probeResponse {
	t.Helper()
	blob := p.httpPost(t, "/probe")
	var out probeResponse
	if err := json.Unmarshal(blob, &out); err != nil {
		t.Fatalf("undecodable probe response: %v\n%s", err, blob)
	}
	if out.Detail == "" {
		out.Detail = out.Result.Detail
	}
	return out
}

type healthReport struct {
	Level  string `json:"level"`
	Alerts []struct {
		Code   string `json:"code"`
		Detail string `json:"detail"`
	} `json:"alerts"`
	Queue struct {
		Unfiled   int    `json:"unfiled"`
		Inherited int    `json:"inherited"`
		Since     int    `json:"since"`
		OldestAge string `json:"oldest_age"`
		OldestAt  string `json:"oldest_at"`
	} `json:"queue"`
	Index struct {
		Documents int    `json:"documents"`
		Age       string `json:"age"`
		Fresh     bool   `json:"fresh"`
	} `json:"index"`
	Git struct {
		State  string `json:"state"`
		Detail string `json:"detail"`
	} `json:"git"`
	Probe struct {
		OK       bool   `json:"ok"`
		Detail   string `json:"detail"`
		Recorded bool   `json:"recorded"`
	} `json:"probe"`
}

func (p *proc) health(t *testing.T) healthReport {
	t.Helper()
	blob := p.httpGet(t, "/status")
	var payload struct {
		Health healthReport `json:"health"`
	}
	if err := json.Unmarshal(blob, &payload); err != nil {
		t.Fatalf("undecodable status: %v\n%s", err, blob)
	}
	return payload.Health
}

func hasAlert(r healthReport, code string) bool {
	for _, a := range r.Alerts {
		if a.Code == code {
			return true
		}
	}
	return false
}

// waitForQueue polls until the index has caught up with the notes written
// straight into the vault. The notifier is an accelerator and the reconcile pass
// is the guarantee, so a test that asserted immediately would be asserting on
// the notifier's mood.
func waitForQueue(t *testing.T, p *proc, want int) healthReport {
	t.Helper()
	deadline := time.Now().Add(30 * time.Second)
	var rep healthReport
	for {
		rep = p.health(t)
		if rep.Queue.Unfiled == want {
			return rep
		}
		if time.Now().After(deadline) {
			t.Fatalf("the queue settled at %d unfiled, want %d", rep.Queue.Unfiled, want)
		}
		time.Sleep(250 * time.Millisecond)
	}
}

var portRe = regexp.MustCompile(`:(\d+)$`)

func (p *proc) port(t *testing.T) string {
	t.Helper()
	m := portRe.FindStringSubmatch(p.addr)
	if m == nil {
		t.Fatalf("could not read a port out of %q", p.addr)
	}
	return m[1]
}

type gateResult struct {
	Gate    string `json:"gate"`
	Pass    bool   `json:"pass"`
	Head    string `json:"head"`
	Vault   string `json:"vault"`
	Reasons []struct {
		Code   string `json:"code"`
		Detail string `json:"detail"`
		Remedy string `json:"remedy"`
	} `json:"reasons"`
}

func decodeGate(t *testing.T, out string) gateResult {
	t.Helper()
	var res gateResult
	if err := json.Unmarshal([]byte(out), &res); err != nil {
		t.Fatalf("undecodable gate verdict: %v\n%s", err, out)
	}
	return res
}

// runCLI runs the built binary and asserts its exit code. The code is the
// contract for the gate — a job script asks by running it, not by parsing
// prose — so every call states the one it expects.
func runCLI(t *testing.T, bin string, wantCode int, args ...string) string {
	t.Helper()
	cmd := exec.Command(bin, args...)
	out, err := cmd.CombinedOutput()
	code := 0
	if err != nil {
		var exit *exec.ExitError
		if !errors.As(err, &exit) {
			t.Fatalf("running %v: %v\n%s", args, err, out)
		}
		code = exit.ExitCode()
	}
	if code != wantCode {
		t.Fatalf("`agentmd %s` exited %d, want %d\n%s",
			strings.Join(args, " "), code, wantCode, out)
	}
	return string(out)
}

func gitCommitAll(t *testing.T, dir, message string) {
	t.Helper()
	for _, args := range [][]string{{"add", "-A"}, {"commit", "-m", message}} {
		cmd := exec.Command("git", args...)
		cmd.Dir = dir
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v\n%s", args, err, out)
		}
	}
}

// httpGet and httpPost talk to the daemon's plain HTTP surfaces, which are not
// MCP and so do not go through `call`.
func (p *proc) httpGet(t *testing.T, path string) []byte {
	t.Helper()
	resp, err := (&http.Client{Timeout: 30 * time.Second}).Get(p.addr + path)
	if err != nil {
		t.Fatalf("GET %s: %v\n%s", path, err, p.logs())
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("GET %s: HTTP %d\n%s", path, resp.StatusCode, raw)
	}
	return raw
}

func (p *proc) httpPost(t *testing.T, path string) []byte {
	t.Helper()
	resp, err := (&http.Client{Timeout: 60 * time.Second}).Post(
		p.addr+path, "application/json", nil)
	if err != nil {
		t.Fatalf("POST %s: %v\n%s", path, err, p.logs())
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("POST %s: HTTP %d\n%s", path, resp.StatusCode, raw)
	}
	return raw
}
