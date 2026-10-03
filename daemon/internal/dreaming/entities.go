package dreaming

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/cardshape"
	"github.com/alexherrero/agentm/daemon/internal/config"
	"github.com/alexherrero/agentm/daemon/internal/extract"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/projectbind"
	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// The entity builder (task 179, agentm-vault § Dreaming, "Entity pages are
// built each night, with no model call").
//
// "What do I know about X?" gets a first answer: one page for each repository,
// repo-qualified issue and release the vault keeps mentioning, under
// `memory/entities/<folder>/`, listing every note that mentions it — grouped by
// project, then by space, newest first. The pages are derived: this job is
// their only writer, it rebuilds them from the index every night at no model
// cost, and a page whose entity falls below its bar is removed through the
// journal, which keeps the page's bytes. A hand-written note in `entities/` is
// never touched: the job rewrites and removes only `kind: entity-profile`.

// JobEntities is the builder's name in the journal.
const JobEntities = "entities"

// Default bars, used when the contract does not say.
const (
	defaultEntityMinMentions   = 2
	defaultPersonMinSharedWork = 2
)

// EntityPage is one page the builder wrote, or would.
type EntityPage struct {
	Type     string `json:"type"`
	ID       string `json:"id"`
	Rel      string `json:"rel"`
	Title    string `json:"title"`
	Mentions int    `json:"mentions"`
	// Last is the newest mention's day.
	Last string `json:"last_seen,omitempty"`
	// SharedWork is a person's count of shared-work notes.
	SharedWork int  `json:"shared_work,omitempty"`
	Changed    bool `json:"changed"`
}

// EntitiesPlan is what the builder decided.
type EntitiesPlan struct {
	Intents []Intent     `json:"-"`
	Pages   []EntityPage `json:"pages"`
	// Removed is every page removed because its entity fell below its bar,
	// memory-root relative.
	Removed []string `json:"removed,omitempty"`
	// Counts is pages by type.
	Counts map[string]int `json:"counts"`
	// Skipped says why the builder did nothing, when it did nothing.
	Skipped string `json:"skipped,omitempty"`
	// Held is every page not written because a hand-written note sits at its
	// path: the builder never overwrites a note it did not write.
	Held []string `json:"held,omitempty"`
	// PeopleHeld is every note the people half could not read tonight; while
	// any is listed, the people pages stand as they were.
	PeopleHeld []string `json:"people_held,omitempty"`
}

// EntityThresholds are the contract's bars, or the defaults.
func EntityThresholds(r *rules.Rules) (minMentions, minSharedWork int) {
	minMentions, minSharedWork = defaultEntityMinMentions, defaultPersonMinSharedWork
	if r == nil {
		return
	}
	if v, ok := r.Threshold("entity_min_mentions"); ok && v >= 1 {
		minMentions = int(v)
	}
	if v, ok := r.Threshold("person_min_shared_work"); ok && v >= 1 {
		minSharedWork = int(v)
	}
	return
}

// mention is one note on a page.
type mention struct {
	Rel     string
	Title   string
	Project string
	Space   string
	Date    string
	// Shared marks a person's shared-work note.
	Shared bool
}

// EntitySources is what the builder reads from the index. *index.Index
// satisfies it.
type EntitySources interface {
	EntityRows(ctx context.Context) ([]index.EntityRow, error)
	NoteRows(ctx context.Context) ([]index.NoteRow, error)
}

