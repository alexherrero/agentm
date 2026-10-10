package dreaming

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/linkrewrite"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// ── closed tasks move ────────────────────────────────────────────────────────
//
// Finished work stops ranking like live work. Two weeks after a task's tracker
// reads `done` or `dropped`, its whole directory moves, under its own name, from
// `projects/<slug>/tasks/` to `projects/<slug>/completed/tasks/`, where the
// ranker's `completed` segment weighs it at ×0.30 like every other finished
// record (agentm-vault, the operator's rulings of 2026-09-24, section 2).
//
// A folder moves whole or not at all. The cap counts folders and never files,
// each file is one journaled move, and the tracker — the file that says the
// task closed — moves last, and only once every other file of the folder has:
// a pass that crashes or finds a file changed under it leaves the tracker
// where the next pass looks, and that pass finishes the folder.
//
// The links are repaired from what is on disk rather than from what one pass
// planned. Every applying night rewrites each link that names a file under
// `tasks/` which now sits under `completed/tasks/`, and re-keys the two
// sidecars the same way — through the port of the hand-run moves' rewriter, so
// the night rewrites a link exactly as they did. A crash between the moves and
// the edits therefore costs one night of stale links, never a lost repair. The
// moved notes' own relative links out, one level deeper now, are rewritten as
// part of their own journaled move.
//
// The operator's `personal/` and `standards/` are never edited: a folder whose
// rewrites would reach a note there stays where it is and is listed, every
// night, until they decide. Nothing walled from recall is read at all, and
// without a contract to say what is walled the mover reads nothing.
//
// The mover ships reporting. It plans every night and says what it would move;
// it moves only when `daemon.task_mover_enabled` is on, or when the operator
// runs `agentmdream move-tasks -apply` by hand.

const (
	// JobTasks is the journal's name for the task mover.
	JobTasks = "tasks"
	// TaskCompletedAfterKey is the contract line that says how long a closed
	// task stays under `tasks/`.
	TaskCompletedAfterKey = "task_completed_after_days"
	// DefaultTaskCompletedAfterDays is the packaged contract's value.
	DefaultTaskCompletedAfterDays = 14
	// MaxTaskFileBytes is the largest file the mover journals. The journal
	// carries a move's content, and its reader stops at a 64 MB line; a folder
	// holding anything bigger is held and listed rather than moved.
	MaxTaskFileBytes = 16 << 20
)

// heldSpaces are the root spaces the mover never edits a note in.
var heldSpaces = []string{"personal", "standards"}

// TaskMovePlan is what the task mover planned, or did.
type TaskMovePlan struct {
	// Mode is "report" when the night only said what it would move, "apply"
	// when it moved.
	Mode      string       `json:"mode"`
	AfterDays float64      `json:"after_days"`
	Folders   []TaskFolder `json:"folders"`
	Held      []HeldTask   `json:"held,omitempty"`
	// Stopped are folders an applying pass began and did not finish, because a
	// file changed between the plan and the move. Their trackers stay under
	// `tasks/`, so the next pass finds them and carries on.
	Stopped []HeldTask `json:"stopped,omitempty"`
	// Capped is how many folders were due and waited for a later night.
	Capped  int            `json:"capped"`
	Edited  []EditedNote   `json:"edited,omitempty"`
	Links   int            `json:"links"`
	Pruned  []string       `json:"pruned,omitempty"`
	Rekeyed map[string]int `json:"rekeyed,omitempty"`
	// Pending is a note, in a run that does not apply, that a crashed pass left
	// intents for the next applying run to settle.
	Pending int      `json:"pending,omitempty"`
	Errors  []string `json:"errors,omitempty"`
	Skipped string   `json:"skipped,omitempty"`
	// Intents are the link edits the plan would make; the moves are per folder.
	Intents []Intent `json:"-"`
	// Moves is every planned file move, vault-relative.
	Moves   []linkrewrite.Move `json:"-"`
	folders [][]Intent
}

