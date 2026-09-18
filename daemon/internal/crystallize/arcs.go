package crystallize

import (
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// The arc synthesis.
//
// "When an arc closes — the roadmap item moves to Completed, the version line
// ships, the series ends — the closing session marks it in the project tracker,
// and the phase's next run writes the arc's synthesis at
// `memory/crystallized/<project>-<arc>.md` from the arc's Outcomes"
// (agentm-vault § Projects and tasks).
//
// The mark is a frontmatter field on the project's own tracker,
// `arc_closed:`, holding one arc name or a list of them. A field rather than a
// section, because the closing session is already editing that frontmatter and
// a section would have to be parsed out of prose the operator writes freely.
// Named arcs accumulate: the field is a record of what has closed, not a
// one-shot trigger, so a session that closes a second arc adds a line rather
// than replacing the first and un-marking history.
//
// The bar still applies. An arc is a long recurrence with a name, not an
// exception to the rule — a project whose "arc" is two tasks in one week has
// nothing to synthesise that its Outcomes do not already say, and writing a
// permanent lesson about it anyway is the failure this phase's bar exists to
// prevent.

// ArcField is the tracker frontmatter key the closing session writes.
const ArcField = "arc_closed"

// Arc is one closed arc: a project and the name its closing session gave it.
type Arc struct {
	Project string `json:"project"`
	Name    string `json:"name"`
}

// Stem is where the arc's synthesis lands: `<project>-<arc>`.
func (a Arc) Stem() string {
	p, n := Term(a.Project), Term(a.Name)
	if strings.HasPrefix(n, p+"-") || p == "" {
		return n
	}
	return p + "-" + n
}

// ClosedArcs reads every project tracker's `arc_closed` field.
func ClosedArcs(vault string) []Arc {
	var out []Arc
	seen := map[string]bool{}
	for _, base := range []string{
		filepath.Join(vault, "projects"),
		filepath.Join(vault, "projects", "completed"),
	} {
		entries, err := os.ReadDir(base)
		if err != nil {
			continue
		}
		for _, e := range entries {
			if !e.IsDir() || e.Name() == "completed" ||
				strings.HasPrefix(e.Name(), ".") || strings.HasPrefix(e.Name(), "_") {
				continue
			}
			raw, err := os.ReadFile(filepath.Join(base, e.Name(), "tracker.md"))
			if err != nil {
				continue
			}
			for _, name := range listValues(frontmatter(string(raw))[ArcField]) {
				a := Arc{Project: e.Name(), Name: name}
				if a.Name == "" || seen[a.Stem()] {
					continue
				}
				seen[a.Stem()] = true
				out = append(out, a)
			}
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Stem() < out[j].Stem() })
	return out
}

// listValues reads a frontmatter value that may be one name or a list of them.
func listValues(raw string) []string {
	raw = strings.TrimSpace(raw)
	if raw == "" || raw == "[]" {
		return nil
	}
	var out []string
	for _, v := range strings.Split(strings.Trim(raw, "[]"), ",") {
		if v = unquote(v); v != "" {
			out = append(out, v)
		}
	}
	return out
}

// ArcClusters are the syntheses this run owes, one per closed arc that has none.
//
// An arc's sources are that project's closed-task Outcomes — every one of them,
// because the arc is the project's own story and a synthesis built from the
// three that happened to share a word is a recurrence, which the ordinary path
// already writes.
func ArcClusters(vault string, sources []Source, arcs []Arc) []Cluster {
	byProject := map[string][]Source{}
	for _, s := range sources {
		if s.Kind == KindOutcome && s.Project != "" {
			byProject[s.Project] = append(byProject[s.Project], s)
		}
	}
	var out []Cluster
	for _, a := range arcs {
		c := Cluster{Subject: a.Stem(), Name: a.Stem(), Arc: a.Name,
			Sources: byProject[a.Project]}
		sort.Slice(c.Sources, func(i, j int) bool {
			return c.Sources[i].At.Before(c.Sources[j].At)
		})
		if ok, _ := c.Meets(); ok {
			out = append(out, c)
		}
	}
	return out
}

// arcNearMisses are the marked arcs the bar refused, so a closing session that
// marked one and saw no synthesis can find out why.
func arcNearMisses(sources []Source, arcs []Arc) []Skipped {
	byProject := map[string][]Source{}
	for _, s := range sources {
		if s.Kind == KindOutcome && s.Project != "" {
			byProject[s.Project] = append(byProject[s.Project], s)
		}
	}
	var out []Skipped
	for _, a := range arcs {
		c := Cluster{Subject: a.Stem(), Sources: byProject[a.Project]}
		if ok, why := c.Meets(); !ok {
			out = append(out, Skipped{Subject: a.Stem(),
				Sources: len(c.Sources), Reason: "arc: " + why})
		}
	}
	return out
}

// arcPrompt is the call for a closed arc, which asks a different question from
// the recurrence prompt: not "what recurred" but "what did this arc turn out to
// be about".
func arcPrompt(c Cluster, now time.Time) string {
	var b strings.Builder
	b.WriteString("An arc of work has closed: ")
	b.WriteString(c.Arc)
	b.WriteString(". These are the Outcomes of every task in it, in order.\n\n")
	for i, s := range c.Sources {
		b.WriteString(sourceBlock(i, s))
	}
	b.WriteString(`Answer with one JSON object.

Write the arc's synthesis — what the whole of this turned out to be about, that
none of the individual Outcomes says on its own and that someone would still
want to know after every one of them has been archived:

{"title": "<one line, sentence case>",
 "lesson": "<three to six sentences: what the arc established, and what it
             changes about how the next one is approached>",
 "why": "<one sentence: why this is the arc's lesson and not a summary of it>"}

If the arc's Outcomes do not add up to anything beyond themselves — the work
landed, and that is all there is to say — answer:

{"skip": "<one sentence saying so>"}

A synthesis written here never decays and nothing ages it out, so a summary
dressed as a lesson is worse than no note at all.`)
	return b.String()
}
