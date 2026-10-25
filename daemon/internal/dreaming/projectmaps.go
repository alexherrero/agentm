package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/projectbind"
)

// Job "mocs", over the projects space (agentm-vault § Projects and tasks,
// plan 09). Beside the type maps, the same nightly job writes three kinds of
// map into the vault root's `projects/`:
//
//	<slug>/moc-<slug>.md  a project's map, at its root beside the charter: its
//	                      tasks, in flight first by importance and the rest
//	                      folded under the day they closed; its decisions and
//	                      designs, newest first; and its research by bundle
//	moc-tasks.md          every project's tasks in the same order, once any
//	                      project has one
//	moc-projects.md       every project, with its charter's What line, under
//	                      what the space is for, which `projects/index.md`
//	                      used to say
//
// A task is a tracker, `tasks/<task>/tracker.md`: the only place a project
// keeps one since the per-project state directory retired (agentm-vault plan
// 15). A record is dated by its `created`, so the night's enrichment, which stamps `updated`,
// never reorders a map. A page carries its newest input's date as `updated`, so
// a regeneration over unchanged inputs is byte-identical and writes nothing, and
// `created` survives. A map of nothing is not written, and nothing here
// deletes. Paths are memory-root relative, the way the calendar's year maps are
// named, so a page at the vault root is `../projects/...` in the nested layout.

const (
	MocProjectsSlug   = "moc-projects"
	MocTasksSlug      = "moc-tasks"
	projectsSpaceName = "projects"
)

// projectsSpaceText is what `projects/index.md` said about the space, carried
// into the map that retires it (plan 09, operator ruling 2).
const projectsSpaceText = "One tree per project. A project's working life lives in its folder here: " +
	"its charter, its tracker and tasks, its plans and progress, its decisions, designs, research and " +
	"drafts, and what it promotes, a design that went final or a deliverable that shipped. A folder " +
	"appears when you declare a project, and the agent manages one only under a session grant " +
	"(\"open the files for project `<slug>`\"). Nothing arrives here except through the promotion door, " +
	"which files it, rewrites the links that pointed at its old path, records where it came from, and " +
	"lands all of it as one revertible commit. [[../index|index]] says who writes where."

type projectTask struct {
	Project    string
	Task       string
	Title      string
	Status     string
	Importance int
	Opened     string
	Updated    string
	Closed     string
	Design     string
	Link       string
}

type projectRecord struct {
	Link string
	Date string
}

// folderGroup is the notes a project map lists together: those directly in a
// folder (Name ""), or every note at or below one of its subfolders. Past
// spaceMapListLimit the group gets its own map beside its notes, MapAbs.
type folderGroup struct {
	Name   string
	Dir    string
	Notes  []projectRecord
	MapAbs string
}

// projectFolder is one of the project skeleton's working folders (`research/`,
// `drafts/`, `desk/`), or a folder the project's `project.yaml` names under
// `map_folders:` (task 193 LD-2). Rel is project-relative, slash-separated.
type projectFolder struct {
	Rel     string
	Loose   folderGroup
	Bundles []folderGroup
}

func (f projectFolder) notes() int {
	n := len(f.Loose.Notes)
	for _, b := range f.Bundles {
		n += len(b.Notes)
	}
	return n
}

type projectEntry struct {
	Slug      string
	Title     string
	Line      string
	Charter   string
	Tracker   string
	MapLink   string
	Tasks     []projectTask
	Decisions []projectRecord
	Designs   []projectRecord
	Folders   []projectFolder
	Newest    string
}

func (p projectEntry) members() int {
	n := len(p.Tasks) + len(p.Decisions) + len(p.Designs)
	for _, f := range p.Folders {
		n += f.notes()
	}
	return n
}

// mapped says whether the project has anything a map would list.
func (p projectEntry) mapped() bool {
	return p.Charter != "" || p.Tracker != "" || p.members() > 0
}

// projReadNote is a note's frontmatter and body, line endings normalised, or
// false when the path is not a readable file.
func projReadNote(path string) (map[string]string, string, bool) {
	st, err := os.Stat(path)
	if err != nil || st.IsDir() {
		return nil, "", false
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, "", false
	}
	fm, body := ParseFrontmatter(strings.ReplaceAll(string(raw), "\r\n", "\n"))
	return fm, body, true
}

