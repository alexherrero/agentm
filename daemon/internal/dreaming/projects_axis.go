package dreaming

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/note"
)

// `projects/` is permanent, and what ranks it is how much the work is being
// worked — not how long since anyone read a file.
//
// Nothing here is demoted or deleted by a pass, and nothing runs on the decay
// curve: a decision from June is not less true in December. Instead the night
// computes each project's *activity* from the last ninety days and every record
// under the project ranks with it as a multiplier — 1.0 when the project was
// worked in the last thirty days, 0.7 inside ninety, 0.5 inside a year, 0.3
// beyond, never lower and never a state change.
//
// Why a project-level signal and not a per-file clock: what "how much I am
// working with a project" means is the project, and a per-file clock in a
// permanent space is the decay curve by another name.
//
// **Where the number is written is not settled yet, and that is deliberate.**
// The design puts `activity:` and `last_worked:` in the project's own tracker's
// machine block. No project carries a tracker today — the operator ruled on
// 2026-09-17 that a project's tracker, charter, blueprint and grounding config
// belong to their own plan, and that a tracker holding "Not started." against a
// project with 172 tasks would be plainly false. So this writes the two fields
// into a project tracker **when one exists** and ranks by the signal either
// way: the ranking is the point, and the file will light up the day it lands.

const JobProjects = "projects"

// ActivityFileName is where the night leaves its readings for the daemon to
// rank by: one writer, one reader, and the number in the file is the same
// number the morning note reports.
const ActivityFileName = "project-activity.json"

// WriteActivity records the readings beside the engine's other state.
func WriteActivity(engineStateDir string, readings []ActivityReading, now time.Time) error {
	if engineStateDir == "" || len(readings) == 0 {
		return nil
	}
	by := make(map[string]float64, len(readings))
	for _, r := range readings {
		by[r.Slug] = r.Activity
	}
	blob, err := json.MarshalIndent(map[string]any{
		"written":  now.UTC().Format(time.RFC3339),
		"activity": by,
		"readings": readings,
	}, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(engineStateDir, 0o755); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(engineStateDir, ActivityFileName), append(blob, '\n'), 0o644)
}

// ReadActivity is the readings as the daemon consumes them: slug to multiplier.
func ReadActivity(engineStateDir string) map[string]float64 {
	out := map[string]float64{}
	blob, err := os.ReadFile(filepath.Join(engineStateDir, ActivityFileName))
	if err != nil {
		return out
	}
	var rec struct {
		Activity map[string]float64 `json:"activity"`
	}
	if err := json.Unmarshal(blob, &rec); err != nil {
		return out
	}
	for k, v := range rec.Activity {
		out[k] = v
	}
	return out
}

// The activity bands, in days of silence, with the multiplier each earns.
var activityBands = []struct {
	Within     float64
	Multiplier float64
}{
	{30, 1.0},
	{90, 0.7},
	{365, 0.5},
}

// ActivityFloor is what a project earns past the last band. Never lower: the
// space is permanent, and a project nobody has touched in two years is quiet
// rather than gone.
const ActivityFloor = 0.3

// ActivityReading is one project's signal.
type ActivityReading struct {
	Slug       string  `json:"slug"`
	LastWorked string  `json:"last_worked"`
	Activity   float64 `json:"activity"`
	// Signals names what the reading was read from, so a number the operator
	// disagrees with is arguable rather than mysterious.
	Signals []string `json:"signals,omitempty"`
}

// ProjectsPlan is what one pass read and wrote in the projects space.
type ProjectsPlan struct {
	Intents  []Intent          `json:"-"`
	Activity []ActivityReading `json:"activity"`
	// Moved is a record whose work finished: to `<slug>/completed/`, or a whole
	// project to `projects/completed/`.
	Moved []SequenceRow `json:"moved"`
	// TrackersWritten is how many project trackers took the two fields. Zero
	// while no project carries one, which is today.
	TrackersWritten int    `json:"trackers_written"`
	Capped          int    `json:"skipped_by_cap"`
	Skipped         string `json:"skipped,omitempty"`
}

// ActivityFor reads the band a number of days earns.
func ActivityFor(days float64) float64 {
	for _, b := range activityBands {
		if days <= b.Within {
			return b.Multiplier
		}
	}
	return ActivityFloor
}