// PlanEntities decides the repository, issue, release and people pages. It
// writes nothing. root is the memory root and vault the configured vault root;
// an empty vault falls back to the one the layout implies.
func PlanEntities(root, vault string, src EntitySources, opts PeopleOptions, r *rules.Rules, now time.Time) (EntitiesPlan, error) {
	plan := EntitiesPlan{Counts: map[string]int{}}
	if vault == "" {
		vault = vaultRootOf(root)
	}
	memRel := memoryRootRel(root, vault)
	projects := projectbind.Repositories(vault)
	former := projectbind.FormerNames(vault)
	facts := readRepoFacts(projectbind.Clones(vault))
	minMentions, minSharedWork := EntityThresholds(r)

	rows, err := src.EntityRows(context.Background())
	if err != nil {
		return plan, err
	}
	byURI := map[string]map[string]mention{}
	for _, row := range rows {
		if !extract.Paged(row.URI) || entitySourceExcluded(row.Path, row.Flags, memRel) {
			continue
		}
		uri := foldFormerName(row.URI, former)
		if byURI[uri] == nil {
			byURI[uri] = map[string]mention{}
		}
		byURI[uri][row.Path] = mentionOf(row.NoteRow, projects)
	}

	// The bar counts sources, not notes (task 186): a task folder is one
	// source. And a number or a version the repository's clone has never had
	// gets no page, however often it is mentioned.
	wanted := map[string]bool{}
	var uris []string
	for uri, ms := range byURI {
		if distinctSources(ms) >= minMentions && clonePermits(uri, facts) {
			uris = append(uris, uri)
		}
	}
	sort.Strings(uris)
	today := now.UTC().Format("2006-01-02")
	for _, uri := range uris {
		kind, id, _ := strings.Cut(uri, ":")
		ms := sortedMentions(byURI[uri])
		page := entityPageFor(kind, id, ms)
		if kind == "repo" {
			page.aliases = append(page.aliases, formerAliases(id, former)...)
		}
		rel := entityRel(memRel, kind, page.slug)
		before, created := mocCurrentPage(root, relUnderRoot(rel, memRel))
		if handWritten(before) {
			plan.Held = append(plan.Held, rel)
			continue
		}
		wanted[rel] = true
		if created == "" {
			created = today
		}
		text := renderEntityPage(page, ms, created)
		plan.add(EntityPage{Type: kind, ID: uri, Rel: rel, Title: page.title, Mentions: len(ms), Last: page.last},
			before, text, relUnderRoot(rel, memRel))
		plan.Counts[kind]++
	}
	people, err := planPeople(&plan, root, vault, memRel, src, opts, projects, minSharedWork, now)
	if err != nil {
		return plan, err
	}
	for rel := range people {
		wanted[rel] = true
	}
	plan.removeUnwanted(root, memRel, wanted, []string{"repo", "issue", "release", "person"})
	return plan, nil
}

// entityPage is what a page renders from.
type entityPage struct {
	kind, id, uri, title, slug string
	aliases                    []string
	projects                   []string
	first, last                string
	sharedWork                 int
}

// entityPageFor names and describes one repository, issue or release page.
func entityPageFor(kind, id string, ms []mention) entityPage {
	p := entityPage{kind: kind, id: id, uri: kind + ":" + id}
	short := func(repo string) string {
		if _, name, ok := strings.Cut(repo, "/"); ok {
			return name
		}
		return repo
	}
	switch kind {
	case "repo":
		p.title = id
		p.aliases = []string{short(id)}
	case "issue":
		repo, num, _ := strings.Cut(id, "#")
		p.title = id
		p.aliases = []string{short(repo) + "#" + num}
	case "release":
		repo, version, _ := strings.Cut(id, "@")
		p.title = repo + " " + version
		p.aliases = []string{short(repo) + " " + version}
	}
	p.slug = cardshape.EntitySlug(id)
	p.projects, p.first, p.last = mentionSpan(ms)
	return p
}

// entityRel is a page's vault-relative path.
func entityRel(memRel, kind, slug string) string {
	return path.Join(memRel, "memory", "entities", cardshape.EntityFolders[kind], slug+".md")
}

// mentionOf reads one note as a mention: its project (its place first, then its
// label) and its space.
func mentionOf(n index.NoteRow, projects map[string][]string) mention {
	return mention{
		Rel: n.Path, Title: displayTitle(n.Path, n.Title), Date: n.Date,
		Project: projectbind.NoteProject(n.Path, n.Project, projects),
		Space:   spaceOf(n.Path),
	}
}

// displayTitle is the label a note is listed under. The index's title column
// is the note's title followed by its file name's words, so search matches
// either; a page shows the title alone. A note with no title of its own is
// named by its file, and a task's `plan`, `progress` or `tracker` — or a
// project's front page — by its folder too, since "progress" alone says
// nothing on a list of forty.
func displayTitle(rel, column string) string {
	stem := strings.TrimSuffix(path.Base(rel), ".md")
	words := strings.NewReplacer("-", " ", "_", " ").Replace(stem)
	column = strings.TrimSpace(column)
	if title := strings.TrimSpace(strings.TrimSuffix(column, " "+words)); title != "" && column != words {
		return title
	}
	switch strings.ToLower(stem) {
	case "plan", "progress", "tracker", "charter", "blueprint", "brief", "_index", "index":
		return path.Base(path.Dir(rel)) + " " + strings.TrimPrefix(stem, "_")
	}
	return words
}