func projDate(v string) string {
	v = strings.TrimSpace(v)
	if len(v) < 10 {
		return ""
	}
	if _, err := time.Parse("2006-01-02", v[:10]); err != nil {
		return ""
	}
	return v[:10]
}

func projNewest(dates ...string) string {
	out := ""
	for _, d := range dates {
		if d = projDate(d); d > out {
			out = d
		}
	}
	return out
}

func projVaultRel(vault, abs string) string {
	rel, err := filepath.Rel(vault, abs)
	if err != nil {
		return filepath.ToSlash(abs)
	}
	return filepath.ToSlash(rel)
}

var projLabelReplacer = strings.NewReplacer("|", "-", "[", "(", "]", ")", "\n", " ")

// projLink links a note by its vault-relative path. Project notes share names,
// since every task's tracker is `tracker.md`, so a stem would not say which.
func projLink(vault, abs, label string) string {
	target := strings.TrimSuffix(projVaultRel(vault, abs), ".md")
	return "[[" + target + "|" + strings.TrimSpace(projLabelReplacer.Replace(label)) + "]]"
}

// projTitle is a note's `title`, else its first `# ` heading, else "".
func projTitle(fm map[string]string, body string) string {
	if t := strings.TrimSpace(fm["title"]); t != "" {
		return t
	}
	for _, line := range strings.Split(body, "\n") {
		if strings.HasPrefix(line, "# ") {
			return strings.TrimSpace(line[2:])
		}
	}
	return ""
}

// projWhatLine is a charter's `**What:**` line, else its first prose line.
func projWhatLine(body string) string {
	for _, line := range strings.Split(body, "\n") {
		if l := strings.TrimSpace(line); strings.HasPrefix(l, "**What:**") {
			return contextPhrase(strings.TrimSpace(strings.TrimPrefix(l, "**What:**")))
		}
	}
	return contextPhrase(body)
}

// projSectionLine is the first line under a `## <heading>` section.
func projSectionLine(body, heading string) string {
	in := false
	for _, line := range strings.Split(body, "\n") {
		l := strings.TrimSpace(line)
		if strings.HasPrefix(l, "## ") {
			in = l == "## "+heading
			continue
		}
		if in && l != "" {
			return contextPhrase(l)
		}
	}
	return ""
}

func readProjectTask(vault, project, path, task string) (projectTask, bool) {
	fm, body, ok := projReadNote(path)
	if !ok || strings.TrimSpace(fm["kind"]) != "tracker" {
		return projectTask{}, false
	}
	if named := strings.TrimSpace(fm["task"]); named != "" {
		task = named
	}
	title := projTitle(fm, body)
	if title == "" {
		title = task
	}
	importance, _ := strconv.Atoi(strings.TrimSpace(fm["importance"]))
	return projectTask{Project: project, Task: task, Title: title, Status: strings.TrimSpace(fm["status"]),
		Importance: importance, Opened: projDate(fm["opened"]), Updated: projDate(fm["updated"]),
		Closed: projDate(fm["closed"]), Design: strings.TrimSpace(fm["design"]),
		Link: projLink(vault, path, title)}, true
}

// projectTasks reads a project's trackers: each task directory's `tracker.md`,
// open under `tasks/` and moved under `completed/tasks/`, each linked where it
// sits.
func projectTasks(vault, project, dir string) []projectTask {
	var out []projectTask
	for _, home := range taskHomes {
		entries, err := os.ReadDir(filepath.Join(dir, home))
		if err != nil {
			continue
		}
		for _, e := range entries {
			if !e.IsDir() || strings.HasPrefix(e.Name(), ".") {
				continue
			}
			path := filepath.Join(dir, home, e.Name(), "tracker.md")
			if t, ok := readProjectTask(vault, project, path, e.Name()); ok {
				out = append(out, t)
			}
		}
	}
	return out
}

