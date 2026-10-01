package dreaming

import (
	"context"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode"

	"github.com/alexherrero/agentm/daemon/internal/cardshape"
	"github.com/alexherrero/agentm/daemon/internal/index"
	"github.com/alexherrero/agentm/daemon/internal/people"
)

// People pages (task 179 step 6, agentm-vault § Dreaming).
//
// Enrichment says who the people are: its deep pass writes `people:`, the
// people a note names, grounded in the note's words and filed under the
// operator's table in `standards/people/aliases.md`, which may also name a
// person outright. It never reads a tracker, a plan, a progress log or a
// calendar note, so this finds where each person appears by matching their
// names across every note the index holds. A person gets a page once named in
// `person_min_shared_work` distinct pieces of shared work; mentions anywhere
// else are listed on the page but never qualify a person on their own.

// EmailThread is one mail thread with the operator, from an email source.
type EmailThread struct {
	// ID names the thread; a thread counts once per person however many
	// messages it holds.
	ID string
	// Participants are the people on the thread, as the source names them.
	// The operator's own names are filed out by the table like any other.
	Participants []string
}

// EmailEvidence is where shared-work evidence from mail comes from (task 179
// step 7). No source ships and nothing reads mail: a future ingest implements
// this, and a thread with the operator then counts as one piece of shared
// work for each person on it — only while the operator's switch,
// `daemon.people_email_evidence_enabled`, is on.
type EmailEvidence interface {
	Threads(ctx context.Context) ([]EmailThread, error)
}

// PeopleOptions is what the people half of the builder reads besides the index.
type PeopleOptions struct {
	Table people.Table
	// Email is the email source, and EmailEnabled the operator's switch. Off,
	// a source is never asked.
	Email        EmailEvidence
	EmailEnabled bool
}

// sharedWorkRe are the paths that are shared work.
var (
	taskFileRe       = regexp.MustCompile(`^projects/(?:completed/)?[^/]+/(?:completed/)?tasks/[^/]+/(?:plan|progress|tracker)\.md$`)
	projectTrackerRe = regexp.MustCompile(`^projects/(?:completed/)?[^/]+/tracker\.md$`)
	decisionRe       = regexp.MustCompile(`^projects/(?:completed/)?[^/]+/(?:completed/)?decisions/.+\.md$`)
)

// IsSharedWork reports whether a note is a piece of the operator's shared
// work: a task's plan, progress or tracker (open or completed), a `decisions/`
// note, a project tracker, a `calendar/` note, or a note under `personal/` or
// `projects/` whose name says it is a meeting's.
func IsSharedWork(rel string) bool {
	low := strings.ToLower(rel)
	switch {
	case taskFileRe.MatchString(low), projectTrackerRe.MatchString(low), decisionRe.MatchString(low):
		return true
	case strings.HasPrefix(low, "calendar/") && !strings.HasPrefix(path.Base(low), "_"):
		return true
	case strings.Contains(path.Base(low), "meeting") &&
		(strings.HasPrefix(low, "personal/") || strings.HasPrefix(low, "projects/")):
		// A meeting's own notes, kept with your life or your work; a memory card
		// about meetings in general is not one.
		return true
	}
	return false
}

// personGroup is every spelling the vault gives one person: the names whose
// page would sit at one slug, "Jean-Luc Picard" and "Jean Luc Picard" alike,
// with how often each was written. One page is built per group.
type personGroup struct {
	counts map[string]int
}

func (g *personGroup) add(name string, weight int) {
	if g.counts == nil {
		g.counts = map[string]int{}
	}
	g.counts[name] += weight
}

// ordered is the group's names, best first: the one written most often, then
// the one with more capitals ("Jane Doe" over "jane doe"), then in order, so
// two builds over one corpus agree on everything derived from the names.
func (g *personGroup) ordered() []string {
	names := make([]string, 0, len(g.counts))
	for n := range g.counts {
		names = append(names, n)
	}
	upper := func(s string) int {
		n := 0
		for _, r := range s {
			if unicode.IsUpper(r) {
				n++
			}
		}
		return n
	}
	sort.Slice(names, func(i, j int) bool {
		a, b := names[i], names[j]
		if g.counts[a] != g.counts[b] {
			return g.counts[a] > g.counts[b]
		}
		if upper(a) != upper(b) {
			return upper(a) > upper(b)
		}
		return a < b
	})
	return names
}

// title is the spelling the page is titled with: the best of ordered.
func (g *personGroup) title() string { return g.ordered()[0] }

// spellings are every spelling the group's names answer to, the table's
// aliases included; of two that differ only in case, the better name's wins.
func (g *personGroup) spellings(t people.Table) []string {
	seen := map[string]bool{}
	var out []string
	for _, n := range g.ordered() {
		for _, s := range t.Spellings(n) {
			if k := strings.ToLower(s); !seen[k] {
				seen[k] = true
				out = append(out, s)
			}
		}
	}
	sort.SliceStable(out, func(i, j int) bool {
		if len(out[i]) != len(out[j]) {
			return len(out[i]) > len(out[j])
		}
		return out[i] < out[j]
	})
	return out
}