// TaskFolder is one task directory the mover planned to move.
type TaskFolder struct {
	Project string `json:"project"`
	Task    string `json:"task"`
	From    string `json:"from"`
	To      string `json:"to"`
	Status  string `json:"status"`
	Closed  string `json:"closed"`
	Files   int    `json:"files"`
}

// HeldTask is a closed task the mover left where it is, and why.
type HeldTask struct {
	From   string   `json:"from"`
	Reason string   `json:"reason"`
	Notes  []string `json:"notes,omitempty"`
}

// EditedNote is a note outside the moved folders whose links were rewritten.
type EditedNote struct {
	Path  string `json:"path"`
	Links int    `json:"links"`
}

// TaskCompletedAfterDays is the contract's `task_completed_after_days`, or the
// packaged default when the contract does not name it.
func TaskCompletedAfterDays(r *rules.Rules) float64 {
	if v, ok := r.Threshold(TaskCompletedAfterKey); ok && v > 0 {
		return v
	}
	return DefaultTaskCompletedAfterDays
}

// TaskMoveCap is how many closed task folders one pass may move: the flag
// when it names one, else the contract's `demotion_cap`, counted in folders.
func TaskMoveCap(r *rules.Rules, flag int) int {
	return DemotionCap(r, flag)
}

type taskCandidate struct {
	TaskFolder
	src, dst string // absolute directories
	closedAt time.Time
	files    []string // vault-relative, the tracker last
}

func (c taskCandidate) dstOf(rel string) string {
	return c.To + strings.TrimPrefix(rel, c.From)
}

// PlanTaskMoves plans the move of every task directory whose tracker has read
// `done` or `dropped` for at least the contract's `task_completed_after_days`,
// oldest first, at most `cap` folders, and the link edits the night would make.
//
// `root` is the memory root. The plan reads and writes nothing it may not: no
// walled area, and nothing at all without a contract.
func PlanTaskMoves(root string, contract *rules.Rules, now time.Time, cap int) (TaskMovePlan, error) {
	plan := TaskMovePlan{Mode: "report", AfterDays: TaskCompletedAfterDays(contract)}
	if contract == nil {
		plan.Skipped = "the storage contract did not load, so what is walled is unknown; the mover reads nothing until it does"
		return plan, nil
	}
	space := ProjectsRoot(root)
	if space == "" {
		plan.Skipped = "no projects/ space"
		return plan, nil
	}
	if cap <= 0 {
		cap = DefaultDemotionCap
	}
	vault := vaultRootOf(root)

	due := dueTasks(vault, space, contract, now, &plan)
	notes, err := vaultNotes(vault, contract)
	if err != nil {
		return plan, err
	}
	// The held-back rule, over every due folder, before the cap is counted: a
	// folder the operator has to decide about must not take a slot every night
	// and starve the rest.
	due = holdForHeldSpaces(vault, notes, due, &plan)
	if len(due) > cap {
		plan.Capped = len(due) - cap
		due = due[:cap]
	}

	// Read each folder before planning it: a file that cannot be read holds its
	// folder, and never the night.
	contents := map[string][]byte{}
	kept := due[:0:0]
	for _, c := range due {
		ok := true
		for _, rel := range c.files {
			raw, err := os.ReadFile(filepath.Join(vault, filepath.FromSlash(rel)))
			if err != nil {
				plan.Held = append(plan.Held, HeldTask{From: c.From,
					Reason: fmt.Sprintf("%s could not be read: %v", rel, err)})
				ok = false
				break
			}
			contents[rel] = raw
		}
		if ok {
			kept = append(kept, c)
		}
	}
	due = kept

	moves, from, to := taskMoveSets(due)
	plan.Moves = moves
	// The rewriter knows tonight's moves and every earlier one whose open path
	// is gone, so the plan reports the repairs an applying night would make.
	rw := linkrewrite.New(append(append([]linkrewrite.Move(nil), moves...),
		completedTwins(vault, space, contract)...), nil)
	exists := afterRun(vault, from, to)

	for _, c := range due {
		var folder []Intent
		for _, rel := range c.files {
			raw := contents[rel]
			dst := c.dstOf(rel)
			after := raw
			if strings.HasSuffix(rel, ".md") {
				if text, n := rw.Text(string(raw), rel, dst, exists); n > 0 {
					after = []byte(text)
					plan.Links += n
				}
			}
			srcRel, e1 := relTo(root, filepath.Join(vault, filepath.FromSlash(rel)))
			dstRel, e2 := relTo(root, filepath.Join(vault, filepath.FromSlash(dst)))
			if e1 != nil || e2 != nil {
				return plan, fmt.Errorf("task mover: %s is not under the memory root's vault", rel)
			}
			why := fmt.Sprintf("its task %s %s, past %s", c.Status, c.Closed, TaskCompletedAfterKey)
			folder = append(folder, Intent{Job: JobTasks, Rel: srcRel, To: dstRel,
				Before: raw, After: after, Summary: why,
				Meta: map[string]string{"from": "live", "to": "completed", "reason": why}})
		}
		plan.folders = append(plan.folders, folder)
		plan.Folders = append(plan.Folders, c.TaskFolder)
	}

	edits, edited, links := linkEdits(vault, root, notes, from, rw, exists)
	plan.Intents, plan.Edited = edits, edited
	plan.Links += links
	return plan, nil
}