// projectRecords lists the notes under a record folder, newest `created` first.
func projectRecords(vault, dir string) []projectRecord {
	var out []projectRecord
	_ = filepath.WalkDir(dir, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		name := d.Name()
		if d.IsDir() {
			if path != dir && (strings.HasPrefix(name, ".") || strings.HasPrefix(name, "_")) {
				return filepath.SkipDir
			}
			return nil
		}
		if strings.HasPrefix(name, ".") || !strings.HasSuffix(name, ".md") {
			return nil
		}
		fm, body, ok := projReadNote(path)
		if !ok {
			return nil
		}
		title := projTitle(fm, body)
		if title == "" {
			title = strings.TrimSuffix(name, ".md")
		}
		date := projDate(fm["created"])
		if date == "" {
			date = projDate(fm["updated"])
		}
		out = append(out, projectRecord{Link: projLink(vault, path, title), Date: date})
		return nil
	})
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Date != out[j].Date {
			return out[i].Date > out[j].Date
		}
		return out[i].Link < out[j].Link
	})
	return out
}

// skeletonFolders are the project skeleton's working folders, in the order the
// map lists them (agentm-vault § The project skeleton). A folder outside them is
// mapped only when the project's `project.yaml` names it under `map_folders:`:
// `projects/movies-tv-games/cleanup/` holds over 1,300 manifests (task 193 LD-2).
var skeletonFolders = []string{"research", "drafts", "desk"}

// projectSections are the folders the map already lists in a section of their
// own, which `map_folders:` cannot ask for a second time.
var projectSections = map[string]bool{"tasks": true, "completed": true, "decisions": true, "designs": true}

// folderNotes lists the notes in dir, or at or below it when recurse is set,
// newest `created` first: no hidden or `_`-prefixed name, and no generated
// `moc-*` page.
func folderNotes(vault, dir string, recurse bool) []projectRecord {
	var out []projectRecord
	_ = filepath.WalkDir(dir, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		name := d.Name()
		if d.IsDir() {
			if path != dir && (!recurse || spaceSkip(name)) {
				return filepath.SkipDir
			}
			return nil
		}
		if spaceSkip(name) || !strings.HasSuffix(name, ".md") {
			return nil
		}
		fm, body, ok := projReadNote(path)
		if !ok {
			return nil
		}
		title := projTitle(fm, body)
		if title == "" {
			title = strings.TrimSuffix(name, ".md")
		}
		out = append(out, projectRecord{Link: projLink(vault, path, title), Date: projDate(fm["created"])})
		return nil
	})
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Date != out[j].Date {
			return out[i].Date > out[j].Date
		}
		return out[i].Link < out[j].Link
	})
	return out
}

// readProjectFolder reads one working folder: its own notes, and each
// subfolder's notes at or below it. A group past spaceMapListLimit is given a
// map of its own, `moc-<name>.md` beside its notes, named for its folder.
func readProjectFolder(vault, projectDir, rel string) projectFolder {
	dir := filepath.Join(projectDir, filepath.FromSlash(rel))
	f := projectFolder{Rel: rel}
	if !isDirExact(dir) {
		return f
	}
	f.Loose = folderGroup{Dir: dir, Notes: folderNotes(vault, dir, false)}
	if len(f.Loose.Notes) > spaceMapListLimit {
		f.Loose.MapAbs = filepath.Join(dir, "moc-"+filepath.Base(dir)+".md")
	}
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		if !e.IsDir() || spaceSkip(e.Name()) {
			continue
		}
		sub := filepath.Join(dir, e.Name())
		g := folderGroup{Name: e.Name(), Dir: sub, Notes: folderNotes(vault, sub, true)}
		if len(g.Notes) == 0 {
			continue
		}
		if len(g.Notes) > spaceMapListLimit {
			g.MapAbs = filepath.Join(sub, "moc-"+e.Name()+".md")
		}
		f.Bundles = append(f.Bundles, g)
	}
	return f
}

// mapFolders is the skeleton's folders, then each `map_folders:` entry the
// project names that is a plain relative path to another folder.
func mapFolders(projectDir string) []string {
	out := append([]string(nil), skeletonFolders...)
	seen := map[string]bool{}
	for _, rel := range out {
		seen[rel] = true
	}
	for _, rel := range projectbind.MapFolders(projectDir) {
		rel = strings.Trim(filepath.ToSlash(strings.TrimSpace(rel)), "/")
		ok := rel != "" && !seen[rel] && !projectSections[strings.SplitN(rel, "/", 2)[0]]
		for _, seg := range strings.Split(rel, "/") {
			if seg == "" || seg == ".." || spaceSkip(seg) {
				ok = false
			}
		}
		if ok {
			seen[rel] = true
			out = append(out, rel)
		}
	}
	return out
}

