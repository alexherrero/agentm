// Package pulse reads what two scheduled jobs outside the daemon leave behind,
// so health can say whether they are still running and, when one is not, why
// (task 198).
//
// The runner leaves `last-cycle.json` at the end of every cycle, and a marker
// per job that is `start` while the job runs. The backup push job leaves a log,
// one timestamped line per run with its reason indented beneath. Both are read
// as data; nothing here starts, stops or repairs either job.
//
// Both jobs are launchd agents with a `StartInterval`, which launchd counts in
// awake time: after a night asleep the next run is due an interval after the
// wake, not hours ago. LastWake is the machine's last wake, so a grade can
// measure from it.
package pulse

import (
	"bufio"
	"bytes"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

// Runner is one reading of the runner's state directory.
type Runner struct {
	// Found says `last-cycle.json` exists.
	Found bool
	// LastCycle is when the last finished cycle began (`at`).
	LastCycle time.Time
	// Running and RunningSince name the newest job still marked `start` that
	// began after LastCycle: a cycle in progress.
	Running      string
	RunningSince time.Time
	// StartupError is the runner log's last line when it is a startup error.
	StartupError string
	// Err says why the reading is incomplete.
	Err string
}

// ReadRunner reads the runner's state directory. The log is the runner's
// launchd log, `telemetry/runner-launchd.log` beside the state directory.
func ReadRunner(dir string) Runner {
	var out Runner
	if dir == "" {
		return out
	}
	blob, err := os.ReadFile(filepath.Join(dir, "last-cycle.json"))
	if os.IsNotExist(err) {
		return out
	}
	out.Found = true
	if err != nil {
		out.Err = err.Error()
		return out
	}
	var cycle struct {
		At *float64 `json:"at"`
	}
	if err := json.Unmarshal(blob, &cycle); err != nil || cycle.At == nil {
		out.Err = "last-cycle.json records no `at`"
		return out
	}
	out.LastCycle = epoch(*cycle.At)

	paths, _ := filepath.Glob(filepath.Join(dir, "*.json"))
	for _, p := range paths {
		name := filepath.Base(p)
		if name == "last-cycle.json" || strings.HasSuffix(name, ".watchdog.json") {
			continue
		}
		b, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		var m struct {
			Status    string   `json:"status"`
			StartedAt *float64 `json:"started_at"`
		}
		if json.Unmarshal(b, &m) != nil || m.Status != "start" || m.StartedAt == nil {
			continue
		}
		if t := epoch(*m.StartedAt); t.After(out.LastCycle) && t.After(out.RunningSince) {
			out.Running, out.RunningSince = strings.TrimSuffix(name, ".json"), t
		}
	}
	out.StartupError = startupError(filepath.Join(filepath.Dir(dir), "telemetry", "runner-launchd.log"))
	return out
}

// startupErrorLine is a traceback's last line, or a missing module said any
// other way. The runner_pulse module in the memory skill uses the same pattern.
var startupErrorLine = regexp.MustCompile(
	`^(?:[A-Za-z_][\w.]*(?:Error|Exception)\b.*|.*No module named .*|.*not available.*)$`)

func startupError(log string) string {
	tail := readTail(log, 8192)
	lines := strings.Split(strings.TrimSpace(tail), "\n")
	last := strings.TrimSpace(lines[len(lines)-1])
	if last == "" || !startupErrorLine.MatchString(last) {
		return ""
	}
	return clip(last, 300)
}

// BackupLog is the push job's account of its recent runs.
type BackupLog struct {
	// Found says the log exists.
	Found bool
	// LastRun is the newest run, LastRunOK whether it pushed or found nothing
	// to push, and LastRunText its first line with the first line of its reason.
	LastRun     time.Time
	LastRunOK   bool
	LastRunText string
	// Failing counts the newest runs that failed in a row; zero when the
	// newest succeeded.
	Failing int
}

// logLine is the job's run line: a local timestamp, two spaces, the outcome.
var logLine = regexp.MustCompile(`^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})  (.*)$`)

// ReadBackupLog reads the push job's log. A run is every line that carries the
// same timestamp, with the indented lines beneath them; it succeeded when it
// says `pushed …` or `ok: …`.
func ReadBackupLog(path string) BackupLog {
	var out BackupLog
	if path == "" {
		return out
	}
	if _, err := os.Stat(path); err != nil {
		return out
	}
	out.Found = true

	type run struct {
		at     time.Time
		stamp  string
		first  string
		reason string
	}
	var runs []run
	sc := bufio.NewScanner(strings.NewReader(readTail(path, 64*1024)))
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		line := sc.Text()
		if m := logLine.FindStringSubmatch(line); m != nil {
			if n := len(runs); n > 0 && runs[n-1].stamp == m[1] {
				continue
			}
			at, err := time.ParseInLocation("2006-01-02 15:04:05", m[1], time.Local)
			if err != nil {
				continue
			}
			runs = append(runs, run{at: at, stamp: m[1], first: strings.TrimSpace(m[2])})
			continue
		}
		if n := len(runs); n > 0 && runs[n-1].reason == "" && strings.HasPrefix(line, " ") {
			runs[n-1].reason = strings.TrimSpace(line)
		}
	}
	if len(runs) == 0 {
		return out
	}
	ok := func(r run) bool {
		return strings.HasPrefix(r.first, "pushed ") || strings.HasPrefix(r.first, "ok:")
	}
	last := runs[len(runs)-1]
	out.LastRun, out.LastRunOK = last.at, ok(last)
	out.LastRunText = strings.TrimSuffix(strings.TrimSpace(last.first), " —")
	if last.reason != "" {
		out.LastRunText += " — " + last.reason
	}
	out.LastRunText = clip(out.LastRunText, 300)
	for i := len(runs) - 1; i >= 0 && !ok(runs[i]); i-- {
		out.Failing++
	}
	return out
}

func readTail(path string, n int64) string {
	f, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer f.Close()
	if st, err := f.Stat(); err == nil && st.Size() > n {
		if _, err := f.Seek(-n, io.SeekEnd); err != nil {
			return ""
		}
	}
	b, err := io.ReadAll(f)
	if err != nil {
		return ""
	}
	// A tail cut mid-line starts with a fragment; drop it.
	if i := bytes.IndexByte(b, '\n'); i >= 0 && len(b) == int(n) {
		b = b[i+1:]
	}
	return string(b)
}

func epoch(sec float64) time.Time {
	return time.Unix(0, int64(sec*float64(time.Second)))
}

func clip(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}