// dueTasks is every closed task past the wait, oldest first; the folders it
// cannot move are recorded on the plan as held.
func dueTasks(vault, space string, contract *rules.Rules, now time.Time, plan *TaskMovePlan) []taskCandidate {
	vrel := func(abs string) string {
		rel, err := filepath.Rel(vault, abs)
		if err != nil {
			return filepath.ToSlash(abs)
		}
		return filepath.ToSlash(rel)
	}
	var due []taskCandidate
	projects, err := os.ReadDir(space)
	if err != nil {
		plan.Skipped = "the projects space is unreadable"
		return nil
	}
	for _, p := range projects {
		name := p.Name()
		if !p.IsDir() || strings.HasPrefix(name, ".") || strings.HasPrefix(name, "_") ||
			name == completedDirName {
			continue
		}
		tasks := filepath.Join(space, name, "tasks")
		if contract.IsRecallExempt(vrel(tasks)) {
			continue
		}
		dirs, err := os.ReadDir(tasks)
		if err != nil {
			continue
		}
		for _, d := range dirs {
			if !d.IsDir() || strings.HasPrefix(d.Name(), ".") {
				continue
			}
			src := filepath.Join(tasks, d.Name())
			if contract.IsRecallExempt(vrel(src)) {
				continue
			}
			fm, _, ok := projReadNote(filepath.Join(src, "tracker.md"))
			if !ok {
				continue
			}
			status := strings.ToLower(strings.TrimSpace(fm["status"]))
			if status != "done" && status != "dropped" {
				continue
			}
			closed := projDate(fm["closed"])
			if closed == "" {
				// A closed tracker with no date cannot age, and guessing one
				// would move a task on a clock nobody set.
				continue
			}
			at, _ := time.Parse("2006-01-02", closed)
			if now.Sub(at).Hours()/24 < plan.AfterDays {
				continue
			}
			c := taskCandidate{
				TaskFolder: TaskFolder{Project: name, Task: d.Name(), Status: status, Closed: closed},
				src:        src,
				dst:        filepath.Join(space, name, completedDirName, "tasks", d.Name()),
				closedAt:   at,
			}
			c.From, c.To = vrel(c.src), vrel(c.dst)
			files, reason := taskFiles(vault, c.src, contract)
			if reason != "" {
				plan.Held = append(plan.Held, HeldTask{From: c.From, Reason: reason})
				continue
			}
			c.files, c.Files = files, len(files)
			// A destination that already exists is a folder an earlier pass left
			// part-moved — the tracker moves last, so it is still found here —
			// and the move carries on, unless a file would land on one that is
			// already there.
			if clash := firstClash(vault, c); clash != "" {
				plan.Held = append(plan.Held, HeldTask{From: c.From,
					Reason: clash + " already exists; a move never overwrites"})
				continue
			}
			due = append(due, c)
		}
	}
	sort.SliceStable(due, func(i, j int) bool {
		if !due[i].closedAt.Equal(due[j].closedAt) {
			return due[i].closedAt.Before(due[j].closedAt)
		}
		return due[i].From < due[j].From
	})
	return due
}