func readProject(vault, dir string) projectEntry {
	slug := filepath.Base(dir)
	p := projectEntry{Slug: slug, Title: slug}
	var dates []string
	for _, name := range []string{"charter.md", "_index.md"} {
		path := filepath.Join(dir, name)
		fm, body, ok := projReadNote(path)
		if !ok {
			continue
		}
		if t := projTitle(fm, body); t != "" {
			p.Title = t
		}
		p.Line = projWhatLine(body)
		p.Charter = projLink(vault, path, "charter")
		dates = append(dates, fm["created"], fm["updated"])
		break
	}
	trackerPath := filepath.Join(dir, "tracker.md")
	// The project's tracker is linked, and its date counts, but its State no
	// longer glosses the project: since task 176 the night generates it as a
	// checklist of the tasks, and the map already counts those. The charter's
	// What line says what the project is.
	if fm, _, ok := projReadNote(trackerPath); ok && strings.TrimSpace(fm["kind"]) == "tracker" {
		p.Tracker = projLink(vault, trackerPath, "tracker")
		dates = append(dates, fm["updated"])
	}
	p.Tasks = projectTasks(vault, slug, dir)
	for _, t := range p.Tasks {
		dates = append(dates, t.Updated, t.Closed)
	}
	p.Decisions = projectRecords(vault, filepath.Join(dir, "decisions"))
	p.Designs = projectRecords(vault, filepath.Join(dir, "designs"))
	for _, records := range [][]projectRecord{p.Decisions, p.Designs} {
		for _, r := range records {
			dates = append(dates, r.Date)
		}
	}
	for _, rel := range mapFolders(dir) {
		f := readProjectFolder(vault, dir, rel)
		if f.notes() == 0 {
			continue
		}
		p.Folders = append(p.Folders, f)
		for _, g := range append([]folderGroup{f.Loose}, f.Bundles...) {
			for _, r := range g.Notes {
				dates = append(dates, r.Date)
			}
		}
	}
	p.Newest = projNewest(dates...)
	p.MapLink = strings.TrimSuffix(projVaultRel(vault, filepath.Join(dir, "moc-"+slug+".md")), ".md")
	return p
}

var projStatusRank = map[string]int{"active": 0, "parked": 1, "queued": 2}

// sortProjectTasks splits tasks into those in flight, by importance, and those
// closed, newest closing first.
func sortProjectTasks(tasks []projectTask) (inFlight, closed []projectTask) {
	for _, t := range tasks {
		if t.Status == "done" || t.Status == "dropped" {
			closed = append(closed, t)
		} else {
			inFlight = append(inFlight, t)
		}
	}
	rank := func(status string) int {
		if r, ok := projStatusRank[status]; ok {
			return r
		}
		return len(projStatusRank)
	}
	sort.SliceStable(inFlight, func(i, j int) bool {
		a, b := inFlight[i], inFlight[j]
		if a.Importance != b.Importance {
			return a.Importance > b.Importance
		}
		if rank(a.Status) != rank(b.Status) {
			return rank(a.Status) < rank(b.Status)
		}
		if a.Project != b.Project {
			return a.Project < b.Project
		}
		return a.Task < b.Task
	})
	sort.SliceStable(closed, func(i, j int) bool {
		a, b := closed[i], closed[j]
		if a.Closed != b.Closed {
			return a.Closed > b.Closed
		}
		if a.Project != b.Project {
			return a.Project < b.Project
		}
		return a.Task < b.Task
	})
	return inFlight, closed
}

func projectTaskLine(t projectTask, withProject bool) string {
	parts := []string{t.Link}
	if withProject {
		parts = append(parts, t.Project)
	}
	if t.Status != "" {
		parts = append(parts, t.Status)
	}
	if t.Importance > 0 {
		parts = append(parts, fmt.Sprintf("importance %d", t.Importance))
	}
	return "- " + strings.Join(parts, " · ")
}

