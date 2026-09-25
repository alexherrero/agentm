package dreaming

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

// Job "mocs", the project trackers (agentm-vault § Five statuses, the
// operator's ruling of 2026-09-24). A project's `tracker.md` is not written by
// any session: the night generates it from the project's task trackers, so it
// cannot disagree with them. On the operator's definition of 2026-09-17 it is
// a checklist of where the project stands across its tasks, shaped so board
// sync can read it.
//
// It keeps the tracker's one schema (`scripts/tracker.py`), so every reader of
// a tracker reads this one too:
//
//	frontmatter  kind, title, project (no task), status, opened, updated,
//	             closed, and the operator's sensitivity marking when
//	             project.yaml carries one
//	Objective    the charter's What line, and that the file is generated
//	State        a summary line, then the checklist: the open tasks first,
//	             grouped by the design that governs them, each with its
//	             status; the done ones collapsed to one line with a count
//	Next         the first open task, or that none is in flight
//	Outcome      empty unless project.yaml marks the project finished
//
// `title` and `status` come from project.yaml, falling back to the charter's
// title and `active`. `opened` is the earliest date the project has — its
// charter's `created` or a task's `opened` — and `updated` the newest a task
// carries, so a night over unchanged trackers writes nothing.

const projectTrackerName = "tracker.md"

var trackerPlain = regexp.MustCompile(`^[A-Za-z0-9_][A-Za-z0-9_ ./()'-]*$`)
var trackerNumberish = regexp.MustCompile(`^[-+]?[0-9][0-9_.eE+-]*$`)
var trackerYAMLWords = map[string]bool{"true": true, "false": true, "yes": true, "no": true,
	"on": true, "off": true, "null": true, "y": true, "n": true, "~": true}

// trackerScalar mirrors tracker.py's `_scalar_out`: plain when unambiguous,
// else double-quoted JSON, so the Python parser reads back the same string.
func trackerScalar(v string) string {
	if trackerPlain.MatchString(v) && !strings.HasSuffix(v, " ") &&
		!trackerYAMLWords[strings.ToLower(v)] && !trackerNumberish.MatchString(v) {
		return v
	}
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v)
	return strings.TrimSuffix(b.String(), "\n")
}

// projectYAML reads the flat keys a project.yaml carries that the tracker
// needs. It is not a YAML parser: `check-project-yaml` holds the file to its
// schema, and these three keys are single-line scalars there.
func projectYAML(dir string) map[string]string {
	out := map[string]string{}
	raw, err := os.ReadFile(filepath.Join(dir, "project.yaml"))
	if err != nil {
		return out
	}
	for _, line := range strings.Split(strings.ReplaceAll(string(raw), "\r\n", "\n"), "\n") {
		key, value, ok := strings.Cut(line, ":")
		if !ok || strings.HasPrefix(line, " ") || strings.HasPrefix(line, "#") {
			continue
		}
		key = strings.TrimSpace(key)
		if key != "title" && key != "status" && key != "sensitivity" {
			continue
		}
		if i := strings.Index(value, " #"); i >= 0 {
			value = value[:i]
		}
		value = strings.TrimSpace(value)
		if len(value) >= 2 && (value[0] == '"' && value[len(value)-1] == '"') {
			var s string
			if json.Unmarshal([]byte(value), &s) == nil {
				value = s
			}
		} else if len(value) >= 2 && value[0] == '\'' && value[len(value)-1] == '\'' {
			value = strings.ReplaceAll(value[1:len(value)-1], "''", "'")
		}
		out[key] = value
	}
	return out
}

// designName is how a task's `design:` reads in a checklist line: the design's
// file stem, `wiki/designs/agentm-vault.md` as `agentm-vault`.
func designName(design string) string {
	d := strings.TrimSpace(design)
	if d == "" {
		return ""
	}
	return strings.TrimSuffix(filepath.Base(filepath.FromSlash(d)), ".md")
}

var trackerStatuses = map[string]bool{"queued": true, "active": true, "parked": true, "done": true, "dropped": true}