// linkEdits are the edits that point every link in `notes` at where its target
// sits after the moves `rw` knows: not in a folder being moved (those rewrite
// their own links as they move), never under `personal/` or `standards/`, and
// never an entity page, which the builder renders from the notes after the
// mover has run (task 190: one writer per page).
func linkEdits(vault, root string, notes []string, moving map[string]bool,
	rw *linkrewrite.Rewriter, exists func(string) bool) ([]Intent, []EditedNote, int) {
	var intents []Intent
	var edited []EditedNote
	links := 0
	entities := path.Join(memoryRootRel(root, vault), "memory", "entities") + "/"
	for _, rel := range notes {
		if moving[rel] || inHeldSpace(rel) || strings.HasPrefix(rel, entities) {
			continue
		}
		abs := filepath.Join(vault, filepath.FromSlash(rel))
		raw, err := os.ReadFile(abs)
		if err != nil || !mightLink(raw) {
			continue
		}
		text, n := rw.Text(string(raw), rel, rel, exists)
		if n == 0 {
			continue
		}
		noteRel, err := relTo(root, abs)
		if err != nil {
			continue
		}
		intents = append(intents, Intent{Job: JobTasks, Rel: noteRel, Before: raw,
			After: []byte(text), Summary: fmt.Sprintf("%d link(s) into moved task folders rewritten", n)})
		edited = append(edited, EditedNote{Path: rel, Links: n})
		links += n
	}
	return intents, edited, links
}

// completedTwins are the moves already made: every file under a project's
// `completed/tasks/` whose open path under `tasks/` holds nothing. A task moved
// back by hand has its open path again and drops out.
func completedTwins(vault, space string, contract *rules.Rules) []linkrewrite.Move {
	var out []linkrewrite.Move
	projects, err := os.ReadDir(space)
	if err != nil {
		return nil
	}
	for _, p := range projects {
		name := p.Name()
		if !p.IsDir() || strings.HasPrefix(name, ".") || strings.HasPrefix(name, "_") ||
			name == completedDirName {
			continue
		}
		done := filepath.Join(space, name, completedDirName, "tasks")
		_ = filepath.WalkDir(done, func(pth string, d os.DirEntry, err error) error {
			if err != nil {
				return nil
			}
			rel, rerr := filepath.Rel(vault, pth)
			if rerr != nil {
				return nil
			}
			rel = filepath.ToSlash(rel)
			if contract.IsRecallExempt(rel) {
				if d.IsDir() {
					return filepath.SkipDir
				}
				return nil
			}
			if d.IsDir() || taskLitter[d.Name()] {
				return nil
			}
			open := strings.Replace(rel, "/"+completedDirName+"/tasks/", "/tasks/", 1)
			if _, err := os.Lstat(filepath.Join(vault, filepath.FromSlash(open))); os.IsNotExist(err) {
				out = append(out, linkrewrite.Move{From: open, To: rel})
			}
			return nil
		})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].From < out[j].From })
	return out
}