// foldClosedTasks renders closed tasks as collapsed callouts, one per day.
func foldClosedTasks(closed []projectTask, withProject bool) []string {
	var lines []string
	for i := 0; i < len(closed); {
		day := closed[i].Closed
		label := "Closed " + day
		if day == "" {
			label = "Closed, undated"
		}
		if len(lines) > 0 {
			lines = append(lines, "")
		}
		lines = append(lines, "> [!done]- "+label)
		for ; i < len(closed) && closed[i].Closed == day; i++ {
			lines = append(lines, "> "+projectTaskLine(closed[i], withProject))
		}
	}
	return lines
}

func renderProjectMap(vault string, p projectEntry, created, updated string) string {
	if created == "" {
		created = updated
	}
	lines := []string{"---", "title: " + p.Slug + " — map of content", "kind: moc", "status: active",
		"created: " + created, "updated: " + updated, "tags: [moc, projects, " + p.Slug + "]",
		"slug: moc-" + p.Slug, fmt.Sprintf("members: %d", p.members()), "generated_by: " + mocGeneratedBy,
		"---", "", "# " + p.Title, ""}
	nav := []string{"[[" + MocProjectsSlug + "]]"}
	if p.Charter != "" {
		nav = append(nav, p.Charter)
	}
	if p.Tracker != "" {
		nav = append(nav, p.Tracker)
	}
	lines = append(lines, strings.Join(nav, " · "), "")
	if p.Line != "" {
		lines = append(lines, p.Line, "")
	}
	lines = append(lines, "Generated nightly from the project's trackers and records; not edited by hand.")
	if len(p.Tasks) > 0 {
		inFlight, closed := sortProjectTasks(p.Tasks)
		lines = append(lines, "", "## Tasks", "")
		if len(inFlight) == 0 {
			lines = append(lines, "No task is in flight.")
		}
		for _, t := range inFlight {
			lines = append(lines, projectTaskLine(t, false))
		}
		if len(closed) > 0 {
			lines = append(lines, "")
			lines = append(lines, foldClosedTasks(closed, false)...)
		}
	}
	for _, section := range []struct {
		name    string
		records []projectRecord
	}{{"Decisions", p.Decisions}, {"Designs", p.Designs}} {
		if len(section.records) == 0 {
			continue
		}
		lines = append(lines, "", "## "+section.name, "")
		for _, r := range section.records {
			line := "- " + r.Link
			if r.Date != "" {
				line += " · " + r.Date
			}
			lines = append(lines, line)
		}
	}
	for _, f := range p.Folders {
		lines = append(lines, "", "## "+folderHeading(f.Rel), "")
		lines = append(lines, folderLines(vault, f)...)
	}
	return mocJoin(append(lines, ""))
}

// folderHeading is a skeleton folder's section name, `Research`, or a mapped
// folder's path, `analysis/`.
func folderHeading(rel string) string {
	for _, s := range skeletonFolders {
		if rel == s {
			return strings.ToUpper(rel[:1]) + rel[1:]
		}
	}
	return rel + "/"
}

func recordLine(indent string, r projectRecord) string {
	line := indent + "- " + r.Link
	if r.Date != "" {
		line += " · " + r.Date
	}
	return line
}

// folderLines renders a working folder's section: its own notes, then each
// subfolder, listed while it holds spaceMapListLimit notes or fewer and linked
// to its own map once it holds more (task 193 LD-2).
func folderLines(vault string, f projectFolder) []string {
	var lines []string
	if n := len(f.Loose.Notes); n > 0 && f.Loose.MapAbs != "" {
		lines = append(lines, fmt.Sprintf("- %s — %d %s", projLink(vault, f.Loose.MapAbs, f.Rel+"/"), n, mocPlural(n, "note", "notes")))
	} else {
		for _, r := range f.Loose.Notes {
			lines = append(lines, recordLine("", r))
		}
	}
	for _, g := range f.Bundles {
		if g.MapAbs != "" {
			n := len(g.Notes)
			lines = append(lines, fmt.Sprintf("- %s — %d %s", projLink(vault, g.MapAbs, g.Name+"/"), n, mocPlural(n, "note", "notes")))
			continue
		}
		lines = append(lines, "- `"+g.Name+"/`")
		for _, r := range g.Notes {
			lines = append(lines, recordLine("  ", r))
		}
	}
	return lines
}