// spaceOf is the space a note sits in: the vault root's folder, and for the
// agent's own folder the one below it (`memory`, `inbox`).
func spaceOf(rel string) string {
	parts := strings.Split(rel, "/")
	if len(parts) == 1 {
		return "vault root"
	}
	if strings.EqualFold(parts[0], "agent") && len(parts) > 2 {
		return parts[1]
	}
	return parts[0]
}

// entitySourceExcluded is a note that never counts as a mention: a page must
// never count itself, a map or the night's own record repeats what it lists,
// and a superseded note's words live on in what replaced it. The operator's
// people table names everyone it lists, and is not about any of them.
func entitySourceExcluded(rel, flags, memRel string) bool {
	if strings.Contains(","+flags+",", ",lifecycle-superseded,") {
		return true
	}
	low := strings.ToLower(rel)
	prefix := ""
	if memRel != "" {
		prefix = strings.ToLower(memRel) + "/"
	}
	for _, p := range []string{
		prefix + "memory/entities/", prefix + "memory/mocs/", prefix + "memory/crystallized/",
		prefix + "diagnostics/", "standards/people/",
	} {
		if strings.HasPrefix(low, p) {
			return true
		}
	}
	base := path.Base(low)
	if strings.HasPrefix(base, "moc-") || low == "index.md" || low == "ideas.md" {
		return true
	}
	if strings.HasPrefix(low, "calendar/") &&
		(strings.HasSuffix(base, "-dreaming.md") || strings.HasSuffix(base, "-review.md")) {
		return true
	}
	return false
}

// sortedMentions is a page's notes, newest first and then by path.
func sortedMentions(set map[string]mention) []mention {
	ms := make([]mention, 0, len(set))
	for _, m := range set {
		ms = append(ms, m)
	}
	sort.Slice(ms, func(i, j int) bool {
		if ms[i].Date != ms[j].Date {
			return ms[i].Date > ms[j].Date
		}
		return ms[i].Rel < ms[j].Rel
	})
	return ms
}

// mentionSpan is the projects a page's notes belong to, and its first and last day.
func mentionSpan(ms []mention) (projects []string, first, last string) {
	seen := map[string]bool{}
	for _, m := range ms {
		if m.Project != "" && !seen[m.Project] {
			seen[m.Project] = true
			projects = append(projects, m.Project)
		}
		if m.Date == "" {
			continue
		}
		if first == "" || m.Date < first {
			first = m.Date
		}
		if m.Date > last {
			last = m.Date
		}
	}
	sort.Strings(projects)
	return
}

// renderEntityPage is a page's bytes. Everything on it comes from the notes it
// lists and the day the page was first written, so an unchanged corpus renders
// the same page and a rebuild writes nothing.
func renderEntityPage(p entityPage, ms []mention, created string) string {
	var b strings.Builder
	b.WriteString("---\n")
	fmt.Fprintf(&b, "title: %s\n", yamlValue(p.title))
	fmt.Fprintf(&b, "kind: %s\n", cardshape.EntityProfileKind)
	fmt.Fprintf(&b, "created: %s\n", created)
	updated := p.last
	if updated == "" {
		updated = created
	}
	fmt.Fprintf(&b, "updated: %s\n", updated)
	fmt.Fprintf(&b, "entity_type: %s\n", p.kind)
	fmt.Fprintf(&b, "entity_id: %s\n", yamlValue(p.uri))
	fmt.Fprintf(&b, "projects: %s\n", yamlList(p.projects))
	if p.first != "" {
		fmt.Fprintf(&b, "first_seen: %s\n", p.first)
		fmt.Fprintf(&b, "last_seen: %s\n", p.last)
	}
	fmt.Fprintf(&b, "mentions: %d\n", len(ms))
	if p.kind == "person" {
		fmt.Fprintf(&b, "shared_work: %d\n", p.sharedWork)
	}
	fmt.Fprintf(&b, "slug: %s\n", p.slug)
	if len(p.aliases) > 0 {
		fmt.Fprintf(&b, "aliases: %s\n", yamlList(p.aliases))
	}
	b.WriteString("---\n\n")
	fmt.Fprintf(&b, "# %s\n\n", p.title)
	span := ""
	if p.first != "" {
		span = fmt.Sprintf(", from %s to %s", p.first, p.last)
	}
	fmt.Fprintf(&b, "Mentioned in %d %s%s. Built each night from the notes that mention it; edit those, not this page.\n",
		len(ms), plural(len(ms), "note", "notes"), span)
	if p.kind == "person" {
		var shared, other []mention
		for _, m := range ms {
			if m.Shared {
				shared = append(shared, m)
			} else {
				other = append(other, m)
			}
		}
		b.WriteString("\n## Shared work\n")
		writeGrouped(&b, shared, "###")
		if len(other) > 0 {
			b.WriteString("\n## Other mentions\n")
			writeGrouped(&b, other, "###")
		}
		return b.String()
	}
	writeGrouped(&b, ms, "##")
	return b.String()
}