// holdForHeldSpaces drops, and records as held, every due folder a note under
// `personal/` or `standards/` links into by path. Those notes are the
// operator's, and a move that left their links pointing at nothing would be
// an edit made by omission. A note there that cannot be read may link
// anywhere, so it holds every due folder that night.
func holdForHeldSpaces(vault string, notes []string, due []taskCandidate, plan *TaskMovePlan) []taskCandidate {
	if len(due) == 0 {
		return due
	}
	moves, from, to := taskMoveSets(due)
	all := linkrewrite.New(moves, nil)
	exists := afterRun(vault, from, to)
	heldBy := map[string][]string{}
	for _, rel := range notes {
		if !inHeldSpace(rel) {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(vault, filepath.FromSlash(rel)))
		if err != nil {
			for _, c := range due {
				plan.Held = append(plan.Held, HeldTask{From: c.From, Notes: []string{rel},
					Reason: "a note under personal/ or standards/ could not be read, so what links into this folder is unknown"})
			}
			return nil
		}
		if !mightLink(raw) {
			continue
		}
		if _, n := all.Text(string(raw), rel, rel, exists); n == 0 {
			continue
		}
		// Which folders: one rewriter per folder, over this one note.
		for _, c := range due {
			one, oneFrom, oneTo := taskMoveSets([]taskCandidate{c})
			if _, n := linkrewrite.New(one, nil).Text(string(raw), rel, rel,
				afterRun(vault, oneFrom, oneTo)); n > 0 {
				heldBy[c.From] = append(heldBy[c.From], rel)
			}
		}
	}
	if len(heldBy) == 0 {
		return due
	}
	kept := due[:0:0]
	for _, c := range due {
		if notes, held := heldBy[c.From]; held {
			plan.Held = append(plan.Held, HeldTask{From: c.From, Notes: notes,
				Reason: "a note under personal/ or standards/ links into it by path; those are yours to edit"})
			continue
		}
		kept = append(kept, c)
	}
	return kept
}

// firstClash is the first destination of `c`'s files that is already taken.
func firstClash(vault string, c taskCandidate) string {
	for _, rel := range c.files {
		dst := c.dstOf(rel)
		if _, err := os.Lstat(filepath.Join(vault, filepath.FromSlash(dst))); err == nil {
			return dst
		}
	}
	return ""
}

// taskMoveSets are the file moves of `due`, and the sets of old and new paths.
func taskMoveSets(due []taskCandidate) ([]linkrewrite.Move, map[string]bool, map[string]bool) {
	var moves []linkrewrite.Move
	from, to := map[string]bool{}, map[string]bool{}
	for _, c := range due {
		for _, rel := range c.files {
			dst := c.dstOf(rel)
			moves = append(moves, linkrewrite.Move{From: rel, To: dst})
			from[rel], to[dst] = true, true
		}
	}
	return moves, from, to
}

// afterRun answers whether a vault-relative path holds a file once the moves
// have run.
func afterRun(vault string, from, to map[string]bool) func(string) bool {
	return func(rel string) bool {
		if to[rel] {
			return true
		}
		if from[rel] {
			return false
		}
		_, err := os.Stat(filepath.Join(vault, filepath.FromSlash(rel)))
		return err == nil
	}
}

func inHeldSpace(rel string) bool {
	first, _, _ := strings.Cut(rel, "/")
	for _, s := range heldSpaces {
		if strings.EqualFold(first, s) {
			return true
		}
	}
	return false
}

// mightLink is the cheap test before the rewriter reads a note: a note with no
// wikilink and no markdown link has nothing to rewrite.
func mightLink(raw []byte) bool {
	s := string(raw)
	return strings.Contains(s, "[[") || strings.Contains(s, "](")
}

// taskLitter is what a sync client or a desktop leaves in a folder. It is not
// content: it does not move, and a folder holding only it is empty.
var taskLitter = map[string]bool{".DS_Store": true, "Icon\r": true, "desktop.ini": true}