// renderFolderMap renders a working folder's own map, for a group past
// spaceMapListLimit: every note in it, newest first, under a link back to the
// project's map.
func renderFolderMap(p projectEntry, folder string, g folderGroup, created, updated string) string {
	if created == "" {
		created = updated
	}
	name := strings.TrimSuffix(strings.TrimPrefix(filepath.Base(g.MapAbs), "moc-"), ".md")
	where := folder + "/"
	if g.Name != "" {
		where += g.Name + "/"
	}
	n := len(g.Notes)
	lines := []string{"---", "title: " + p.Slug + " " + where + " — map of content", "kind: moc", "status: active",
		"created: " + created, "updated: " + updated, "tags: [moc, projects, " + p.Slug + "]",
		"slug: moc-" + name, fmt.Sprintf("members: %d", n), "generated_by: " + mocGeneratedBy,
		"---", "", "# " + where, "", "[[" + p.MapLink + "|" + p.Slug + "]]", "",
		fmt.Sprintf("The %d %s in `%s` of project %s, newest first. Generated nightly from the folder's notes; not edited by hand.",
			n, mocPlural(n, "note", "notes"), where, p.Slug), ""}
	for _, r := range g.Notes {
		lines = append(lines, recordLine("", r))
	}
	return mocJoin(append(lines, ""))
}

func renderTasksMap(tasks []projectTask, projects int, created, updated string) string {
	if created == "" {
		created = updated
	}
	inFlight, closed := sortProjectTasks(tasks)
	lines := []string{"---", "title: tasks — map of content", "kind: moc", "status: active",
		"created: " + created, "updated: " + updated, "tags: [moc, projects, tasks]", "slug: " + MocTasksSlug,
		fmt.Sprintf("members: %d", len(tasks)), "generated_by: " + mocGeneratedBy, "---", "", "# tasks", "",
		"[[" + MocRootSlug + "]] · [[" + MocProjectsSlug + "]]", "",
		fmt.Sprintf("%d %s across %d %s: in flight first, by importance, and the rest folded under the day they closed. Generated nightly from each task's tracker; not edited by hand.",
			len(tasks), mocPlural(len(tasks), "task", "tasks"), projects, mocPlural(projects, "project", "projects")),
		"", "## In flight", ""}
	if len(inFlight) == 0 {
		lines = append(lines, "No task is in flight.")
	}
	for _, t := range inFlight {
		lines = append(lines, projectTaskLine(t, true))
	}
	if len(closed) > 0 {
		lines = append(lines, "", "## Closed", "")
		lines = append(lines, foldClosedTasks(closed, true)...)
	}
	return mocJoin(append(lines, ""))
}

func renderProjectsMap(projects []projectEntry, mapped map[string]bool, hasTasks bool, created, updated string) string {
	if created == "" {
		created = updated
	}
	lines := []string{"---", "title: projects — map of content", "kind: moc", "status: active",
		"created: " + created, "updated: " + updated, "tags: [moc, projects]", "slug: " + MocProjectsSlug,
		fmt.Sprintf("members: %d", len(projects)), "generated_by: " + mocGeneratedBy, "---", "", "# projects", ""}
	nav := "[[" + MocRootSlug + "]]"
	if hasTasks {
		nav += " · [[" + MocTasksSlug + "]]"
	}
	lines = append(lines, nav, "", projectsSpaceText, "",
		"Generated nightly from each project's charter and tracker; not edited by hand.", "", "## Projects", "")
	for _, p := range projects {
		line := "- " + p.Slug
		if mapped[p.Slug] {
			line = "- [[" + p.MapLink + "|" + p.Slug + "]]"
		}
		if p.Line != "" {
			line += " — " + p.Line
		}
		inFlight, _ := sortProjectTasks(p.Tasks)
		if n := len(inFlight); n > 0 {
			line += fmt.Sprintf(" · %d %s in flight", n, mocPlural(n, "task", "tasks"))
		}
		lines = append(lines, line)
	}
	return mocJoin(append(lines, ""))
}