// PlanProjects reads every project's activity, writes it where a tracker takes
// it, and moves what has finished.
func PlanProjects(root string, log *note.AccessLog, now time.Time, cap int) (ProjectsPlan, error) {
	var plan ProjectsPlan
	space := ProjectsRoot(root)
	if space == "" {
		plan.Skipped = "no projects/ space"
		return plan, nil
	}
	if cap <= 0 {
		cap = DefaultDemotionCap
	}
	day := time.Date(now.UTC().Year(), now.UTC().Month(), now.UTC().Day(), 0, 0, 0, 0, time.UTC)
	entries, err := os.ReadDir(space)
	if err != nil {
		plan.Skipped = "the projects space is unreadable"
		return plan, nil
	}
	for _, e := range entries {
		name := e.Name()
		if !e.IsDir() || strings.HasPrefix(name, ".") ||
			name == completedDirName || name == "_archive" {
			continue
		}
		reading := readActivity(filepath.Join(space, name), name, log, root, day)
		plan.Activity = append(plan.Activity, reading)

		// The tracker, when there is one.
		tracker := filepath.Join(space, name, "tracker.md")
		if raw, err := os.ReadFile(tracker); err == nil {
			after := setActivityFields(string(raw), reading)
			if after != string(raw) {
				rel, relErr := relTo(root, tracker)
				if relErr == nil {
					summary := fmt.Sprintf("activity %.1f, last worked %s", reading.Activity, reading.LastWorked)
					plan.Intents = append(plan.Intents, Intent{Job: JobProjects, Rel: rel,
						Before: raw, After: []byte(after), Summary: summary,
						Meta: map[string]string{"from": "", "to": "activity", "reason": summary}})
					plan.TrackersWritten++
				}
			}
		}
	}
	sort.Slice(plan.Activity, func(i, j int) bool { return plan.Activity[i].Slug < plan.Activity[j].Slug })
	return plan, nil
}

const completedDirName = "completed"

// readActivity is one project's signal, from every source the night can see.
//
// Four signals, and the reading is the most recent of them: a task tracker
// moving (opened, updated or closed), any file under the project being written,
// a memory carrying the project's name, and a genuine recall of one of its
// records. The design names sessions bound to the project as a fifth; a session
// that touches a project writes one of the first four, so it is counted through
// them rather than through a session log the binary cannot read.
func readActivity(dir, slug string, log *note.AccessLog, root string, now time.Time) ActivityReading {
	reading := ActivityReading{Slug: slug, Activity: ActivityFloor}
	newest := ""
	saw := func(date, signal string) {
		if date == "" || date > now.Format("2006-01-02") {
			return
		}
		if date > newest {
			newest = date
			reading.Signals = []string{signal}
			return
		}
		if date == newest {
			for _, s := range reading.Signals {
				if s == signal {
					return
				}
			}
			reading.Signals = append(reading.Signals, signal)
		}
	}

	_ = filepath.Walk(dir, func(p string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() || filepath.Ext(p) != ".md" {
			return nil
		}
		fm, _, ok := projReadNote(p)
		if !ok {
			return nil
		}
		signal := "a file written"
		if strings.TrimSpace(fm["kind"]) == "tracker" {
			signal = "a task moved"
			saw(projDate(fm["closed"]), signal)
			saw(projDate(fm["opened"]), signal)
		}
		saw(projDate(fm["updated"]), signal)
		saw(projDate(fm["created"]), signal)

		// A genuine recall of one of the project's records, from the clock the
		// recall path keeps. Keyed by path since the re-keying, which is what
		// makes this readable at all: a basename key could not say which
		// project a `progress` belonged to.
		if log != nil {
			if rel, err := relTo(root, p); err == nil {
				if t, ok := log.LastAccess(rel, strings.TrimSuffix(filepath.Base(p), ".md")); ok {
					saw(t.Format("2006-01-02"), "a record recalled")
				}
			}
		}
		return nil
	})

	if newest == "" {
		return reading
	}
	reading.LastWorked = newest
	if t, err := time.Parse("2006-01-02", newest); err == nil {
		reading.Activity = ActivityFor(now.Sub(t).Hours() / 24)
	}
	return reading
}