// taskFiles is every file under a task directory, vault-relative and sorted,
// with the folder's own tracker last; or, when the folder cannot move whole,
// the reason why. Hidden folders inside it move with it. The tracker is what
// says the task closed: moved last, a pass that stops mid-folder leaves it
// where the next pass looks, and that pass finishes the folder.
func taskFiles(vault, dir string, contract *rules.Rules) ([]string, string) {
	var out []string
	reason := ""
	tracker := ""
	err := filepath.WalkDir(dir, func(p string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, rerr := filepath.Rel(vault, p)
		if rerr != nil {
			return rerr
		}
		rel = filepath.ToSlash(rel)
		if contract.IsRecallExempt(rel) {
			reason = rel + " is walled from recall, and the mover does not read it"
			return filepath.SkipAll
		}
		if d.IsDir() {
			return nil
		}
		if taskLitter[d.Name()] {
			return nil
		}
		if !d.Type().IsRegular() {
			reason = rel + " is not a regular file"
			return filepath.SkipAll
		}
		info, err := d.Info()
		if err != nil {
			return err
		}
		if info.Size() > MaxTaskFileBytes {
			reason = fmt.Sprintf("%s is %d MB, too large for the journal to carry", rel, info.Size()>>20)
			return filepath.SkipAll
		}
		if filepath.Dir(p) == dir && strings.EqualFold(d.Name(), "tracker.md") {
			tracker = rel
		}
		out = append(out, rel)
		return nil
	})
	if err != nil && reason == "" {
		reason = "the folder could not be read: " + err.Error()
	}
	if reason != "" {
		return nil, reason
	}
	sort.SliceStable(out, func(i, j int) bool {
		if (out[i] == tracker) != (out[j] == tracker) {
			return out[j] == tracker
		}
		return out[i] < out[j]
	})
	return out, ""
}

// vaultNotes is every markdown note in the vault the mover may read, sorted and
// vault-relative: not the dot-folders, and never a walled area, which is
// refused at the walk so its files are never opened.
func vaultNotes(vault string, contract *rules.Rules) ([]string, error) {
	var out []string
	err := filepath.WalkDir(vault, func(p string, d os.DirEntry, err error) error {
		if err != nil {
			if p == vault {
				return err
			}
			return nil
		}
		rel, rerr := filepath.Rel(vault, p)
		if rerr != nil {
			return nil
		}
		rel = filepath.ToSlash(rel)
		if d.IsDir() {
			if p != vault && (strings.HasPrefix(d.Name(), ".") || contract.IsRecallExempt(rel)) {
				return filepath.SkipDir
			}
			return nil
		}
		if strings.HasSuffix(d.Name(), ".md") && !contract.IsRecallExempt(rel) {
			out = append(out, rel)
		}
		return nil
	})
	sort.Strings(out)
	return out, err
}

// commitOne journals and makes one intent, counting it on the report, and says
// whether it applied.
func commitOne(journal *Journal, root, runID string, in Intent, now time.Time, rep *Report) (bool, error) {
	rep.seq++
	id := fmt.Sprintf("%s-%04d", runID, rep.seq)
	kind, err := journal.Commit(root, runID, id, in, now)
	if err != nil {
		return false, err
	}
	if kind == KindSkipped {
		rep.Skipped++
		rep.SkippedByHandMove = append(rep.SkippedByHandMove, in.Rel)
		return false, nil
	}
	rep.Applied++
	return true, nil
}

// ApplyTaskMoves makes a task plan's moves, folder by folder, then repairs the
// links and re-keys the sidecars from what is on disk. Only a journal error
// stops it: a folder a changed file interrupts is left for the next night, and
// a sidecar that cannot be re-keyed is reported and re-keyed on a later night.
func ApplyTaskMoves(journal *Journal, root, engineStateDir, runID string, plan *TaskMovePlan,
	contract *rules.Rules, now time.Time, pace time.Duration, rep *Report) error {
	plan.Mode = "apply"
	if contract == nil {
		return nil
	}
	vault := vaultRootOf(root)
	for i, f := range plan.Folders {
		intents := plan.folders[i]
		complete := true
		for j, in := range intents {
			if j == len(intents)-1 && !complete {
				// The tracker stays: the folder is not whole under completed/
				// yet, and this is what the next pass looks for.
				plan.Stopped = append(plan.Stopped, HeldTask{From: f.From,
					Reason: "a file changed after the plan; the tracker stays and the next pass carries on"})
				break
			}
			ok, err := commitOne(journal, root, runID, in, now, rep)
			if err != nil {
				return err
			}
			complete = complete && ok
			if pace > 0 {
				time.Sleep(pace)
			}
		}
		if complete && pruneEmptied(filepath.Join(vault, filepath.FromSlash(f.From))) {
			plan.Pruned = append(plan.Pruned, f.From)
		}
	}

	// The repair, from disk: tonight's moves, and any a crashed or interrupted
	// night made without its edits.
	space := ProjectsRoot(root)
	twins := completedTwins(vault, space, contract)
	notes, err := vaultNotes(vault, contract)
	if err != nil {
		plan.Errors = append(plan.Errors, "listing the notes for the link repair: "+err.Error())
		return nil
	}
	edits, edited, _ := linkEdits(vault, root, notes, nil, linkrewrite.New(twins, nil),
		func(rel string) bool {
			_, err := os.Stat(filepath.Join(vault, filepath.FromSlash(rel)))
			return err == nil
		})
	plan.Intents, plan.Edited, plan.Links = nil, nil, 0
	for i, in := range edits {
		ok, err := commitOne(journal, root, runID, in, now, rep)
		if err != nil {
			return err
		}
		if ok {
			plan.Intents = append(plan.Intents, in)
			plan.Edited = append(plan.Edited, edited[i])
			plan.Links += edited[i].Links
		}
	}
	rekeyed, err := RekeySidecars(engineStateDir, vault, twins)
	plan.Rekeyed = rekeyed
	if err != nil {
		plan.Errors = append(plan.Errors, "re-keying the sidecars: "+err.Error())
	}
	return nil
}