// planPeople adds the people pages to a plan.
//
// A note the index holds and the builder cannot read tonight — a Drive
// placeholder, a permission blip — would make the people it names look short
// of shared work. So when any note cannot be read, the people half stands
// still for the night: every person page on disk is kept as it is, none is
// written, and the plan says which notes could not be read. A note that has
// gone since the index last saw it is simply gone.
func planPeople(plan *EntitiesPlan, root, vault, memRel string, src EntitySources, opts PeopleOptions,
	projects map[string][]string, minSharedWork int, now time.Time) (map[string]bool, error) {
	wanted := map[string]bool{}
	notes, err := src.NoteRows(context.Background())
	if err != nil {
		return wanted, err
	}
	type noteText struct {
		row   index.NoteRow
		text  people.Text
		named map[string]bool // the slugs of the people its `people:` names
	}
	var texts []noteText
	groups := map[string]*personGroup{}
	register := func(name string, weight int) string {
		slug := cardshape.EntitySlug(name)
		if groups[slug] == nil {
			groups[slug] = &personGroup{}
		}
		groups[slug].add(name, weight)
		return slug
	}
	for full := range opts.Table.Aliases {
		if name, ok := opts.Table.Canonical(full); ok {
			// The table's own full name titles its person's page.
			register(name, 1000)
		}
	}
	var unreadable []string
	for _, n := range notes {
		if entitySourceExcluded(n.Path, n.Flags, memRel) {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(vault, filepath.FromSlash(n.Path)))
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			unreadable = append(unreadable, n.Path)
			continue
		}
		_, body := ParseFrontmatter(string(raw))
		named := map[string]bool{}
		for _, v := range peopleField(string(raw)) {
			if name, ok := opts.Table.Canonical(v); ok {
				named[register(name, 1)] = true
			}
		}
		texts = append(texts, noteText{row: n, text: people.NewText(displayTitle(n.Path, n.Title) + "\n" + body), named: named})
	}

	threads := map[string]map[string]bool{}
	if opts.EmailEnabled && opts.Email != nil {
		ts, err := opts.Email.Threads(context.Background())
		if err != nil {
			return wanted, err
		}
		for _, t := range ts {
			for _, p := range t.Participants {
				if name, ok := opts.Table.Canonical(p); ok {
					slug := register(name, 1)
					if threads[slug] == nil {
						threads[slug] = map[string]bool{}
					}
					threads[slug][t.ID] = true
				}
			}
		}
	}

	if len(unreadable) > 0 {
		sort.Strings(unreadable)
		plan.PeopleHeld = unreadable
		for _, rel := range existingPages(root, memRel, "person") {
			wanted[rel] = true
		}
		return wanted, nil
	}

	slugs := make([]string, 0, len(groups))
	for slug := range groups {
		slugs = append(slugs, slug)
	}
	sort.Strings(slugs)
	today := now.UTC().Format("2006-01-02")
	for _, slug := range slugs {
		g := groups[slug]
		name := g.title()
		spellings := g.spellings(opts.Table)
		set := map[string]mention{}
		shared := 0
		for _, t := range texts {
			if !t.named[slug] && !t.text.Mentions(spellings) {
				continue
			}
			m := mentionOf(t.row, projects)
			m.Shared = IsSharedWork(t.row.Path)
			if m.Shared {
				shared++
			}
			set[t.row.Path] = m
		}
		shared += len(threads[slug])
		if shared < minSharedWork {
			continue
		}
		ms := sortedMentions(set)
		p := entityPage{kind: "person", title: name, slug: slug, sharedWork: shared}
		p.id = p.slug
		p.uri = "person:" + p.slug
		for _, s := range spellings {
			if s != name {
				p.aliases = append(p.aliases, s)
			}
		}
		sort.Strings(p.aliases)
		p.projects, p.first, p.last = mentionSpan(ms)
		rel := entityRel(memRel, "person", p.slug)
		before, created := mocCurrentPage(root, relUnderRoot(rel, memRel))
		if handWritten(before) {
			plan.Held = append(plan.Held, rel)
			continue
		}
		wanted[rel] = true
		if created == "" {
			created = today
		}
		plan.add(EntityPage{Type: "person", ID: p.uri, Rel: rel, Title: name, Mentions: len(ms), Last: p.last,
			SharedWork: shared},
			before, renderEntityPage(p, ms, created), relUnderRoot(rel, memRel))
		plan.Counts["person"]++
	}
	return wanted, nil
}

// peopleField reads a note's `people:` list, in either shape a writer leaves
// it: a flow list `[a, "b c"]`, or a block of `- name` lines under the key.
func peopleField(raw string) []string {
	if !strings.HasPrefix(raw, "---\n") {
		return nil
	}
	end := strings.Index(raw[4:], "\n---")
	if end < 0 {
		return nil
	}
	lines := strings.Split(raw[4:4+end], "\n")
	var parts []string
	for i, line := range lines {
		key, value, ok := strings.Cut(line, ":")
		if !ok || strings.TrimSpace(key) != "people" || strings.HasPrefix(line, " ") {
			continue
		}
		value = strings.TrimSpace(value)
		if strings.HasPrefix(value, "[") && strings.HasSuffix(value, "]") {
			parts = strings.Split(value[1:len(value)-1], ",")
		} else if value == "" {
			for _, item := range lines[i+1:] {
				t := strings.TrimSpace(item)
				if !strings.HasPrefix(t, "- ") {
					break
				}
				parts = append(parts, strings.TrimPrefix(t, "- "))
			}
		} else {
			parts = []string{value}
		}
		break
	}
	var out []string
	for _, p := range parts {
		if p = strings.Trim(strings.TrimSpace(p), `"'`); p != "" {
			out = append(out, p)
		}
	}
	return out
}

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}