// setActivityFields writes `activity:` and `last_worked:` into a tracker's
// frontmatter, in place, leaving everything else byte-identical.
//
// The same line-surgical edit `SetLifecycle` makes, and for the same reason: a
// tracker is the operator's living head, and a pass that re-rendered its
// frontmatter would reorder fields they wrote by hand.
func setActivityFields(text string, reading ActivityReading) string {
	text = setFrontmatterField(text, "activity", fmt.Sprintf("%.1f", reading.Activity))
	if reading.LastWorked != "" {
		text = setFrontmatterField(text, "last_worked", reading.LastWorked)
	}
	return text
}

// setFrontmatterField replaces or appends one frontmatter key.
func setFrontmatterField(text, key, value string) string {
	if !strings.HasPrefix(text, "---\n") {
		return text
	}
	lines := strings.Split(text, "\n")
	end := -1
	for i := 1; i < len(lines); i++ {
		if strings.TrimSpace(lines[i]) == "---" {
			end = i
			break
		}
	}
	if end < 0 {
		return text
	}
	for i := 1; i < end; i++ {
		k, _, ok := strings.Cut(lines[i], ":")
		if ok && strings.TrimSpace(k) == key && len(lines[i]) > 0 &&
			!strings.ContainsRune(" \t#-", rune(lines[i][0])) {
			lines[i] = key + ": " + value
			return strings.Join(lines, "\n")
		}
	}
	lines = append(lines[:end], append([]string{key + ": " + value}, lines[end:]...)...)
	return strings.Join(lines, "\n")
}

// ── what has finished moves ──────────────────────────────────────────────────
//
// Active work and finished work are told apart by name, by field and by one
// folder at each level. A record moves to `<slug>/completed/` when the work it
// belonged to is finished: a research bundle, brief or draft whose `task:`
// names a closed task, or a decision or design a later one supersedes. A whole
// project moves to `projects/completed/<slug>/` the night after its tracker
// reads `done`.
//
// The living documents never move — the charter, the tracker, `followups`,
// `roadmap`, a launched design, a standing decision — and the tasks never move
// at all: a task is the unit of work, and the sequence of task directories is
// the history of the project as the operator reads it.
//
// Why "completed" and not "archive": the memory archive holds what has been
// retired from use, and a project's completed folder holds finished work that
// is still true. Two words for two meanings, one ranker rule for both.

// completedFolders are the per-project folders whose finished records move.
var completedFolders = []string{"decisions", "designs", "research", "drafts", "desk"}

// neverMove are the project's standing documents, by file name.
var neverMove = map[string]bool{
	"charter.md": true, "tracker.md": true, "followups.md": true, "roadmap.md": true,
}