// pruneEmptied removes a task folder the moves left with nothing but empty
// folders and sync litter, and says whether it did. A folder still holding a
// file stays: that is a file the plan did not see, and it is not the night's to
// remove.
func pruneEmptied(dir string) bool {
	empty := true
	_ = filepath.WalkDir(dir, func(p string, d os.DirEntry, err error) error {
		if err != nil {
			empty = false
			return filepath.SkipAll
		}
		if !d.IsDir() && !taskLitter[d.Name()] {
			empty = false
			return filepath.SkipAll
		}
		return nil
	})
	if !empty {
		return false
	}
	if _, err := os.Stat(dir); err != nil {
		return false
	}
	return os.RemoveAll(dir) == nil
}

// SidecarFiles are the two engine sidecars keyed by a note's vault-relative
// path: recall heat and the recall-access clock.
var SidecarFiles = []string{".heat.json", ".lifecycle.json"}

// RekeySidecars moves each sidecar entry from a moved note's old path to its
// new one, recomputing the body fingerprint where the entry carries one — the
// same re-key the hand-run moves make (`rekey_sidecars` in
// `scripts/migrate/agentkv_layout.py`), and like it, without the Python writers'
// vault mutex. An entry already present at the new path is left alone. It is a
// repair over what is on disk, so a re-key a concurrent writer undoes is made
// again the next applying night. Returns the count re-keyed per sidecar.
func RekeySidecars(engineStateDir, vault string, moves []linkrewrite.Move) (map[string]int, error) {
	counts := map[string]int{}
	var errs []error
	for _, name := range SidecarFiles {
		p := filepath.Join(engineStateDir, name)
		raw, err := os.ReadFile(p)
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			errs = append(errs, err)
			continue
		}
		var data map[string]any
		if err := json.Unmarshal(raw, &data); err != nil {
			errs = append(errs, fmt.Errorf("%s: %w", p, err))
			continue
		}
		entries, _ := data["entries"].(map[string]any)
		n := 0
		for _, m := range moves {
			e, ok := entries[m.From]
			if !ok {
				continue
			}
			if _, taken := entries[m.To]; taken {
				continue
			}
			if rec, isMap := e.(map[string]any); isMap {
				if _, has := rec["fingerprint"]; has {
					if text, err := os.ReadFile(filepath.Join(vault, filepath.FromSlash(m.To))); err == nil {
						if fp := BodyFingerprint(string(text)); fp != "" {
							rec["fingerprint"] = fp
						}
					}
				}
			}
			delete(entries, m.From)
			entries[m.To] = e
			n++
		}
		counts[name] = n
		if n == 0 {
			continue
		}
		blob, err := json.MarshalIndent(data, "", "  ")
		if err != nil {
			errs = append(errs, err)
			continue
		}
		// Its own temporary name: the daemon's recall clock writes
		// `.lifecycle.json.tmp`, and two writers renaming one temporary file
		// would put either's half in place.
		tmp := fmt.Sprintf("%s.taskmover-%d.tmp", p, os.Getpid())
		if err := os.WriteFile(tmp, append(blob, '\n'), 0o644); err != nil {
			errs = append(errs, err)
			continue
		}
		if err := os.Rename(tmp, p); err != nil {
			_ = os.Remove(tmp)
			errs = append(errs, err)
		}
	}
	return counts, errors.Join(errs...)
}

