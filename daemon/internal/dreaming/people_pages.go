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

// PeopleOptions is what the people half of the builder reads besides the index.
type PeopleOptions struct {
	Table people.Table
}

// sharedWorkRe are the paths that are shared work.
var (
	taskFileRe       = regexp.MustCompile(`^projects/(?:completed/)?[^/]+/(?:completed/)?tasks/[^/]+/(?:plan|progress|tracker)\.md$`)
	projectTrackerRe = regexp.MustCompile(`^projects/(?:completed/)?[^/]+/tracker\.md$`)
	decisionRe       = regexp.MustCompile(`^projects/(?:completed/)?[^/]+/(?:completed/)?decisions/.+\.md$`)
)

// IsSharedWork reports whether a note is a piece of the operator's shared
// work: a task's plan, progress or tracker (open or completed), a `decisions/`
// note, a project tracker, a `calendar/` note or a meeting note.
func IsSharedWork(rel string) bool {
	low := strings.ToLower(rel)
	switch {
	case taskFileRe.MatchString(low), projectTrackerRe.MatchString(low), decisionRe.MatchString(low):
		return true
	case strings.HasPrefix(low, "calendar/") && !strings.HasPrefix(path.Base(low), "_"):
		return true
	case strings.Contains(path.Base(low), "meeting"):
		return true
	}
	return false
}

// planPeople adds the people pages to a plan.
func planPeople(plan *EntitiesPlan, root, vault, memRel string, src EntitySources, opts PeopleOptions,
	projects map[string][]string, minSharedWork int, now time.Time) (map[string]bool, error) {
	wanted := map[string]bool{}
	notes, err := src.NoteRows(context.Background())
	if err != nil {
		return wanted, err
	}
	type noteText struct {
		row   index.NoteRow
		text  string
		named []string
	}
	var texts []noteText
	registry := map[string]bool{}
	for full := range opts.Table.Aliases {
		if name, ok := opts.Table.Canonical(full); ok {
			registry[name] = true
		}
	}
	for _, n := range notes {
		if entitySourceExcluded(n.Path, n.Flags, memRel) {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(vault, filepath.FromSlash(n.Path)))
		if err != nil {
			continue
		}
		_, body := ParseFrontmatter(string(raw))
		var named []string
		for _, v := range peopleField(string(raw)) {
			if name, ok := opts.Table.Canonical(v); ok {
				named = append(named, name)
				registry[name] = true
			}
		}
		texts = append(texts, noteText{row: n, text: displayTitle(n.Path, n.Title) + "\n" + body, named: named})
	}

	var names []string
	for name := range registry {
		names = append(names, name)
	}
	sort.Strings(names)
	today := now.UTC().Format("2006-01-02")
	for _, name := range names {
		spellings := opts.Table.Spellings(name)
		set := map[string]mention{}
		shared := 0
		for _, t := range texts {
			if !contains(t.named, name) && !people.Mentions(t.text, spellings) {
				continue
			}
			m := mentionOf(t.row, projects)
			m.Shared = IsSharedWork(t.row.Path)
			if m.Shared {
				shared++
			}
			set[t.row.Path] = m
		}
		if shared < minSharedWork {
			continue
		}
		ms := sortedMentions(set)
		p := entityPage{kind: "person", title: name, slug: cardshape.EntitySlug(name), sharedWork: shared}
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
		wanted[rel] = true
		before, created := mocCurrentPage(root, relUnderRoot(rel, memRel))
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