// writeGrouped lists notes by project, the busiest first, and within a project
// by space, newest first. Notes outside every project come last.
func writeGrouped(b *strings.Builder, ms []mention, h string) {
	byProject := map[string][]mention{}
	for _, m := range ms {
		byProject[m.Project] = append(byProject[m.Project], m)
	}
	var names []string
	for name := range byProject {
		if name != "" {
			names = append(names, name)
		}
	}
	sort.Slice(names, func(i, j int) bool {
		if len(byProject[names[i]]) != len(byProject[names[j]]) {
			return len(byProject[names[i]]) > len(byProject[names[j]])
		}
		return names[i] < names[j]
	})
	if len(byProject[""]) > 0 {
		names = append(names, "")
	}
	for _, name := range names {
		heading := name
		if heading == "" {
			heading = "Outside any project"
		}
		fmt.Fprintf(b, "\n%s %s\n", h, heading)
		bySpace := map[string][]mention{}
		var spaces []string
		for _, m := range byProject[name] {
			if bySpace[m.Space] == nil {
				spaces = append(spaces, m.Space)
			}
			bySpace[m.Space] = append(bySpace[m.Space], m)
		}
		sort.Strings(spaces)
		for _, space := range spaces {
			fmt.Fprintf(b, "\n%s# %s\n\n", h, space)
			for _, m := range bySpace[space] {
				day := m.Date
				if day == "" {
					day = "undated"
				}
				fmt.Fprintf(b, "- %s · %s\n", day, noteLink(m))
			}
		}
	}
}

// noteLink is an Obsidian link to a note, labelled with its title.
func noteLink(m mention) string {
	target := strings.TrimSuffix(m.Rel, ".md")
	label := m.Title
	if label == "" {
		label = path.Base(target)
	}
	label = strings.NewReplacer("|", "-", "[", "(", "]", ")", "\n", " ").Replace(label)
	return "[[" + target + "|" + label + "]]"
}

// yamlValue quotes a value when YAML would otherwise read it as something else.
func yamlValue(s string) string {
	if s == "" || strings.ContainsAny(s, ":#[]{}&*!|>'\"%@`,") || strings.HasPrefix(s, "-") ||
		strings.HasPrefix(s, " ") || strings.HasSuffix(s, " ") {
		blob, _ := json.Marshal(s)
		return string(blob)
	}
	return s
}

func yamlList(values []string) string {
	quoted := make([]string, 0, len(values))
	for _, v := range values {
		quoted = append(quoted, yamlValue(v))
	}
	return "[" + strings.Join(quoted, ", ") + "]"
}

func plural(n int, one, many string) string {
	if n == 1 {
		return one
	}
	return many
}

// add records a page, and an intent when its text differs from the file.
func (plan *EntitiesPlan) add(item EntityPage, before []byte, text, rel string) {
	if before != nil && string(before) == text {
		plan.Pages = append(plan.Pages, item)
		return
	}
	item.Changed = true
	plan.Pages = append(plan.Pages, item)
	plan.Intents = append(plan.Intents, Intent{Job: JobEntities, Rel: rel, Before: before, After: []byte(text),
		Summary: fmt.Sprintf("the entity page for %s written (%d mentions)", item.ID, item.Mentions)})
}

// handWritten reports whether the bytes at a page's path are a note the builder
// did not write: anything but its own `entity-profile` record.
func handWritten(before []byte) bool {
	if before == nil {
		return false
	}
	fm, _ := ParseFrontmatter(string(before))
	return strings.TrimSpace(fm["kind"]) != cardshape.EntityProfileKind
}

// existingPages is every builder page of one type on disk, vault-relative.
func existingPages(root, memRel, kind string) []string {
	dir := filepath.Join(root, "memory", "entities", cardshape.EntityFolders[kind])
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	var out []string
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".md") {
			continue
		}
		if cur, err := os.ReadFile(filepath.Join(dir, e.Name())); err == nil && !handWritten(cur) {
			out = append(out, path.Join(memRel, "memory", "entities", cardshape.EntityFolders[kind], e.Name()))
		}
	}
	return out
}