// MoveTasksOptions bound one `agentmdream move-tasks` run.
type MoveTasksOptions struct {
	Now      time.Time
	Apply    bool
	Cap      int
	RunID    string
	LockWait time.Duration
	Pace     time.Duration
}

// MoveTasks runs the task mover on its own, outside the nightly pass: the
// supervised first move, and the rehearsal against a copy of the vault. It
// takes the pass's own lock. Applying, it first settles anything a crashed pass
// left half-done, then moves — whatever the switch says, because running it by
// hand is the operator's decision to move. Not applying, it writes nothing at
// all, and says how many intents a crash left pending.
func MoveTasks(cfg *config.Config, opt MoveTasksOptions) (TaskMovePlan, Report, error) {
	now := opt.Now
	if now.IsZero() {
		now = time.Now().UTC()
	}
	if opt.LockWait <= 0 {
		opt.LockWait = 2 * time.Second
	}
	rep := Report{Mode: "report"}
	if opt.Apply {
		rep.Mode = "apply"
	}
	root := filepath.Join(cfg.VaultPath, filepath.FromSlash(cfg.MemoryRoot))
	rep.Root = root
	lock, err := Acquire(SingletonLockDir(cfg.EngineStateDir), 30*time.Second, opt.LockWait)
	if err != nil {
		var held *ErrHeld
		if errors.As(err, &held) {
			rep.Refused = held.Error()
			return TaskMovePlan{}, rep, ErrRefused
		}
		return TaskMovePlan{}, rep, err
	}
	defer lock.Release()
	journal, err := OpenJournal(cfg.EngineStateDir)
	if err != nil {
		return TaskMovePlan{}, rep, err
	}
	entries, err := journal.Read()
	if err != nil {
		return TaskMovePlan{}, rep, err
	}
	runID, pending := Unfinished(entries)
	if opt.Apply && runID != "" {
		for _, e := range pending {
			if _, err := journal.Resolve(root, e, now); err != nil {
				return TaskMovePlan{}, rep, err
			}
			rep.Resumed++
		}
		if err := journal.Append(Entry{Kind: KindRunDone, RunID: runID, TS: now,
			Outcome: fmt.Sprintf("resumed: %d intent(s) settled", len(pending))}); err != nil {
			return TaskMovePlan{}, rep, err
		}
	}
	var contract *rules.Rules
	if cfg.Rules != nil {
		if r, err := cfg.Rules.Get(); err == nil {
			contract = r
		}
	}
	plan, err := PlanTaskMoves(root, contract, now, TaskMoveCap(contract, opt.Cap))
	if !opt.Apply {
		plan.Pending = len(pending)
	}
	if err != nil || !opt.Apply || contract == nil {
		return plan, rep, err
	}
	newRun := opt.RunID
	if newRun == "" {
		newRun = newRunID(now)
	}
	rep.RunID = newRun
	if err := journal.Append(Entry{Kind: KindRunStart, RunID: newRun, TS: now, Mode: "apply"}); err != nil {
		return plan, rep, err
	}
	if err := ApplyTaskMoves(journal, root, cfg.EngineStateDir, newRun, &plan, contract, now, opt.Pace, &rep); err != nil {
		return plan, rep, err
	}
	rep.Outcome = OutcomeApplied
	err = journal.Append(Entry{Kind: KindRunDone, RunID: newRun, TS: now,
		Outcome: fmt.Sprintf("move-tasks: %d folder(s), %d applied, %d skipped",
			len(plan.Folders), rep.Applied, rep.Skipped)})
	return plan, rep, err
}