// PlanCompleted decides what has finished and where it goes.
//
// `closedTasks` is the set of task names whose tracker reads a final status —
// read once by the caller, because a record names its task and the task's own
// tracker is the only thing that says whether it closed.
func PlanCompleted(root string, closedTasks map[string]bool, doneProjects []string,
	now time.Time, cap int) (ProjectsPlan, error) {
	var plan ProjectsPlan
	space := ProjectsRoot(root)
	if space == "" {
		plan.Skipped = "no projects/ space"
		return plan, nil
	}
	if cap <= 0 {
		cap = DefaultDemotionCap
	}
	entries, err := os.ReadDir(space)
	if err != nil {
		plan.Skipped = "the projects space is unreadable"
		return plan, nil
	}
	for _, e := range entries {
		name := e.Name()
		if !e.IsDir() || strings.HasPrefix(name, ".") ||
			name == completedDirName || name == "_archive" {
			continue
		}
		for _, folder := range completedFolders {
			dir := filepath.Join(space, name, folder)
			files, err := os.ReadDir(dir)
			if err != nil {
				continue
			}
			for _, f := range files {
				if f.IsDir() || filepath.Ext(f.Name()) != ".md" || neverMove[f.Name()] {
					continue
				}
				p := filepath.Join(dir, f.Name())
				fm, _, ok := projReadNote(p)
				if !ok {
					continue
				}
				why := ""
				if task := strings.TrimSpace(fm["task"]); task != "" && closedTasks[task] {
					why = "its task closed"
				}
				if s := strings.TrimSpace(fm["superseded_by"]); s != "" {
					why = "a later one supersedes it"
				}
				if why == "" {
					continue
				}
				if len(plan.Moved) >= cap {
					plan.Capped++
					continue
				}
				raw, err := os.ReadFile(p)
				if err != nil {
					continue
				}
				dst := filepath.Join(space, name, completedDirName, folder, f.Name())
				from, e1 := relTo(root, p)
				to, e2 := relTo(root, dst)
				if e1 != nil || e2 != nil {
					continue
				}
				plan.Intents = append(plan.Intents, Intent{Job: JobProjects, Rel: from, To: to,
					Before: raw, After: raw, Summary: why,
					Meta: map[string]string{"from": "live", "to": "completed", "reason": why}})
				plan.Moved = append(plan.Moved, SequenceRow{From: from, To: to})
			}
		}
	}

	// A finished project moves whole, the night after its tracker reads `done`.
	// The closing session marks the tracker, which is the fact; the move is
	// bookkeeping, and it belongs with the other moves the night makes from
	// frontmatter.
	//
	// The directory move itself is not an intent: the journal applies one file
	// at a time, and a project is a tree. It is named here and made by the
	// structural-move tooling, which is what rewrites the links a whole-tree
	// move breaks.
	for _, slug := range doneProjects {
		src := filepath.Join(space, slug)
		if st, err := os.Stat(src); err != nil || !st.IsDir() {
			continue
		}
		dst := filepath.Join(space, completedDirName, slug)
		if _, err := os.Stat(dst); err == nil {
			continue
		}
		from, e1 := relTo(root, src)
		to, e2 := relTo(root, dst)
		if e1 != nil || e2 != nil {
			continue
		}
		plan.Moved = append(plan.Moved, SequenceRow{From: from, To: to})
	}
	return plan, nil
}

// ClosedTasks is every task whose tracker reads a final status, by task name.
func ClosedTasks(root string) map[string]bool {
	out := map[string]bool{}
	space := ProjectsRoot(root)
	if space == "" {
		return out
	}
	entries, _ := os.ReadDir(space)
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		tasks := filepath.Join(space, e.Name(), "tasks")
		dirs, err := os.ReadDir(tasks)
		if err != nil {
			continue
		}
		for _, d := range dirs {
			if !d.IsDir() {
				continue
			}
			fm, _, ok := projReadNote(filepath.Join(tasks, d.Name(), "tracker.md"))
			if !ok {
				continue
			}
			status := strings.ToLower(strings.TrimSpace(fm["status"]))
			if status != "done" && status != "dropped" && status != "withdrawn" {
				continue
			}
			name := d.Name()
			if named := strings.TrimSpace(fm["task"]); named != "" {
				name = named
			}
			out[name] = true
		}
	}
	return out
}

// DoneProjects is every project whose own tracker reads `done` with an Outcome.
// Empty while no project carries a tracker, which is today.
func DoneProjects(root string) []string {
	var out []string
	space := ProjectsRoot(root)
	if space == "" {
		return out
	}
	entries, _ := os.ReadDir(space)
	for _, e := range entries {
		if !e.IsDir() || e.Name() == completedDirName {
			continue
		}
		fm, body, ok := projReadNote(filepath.Join(space, e.Name(), "tracker.md"))
		if !ok || strings.ToLower(strings.TrimSpace(fm["status"])) != "done" {
			continue
		}
		// With its Outcome written. A tracker that says `done` and has no
		// Outcome is a tracker mid-edit, and moving the project out from under
		// the session writing it is the one thing this must not do.
		if !strings.Contains(body, "## Outcome") ||
			strings.TrimSpace(afterHeading(body, "## Outcome")) == "" {
			continue
		}
		out = append(out, e.Name())
	}
	sort.Strings(out)
	return out
}

// afterHeading is the text under a heading, to the next one.
func afterHeading(body, heading string) string {
	i := strings.Index(body, heading)
	if i < 0 {
		return ""
	}
	rest := body[i+len(heading):]
	if j := strings.Index(rest, "\n## "); j >= 0 {
		rest = rest[:j]
	}
	return rest
}