func renderProjectTracker(slug, title, status, sensitivity, what, opened, updated, closed string,
	tasks []projectTask) string {
	var open, done []projectTask
	for _, t := range tasks {
		if t.Status == "done" || t.Status == "dropped" {
			done = append(done, t)
		} else {
			open = append(open, t)
		}
	}
	rank := func(status string) int {
		if r, ok := projStatusRank[status]; ok {
			return r
		}
		return len(projStatusRank)
	}
	// Grouped by design: the tasks a design governs together, named designs
	// first in name order, then the tasks no design governs.
	sort.SliceStable(open, func(i, j int) bool {
		a, b := designName(open[i].Design), designName(open[j].Design)
		if (a == "") != (b == "") {
			return a != ""
		}
		if a != b {
			return a < b
		}
		if rank(open[i].Status) != rank(open[j].Status) {
			return rank(open[i].Status) < rank(open[j].Status)
		}
		return open[i].Task < open[j].Task
	})
	sort.SliceStable(done, func(i, j int) bool {
		if done[i].Closed != done[j].Closed {
			return done[i].Closed > done[j].Closed
		}
		return done[i].Task > done[j].Task
	})

	fm := []string{"---", "kind: tracker", "title: " + trackerScalar(title),
		"project: " + trackerScalar(slug), "status: " + status,
		"opened: " + opened, "updated: " + updated}
	if closed != "" {
		fm = append(fm, "closed: "+closed)
	} else {
		fm = append(fm, "closed:")
	}
	if sensitivity != "" {
		fm = append(fm, "sensitivity: "+trackerScalar(sensitivity))
	}
	fm = append(fm, "---")

	objective := "Generated nightly from this project's task trackers; change those, not this file."
	if what != "" {
		objective = what + "\n\n" + objective
	}
	state := []string{fmt.Sprintf("%d open · %d done", len(open), len(done))}
	for _, t := range open {
		line := fmt.Sprintf("- [ ] %s — %s", t.Task, t.Status)
		if d := designName(t.Design); d != "" {
			line += " · " + d
		}
		state = append(state, line)
	}
	if len(done) > 0 {
		last := done[0]
		line := fmt.Sprintf("- [x] done: %d", len(done))
		if last.Closed != "" {
			line += fmt.Sprintf(" — the latest %s, closed %s", last.Task, last.Closed)
		} else {
			line += " — the latest " + last.Task
		}
		state = append(state, line)
	}
	next := "No task is in flight."
	if len(open) > 0 {
		next = fmt.Sprintf("%s (%s)", open[0].Task, open[0].Status)
	}
	outcome := ""
	if status == "done" || status == "dropped" {
		outcome = fmt.Sprintf("project.yaml marks the project %s.", status)
	}
	body := []string{"## Objective", "", objective, "", "## State", "", strings.Join(state, "\n"), "",
		"## Next", "", next, "", "## Outcome"}
	if outcome != "" {
		body = append(body, "", outcome)
	}
	return strings.Join(fm, "\n") + "\n\n" + strings.Join(body, "\n") + "\n"
}

// PlanProjectTrackers decides every project's `tracker.md`. It writes nothing,
// and plans nothing when the vault has no projects space.
func PlanProjectTrackers(root string, now time.Time) (MocsPlan, error) {
	plan := MocsPlan{BelowFloor: map[string]int{}}
	vault := vaultRootOf(root)
	space := filepath.Join(vault, projectsSpaceName)
	if !isDirExact(space) {
		return plan, nil
	}
	entries, err := os.ReadDir(space)
	if err != nil {
		return plan, err
	}
	today := now.UTC().Format("2006-01-02")
	for _, e := range entries {
		slug := e.Name()
		if !e.IsDir() || strings.HasPrefix(slug, "_") || strings.HasPrefix(slug, ".") || slug == "completed" {
			continue
		}
		dir := filepath.Join(space, slug)
		cfg := projectYAML(dir)
		title := cfg["title"]
		what := ""
		var opens []string
		for _, name := range []string{"charter.md", "_index.md"} {
			fm, body, ok := projReadNote(filepath.Join(dir, name))
			if !ok {
				continue
			}
			if title == "" {
				title = projTitle(fm, body)
			}
			what = projWhatLine(body)
			opens = append(opens, projDate(fm["created"]))
			break
		}
		if title == "" {
			title = slug
		}
		status := cfg["status"]
		if !trackerStatuses[status] {
			status = "active"
		}
		tasks := projectTasks(vault, slug, dir)
		var dates []string
		for _, t := range tasks {
			opens = append(opens, t.Opened)
			dates = append(dates, t.Updated, t.Closed)
		}
		rel := memoryRel(root, filepath.Join(dir, projectTrackerName))
		before, _ := mocCurrentPage(root, rel)
		// `opened` is the earliest date the project has; with none at all, the
		// date the tracker was first written, kept from the file on later nights.
		opened := projOldest(opens...)
		if opened == "" && before != nil {
			fm, _ := ParseFrontmatter(string(before))
			opened = projDate(fm["opened"])
		}
		if opened == "" {
			opened = today
		}
		updated := projNewest(dates...)
		if updated == "" || updated < opened {
			updated = opened
		}
		closed := ""
		if status == "done" || status == "dropped" {
			closed = updated
		}
		text := renderProjectTracker(slug, title, status, cfg["sensitivity"], what, opened, updated, closed, tasks)
		plan.add(MocPage{Rel: rel, Members: len(tasks), Newest: updated}, before, text,
			fmt.Sprintf("the tracker of project %s regenerated (%d %s)", slug, len(tasks), mocPlural(len(tasks), "task", "tasks")))
	}
	return plan, nil
}

// projOldest is the earliest valid date among `dates`, or "".
func projOldest(dates ...string) string {
	out := ""
	for _, d := range dates {
		if d = projDate(d); d != "" && (out == "" || d < out) {
			out = d
		}
	}
	return out
}