// removeUnwanted removes every page of the given types that this build did not
// plan: the builder's own `entity-profile` pages only, through the journal.
func (plan *EntitiesPlan) removeUnwanted(root, memRel string, wanted map[string]bool, kinds []string) {
	for _, kind := range kinds {
		dir := filepath.Join(root, "memory", "entities", cardshape.EntityFolders[kind])
		entries, err := os.ReadDir(dir)
		if err != nil {
			continue
		}
		for _, e := range entries {
			if e.IsDir() || !strings.HasSuffix(e.Name(), ".md") {
				continue
			}
			rel := path.Join(memRel, "memory", "entities", cardshape.EntityFolders[kind], e.Name())
			if wanted[rel] {
				continue
			}
			cur, err := os.ReadFile(filepath.Join(dir, e.Name()))
			if err != nil {
				continue
			}
			fm, _ := ParseFrontmatter(string(cur))
			if strings.TrimSpace(fm["kind"]) != cardshape.EntityProfileKind {
				continue // a hand-written note is left where it is
			}
			under := relUnderRoot(rel, memRel)
			plan.Removed = append(plan.Removed, under)
			plan.Intents = append(plan.Intents, Intent{Job: JobEntities, Rel: under, Before: cur, Delete: true,
				Summary: fmt.Sprintf("the entity page %s removed (under its bar)", strings.TrimSpace(fm["entity_id"]))})
		}
	}
}

// memoryRootRel is the memory root relative to the vault root, "" for a flat
// layout where they are one directory.
func memoryRootRel(root, vault string) string {
	if rel, err := filepath.Rel(vault, root); err == nil && rel != "." {
		return filepath.ToSlash(rel)
	}
	return ""
}

// relUnderRoot turns a vault-relative path into the memory-root-relative one
// an intent names.
func relUnderRoot(rel, memRel string) string {
	if memRel == "" {
		return rel
	}
	return strings.TrimPrefix(rel, memRel+"/")
}

// BuildEntitiesOptions is one by-hand run of the builder.
type BuildEntitiesOptions struct {
	Apply    bool
	Now      time.Time
	RunID    string
	LockWait time.Duration
}

// BuildEntities runs the entity builder on its own, the way `move-tasks` runs
// the mover: the first supervised build, and a rebuild after the extractor
// changes. It plans and prints by default; with Apply it writes the pages and
// the entity map through the journal, under the dreaming lock, exactly as the
// night would.
func BuildEntities(cfg *config.Config, opt BuildEntitiesOptions) (EntitiesPlan, MocsPlan, Report, error) {
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
			return EntitiesPlan{}, MocsPlan{}, rep, ErrRefused
		}
		return EntitiesPlan{}, MocsPlan{}, rep, err
	}
	defer lock.Release()
	var contract *rules.Rules
	if cfg.Rules != nil {
		if r, err := cfg.Rules.Get(); err == nil {
			contract = r
		}
	}
	plan, err := planEntitiesFor(cfg, root, contract, now)
	if err != nil {
		return plan, MocsPlan{}, rep, err
	}
	if plan.Skipped != "" {
		// A builder that could not plan leaves the pages and their map alone.
		return plan, MocsPlan{}, rep, nil
	}
	entityMap := PlanEntityMap(root, plan.Pages, now)
	if !opt.Apply {
		return plan, entityMap, rep, nil
	}
	journal, err := OpenJournal(cfg.EngineStateDir)
	if err != nil {
		return plan, entityMap, rep, err
	}
	runID := opt.RunID
	if runID == "" {
		runID = newRunID(now)
	}
	rep.RunID = runID
	if err := journal.Append(Entry{Kind: KindRunStart, RunID: runID, TS: now, Mode: "apply"}); err != nil {
		return plan, entityMap, rep, err
	}
	intents := append(append([]Intent(nil), plan.Intents...), entityMap.Intents...)
	if err := applyAll(journal, root, runID, intents, now, 0, &rep); err != nil {
		return plan, entityMap, rep, err
	}
	rep.Outcome = OutcomeApplied
	err = journal.Append(Entry{Kind: KindRunDone, RunID: runID, TS: now,
		Outcome: fmt.Sprintf("entities: %d page(s), %d removed, %d applied, %d skipped",
			len(plan.Pages), len(plan.Removed), rep.Applied, rep.Skipped)})
	return plan, entityMap, rep, err
}