// memoryRel names a page the way an intent does: relative to the memory root,
// with forward slashes, climbing out of it when the page is at the vault root.
func memoryRel(root, abs string) string {
	rel, err := filepath.Rel(root, abs)
	if err != nil {
		return filepath.ToSlash(abs)
	}
	return filepath.ToSlash(rel)
}

// PlanProjectMaps decides the projects space's maps. It writes nothing, and
// plans nothing when the vault has no projects space.
func PlanProjectMaps(root string, now time.Time) (MocsPlan, error) {
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
	var projects []projectEntry
	var tasks []projectTask
	withTasks := 0
	mapped := map[string]bool{}
	for _, e := range entries {
		name := e.Name()
		// `_archive/` holds retired projects and `completed/` finished ones;
		// the migration and the axis per space take those on, and neither is a
		// project of its own.
		if !e.IsDir() || strings.HasPrefix(name, "_") || strings.HasPrefix(name, ".") || name == "completed" {
			continue
		}
		p := readProject(vault, filepath.Join(space, name))
		projects = append(projects, p)
		if len(p.Tasks) > 0 {
			withTasks++
			tasks = append(tasks, p.Tasks...)
		}
		if !p.mapped() {
			continue
		}
		mapped[name] = true
		updated := p.Newest
		if updated == "" {
			updated = today
		}
		rel := memoryRel(root, filepath.Join(space, name, "moc-"+name+".md"))
		before, created := mocCurrentPage(root, rel)
		plan.add(MocPage{Rel: rel, Members: p.members(), Newest: updated}, before,
			renderProjectMap(vault, p, created, updated), fmt.Sprintf("the map of project %s regenerated", name))
		// A working folder's group past the list limit has its own map (task 193 LD-2).
		for _, f := range p.Folders {
			for _, g := range append([]folderGroup{f.Loose}, f.Bundles...) {
				if g.MapAbs == "" {
					continue
				}
				var dates []string
				for _, r := range g.Notes {
					dates = append(dates, r.Date)
				}
				gUpdated := projNewest(dates...)
				if gUpdated == "" {
					gUpdated = today
				}
				gRel := memoryRel(root, g.MapAbs)
				gBefore, gCreated := mocCurrentPage(root, gRel)
				plan.add(MocPage{Rel: gRel, Members: len(g.Notes), Newest: gUpdated}, gBefore,
					renderFolderMap(p, f.Rel, g, gCreated, gUpdated),
					fmt.Sprintf("the map of %s's %s regenerated (%d %s)", name, projVaultRel(filepath.Join(space, name), g.Dir),
						len(g.Notes), mocPlural(len(g.Notes), "note", "notes")))
			}
		}
	}
	if len(projects) == 0 {
		return plan, nil
	}
	if len(tasks) > 0 {
		var dates []string
		for _, t := range tasks {
			dates = append(dates, t.Updated, t.Closed)
		}
		updated := projNewest(dates...)
		if updated == "" {
			updated = today
		}
		rel := memoryRel(root, filepath.Join(space, MocTasksSlug+".md"))
		before, created := mocCurrentPage(root, rel)
		plan.add(MocPage{Rel: rel, Members: len(tasks), Newest: updated}, before,
			renderTasksMap(tasks, withTasks, created, updated),
			fmt.Sprintf("the tasks map regenerated (%d %s)", len(tasks), mocPlural(len(tasks), "task", "tasks")))
	}
	var dates []string
	for _, p := range projects {
		dates = append(dates, p.Newest)
	}
	updated := projNewest(dates...)
	if updated == "" {
		updated = today
	}
	rel := memoryRel(root, filepath.Join(space, MocProjectsSlug+".md"))
	before, created := mocCurrentPage(root, rel)
	plan.add(MocPage{Rel: rel, Members: len(projects), Newest: updated}, before,
		renderProjectsMap(projects, mapped, len(tasks) > 0, created, updated),
		fmt.Sprintf("the projects map regenerated (%d %s)", len(projects), mocPlural(len(projects), "project", "projects")))
	return plan, nil
}
