// Package crystallize writes the lesson a repetition taught.
//
// A crystallized note is a hard-learned lesson: something that proved itself
// over time, across more than one task or more than one card, that the
// operator would keep after every card it came from has aged out. A session
// closing a task cannot see that — it has the task in context but not the time
// axis — and a nightly pass over one card cannot either. So this runs weekly,
// inside the night's window and against its budget line (agentm-vault
// § Projects and tasks, "Crystallize is a weekly phase of dreaming, and a
// session never writes one").
//
// # Where the bar lives and where the model lives
//
// The two are deliberately apart. *Counting* a recurrence is arithmetic — at
// least three sources, across at least two sessions, at least seven days apart,
// the operator's bar — and it is done here, in code a test can hold to it.
// *Recognising* what the recurrence taught is a judgment, and it is one model
// call per cluster that cleared the bar, on the strong tier the tier table pins
// (`tiers.Crystallize`). A cluster that does not clear the bar never reaches a
// model, so the cheapest run of this phase is the one that finds nothing and
// spends nothing.
//
// The model may also refuse. A term three notes happen to share is not a
// lesson, and a phase that wrote one per cluster would fill the decay-exempt
// layer with vocabulary. The prompt says so and the answer has a `skip` shape.
package crystallize

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode"
)

// The operator's recurrence bar, written here rather than configured.
//
// A bar that can be lowered is a bar that gets lowered on the night it would
// have refused. The design's re-audit is on the numbers being too *high* — "if
// the phase wrote nothing, the bar is too high for a task-a-week cadence" —
// and that re-audit is a person changing these constants with the reason in
// the commit, not a config key nobody reads.
const (
	// MinSources is how many Outcomes or cards must name the same thing.
	MinSources = 3
	// MinSessions is how many distinct sessions those sources must span. Three
	// mentions in one sitting is one thought, said three times.
	MinSessions = 2
	// MinSpanDays is how far apart the first and last must be. A lesson is what
	// survived a week, not what was on the operator's mind for an afternoon.
	MinSpanDays = 7
)

// Dir is where lessons land, under the memory root.
const Dir = "memory/crystallized"

// Kinds of source, named so the lesson's `consolidated_from` and the run
// record say what taught it.
const (
	KindOutcome   = "outcome"
	KindCard      = "card"
	KindCandidate = "candidate"
)

// Source is one thing the phase read: a closed task's Outcome, a memory card,
// or a candidate line a session left in its trace.
type Source struct {
	// Rel is the path under the vault root — one base for every kind, so a
	// link built from it resolves and a reader comparing two sources is
	// comparing the same thing.
	Rel string `json:"rel"`
	// Path is the file on disk. Carried rather than re-derived, because the
	// bases differ by kind at read time — a card comes from the memory root and
	// a tracker from the vault root — and a writer that re-joined the wrong one
	// would silently fail to write and look like a rule that had held.
	Path string `json:"-"`
	// Kind is which of the three it is.
	Kind string `json:"kind"`
	// Project is the project slug when the source has one.
	Project string `json:"project,omitempty"`
	// Session is what makes two sources the same sitting: a task's directory,
	// a card's `source_session`, a trace's own file. Sources with the same
	// Session count once towards MinSessions.
	Session string `json:"session"`
	// At is when it was written, for the span.
	At time.Time `json:"at"`
	// Title is what the note calls itself, for the prompt and the note link.
	Title string `json:"title,omitempty"`
	// Text is the excerpt the model reads.
	Text string `json:"text"`
	// Terms are the subject terms this source carries.
	Terms []string `json:"terms,omitempty"`
}

// repeatedNames are the file names every project and every task carries. A
// wikilink to one of them by stem names seventy files at once.
var repeatedNames = map[string]bool{
	"tracker": true, "plan": true, "progress": true,
	"charter": true, "followups": true, "_index": true,
}

// Link is how a source is named in `consolidated_from` — the inside of the
// wikilink, brackets excluded.
//
// A note's stem, because that is how Obsidian resolves a link and how it keeps
// resolving after the note moves. Except when the stem is one every project
// carries: `[[tracker]]` names seventy files and opens whichever one Obsidian
// happens to rank first. Those link by path with the directory as the alias, so
// the link resolves to one file and reads as the thing it came from.
func (s Source) Link() string {
	if s.Rel == "" {
		return s.Session
	}
	stem := strings.TrimSuffix(filepath.Base(s.Rel), ".md")
	if !repeatedNames[stem] {
		return stem
	}
	dir := filepath.Base(filepath.Dir(s.Rel))
	return strings.TrimSuffix(s.Rel, ".md") + "|" + dir
}

// Cluster is one recurrence: every source that named the same subject.
type Cluster struct {
	// Subject is the term they share, already normalised.
	Subject string `json:"subject"`
	// Name, when set, is the filename stem this cluster must be written under,
	// overriding whatever the model proposes. An arc's synthesis lands at
	// `<project>-<arc>` because that is where the closing session will look
	// for it, and letting a model rename it would leave the operator hunting.
	Name string `json:"name,omitempty"`
	// Arc, when set, is the closed arc this cluster synthesises. It changes
	// the question the prompt asks.
	Arc     string   `json:"arc,omitempty"`
	Sources []Source `json:"sources"`
}

// Sessions is how many distinct sittings the cluster spans.
func (c Cluster) Sessions() int {
	seen := map[string]bool{}
	for _, s := range c.Sources {
		seen[s.Session] = true
	}
	return len(seen)
}

// SpanDays is the days between the earliest and latest source.
func (c Cluster) SpanDays() float64 {
	if len(c.Sources) == 0 {
		return 0
	}
	first, last := c.Sources[0].At, c.Sources[0].At
	for _, s := range c.Sources[1:] {
		if s.At.Before(first) {
			first = s.At
		}
		if s.At.After(last) {
			last = s.At
		}
	}
	return last.Sub(first).Hours() / 24
}

// Meets reports whether this cluster clears the operator's bar, and when it
// does not, which leg of the bar it missed.
//
// The reason is always populated, including on the happy path's negative
// branches, because the design's re-audit is "if the phase wrote nothing, the
// bar is too high" — and answering that needs to know whether the near misses
// were short of sources, short of sessions or short of days.
func (c Cluster) Meets() (bool, string) {
	if n := len(c.Sources); n < MinSources {
		return false, fmt.Sprintf("%d source(s), and the bar is %d", n, MinSources)
	}
	if n := c.Sessions(); n < MinSessions {
		return false, fmt.Sprintf("%d source(s) but all within %d session(s), "+
			"and the bar is %d", len(c.Sources), n, MinSessions)
	}
	if d := c.SpanDays(); d < MinSpanDays {
		return false, fmt.Sprintf("%d source(s) across %.0f day(s), and the bar "+
			"is %d", len(c.Sources), d, MinSpanDays)
	}
	return true, ""
}

// Projects is the project every source shares, or "" when they do not share
// one. A lesson carries `project:` only when it is one project's lesson.
func (c Cluster) Project() string {
	p := ""
	for _, s := range c.Sources {
		if s.Project == "" {
			return ""
		}
		if p == "" {
			p = s.Project
		} else if p != s.Project {
			return ""
		}
	}
	return p
}

// --- subject terms ----------------------------------------------------------

var (
	tickRe = regexp.MustCompile("`([^`\n]+)`")
	wikiRe = regexp.MustCompile(`\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]`)
)

// Term is a subject term as the clusterer compares them: lower case, letters
// and digits only, runs of anything else folded to one hyphen.
//
// Deliberately coarse. `agentmd enrich` and `agentmd  --enrich` should not be
// two subjects, and a term that survives this fold is one a person would still
// recognise in a filename.
func Term(s string) string {
	var b strings.Builder
	hyphen := false
	for _, r := range strings.ToLower(s) {
		switch {
		case unicode.IsLetter(r) || unicode.IsDigit(r):
			b.WriteRune(r)
			hyphen = false
		case !hyphen && b.Len() > 0:
			b.WriteByte('-')
			hyphen = true
		}
	}
	return strings.Trim(b.String(), "-")
}

// stopTerms never make a subject. They are the vocabulary of the vault itself
// — every note names them and none of them is a lesson.
var stopTerms = map[string]bool{
	"": true, "todo": true, "note": true, "notes": true, "the": true,
	"memory": true, "vault": true, "agent": true, "card": true, "cards": true,
	"tracker": true, "plan": true, "progress": true, "task": true, "tasks": true,
	"project": true, "projects": true, "outcome": true, "status": true,
	"done": true, "wip": true, "n-a": true, "tbd": true,
}

// termsOf is the subject vocabulary of one piece of text plus its tags: the
// tags as written, every backticked span, and every wikilink target.
//
// Backticks and wikilinks rather than the prose, because those are where this
// operator's notes name a mechanism. Prose words would cluster on "because"
// and "however", and a stoplist long enough to stop that is a stoplist nobody
// can audit.
func termsOf(tags []string, text string) []string {
	seen := map[string]bool{}
	var out []string
	add := func(raw string) {
		t := Term(raw)
		if stopTerms[t] || len(t) < 3 || seen[t] {
			return
		}
		seen[t] = true
		out = append(out, t)
	}
	for _, tag := range tags {
		add(tag)
	}
	for _, m := range tickRe.FindAllStringSubmatch(text, -1) {
		add(m[1])
	}
	for _, m := range wikiRe.FindAllStringSubmatch(text, -1) {
		add(m[1])
	}
	sort.Strings(out)
	return out
}

// Clusters groups sources by the terms they share and keeps the ones that
// clear the bar.
//
// Overlapping clusters are pruned: a cluster whose sources are all in another,
// larger cluster says the same thing twice, and two lessons from one recurrence
// is exactly the noise the decay-exempt layer must not collect. Ties are broken
// by subject so a night's output does not depend on map order.
func Clusters(sources []Source) []Cluster {
	byTerm := map[string][]Source{}
	for _, s := range sources {
		for _, t := range s.Terms {
			byTerm[t] = append(byTerm[t], s)
		}
	}
	terms := make([]string, 0, len(byTerm))
	for t := range byTerm {
		terms = append(terms, t)
	}
	sort.Strings(terms)

	var kept []Cluster
	for _, t := range terms {
		c := Cluster{Subject: t, Sources: byTerm[t]}
		sort.Slice(c.Sources, func(i, j int) bool {
			if !c.Sources[i].At.Equal(c.Sources[j].At) {
				return c.Sources[i].At.Before(c.Sources[j].At)
			}
			return c.Sources[i].Rel < c.Sources[j].Rel
		})
		if ok, _ := c.Meets(); ok {
			kept = append(kept, c)
		}
	}
	return prune(kept)
}

// prune drops a cluster whose source set is contained in another's. The larger
// cluster is the recurrence; the smaller is a word inside it.
func prune(in []Cluster) []Cluster {
	keys := make([]map[string]bool, len(in))
	for i, c := range in {
		k := map[string]bool{}
		for _, s := range c.Sources {
			k[s.Kind+"|"+s.Rel+"|"+s.Session+"|"+s.Text] = true
		}
		keys[i] = k
	}
	var out []Cluster
	for i := range in {
		dropped := false
		for j := range in {
			if i == j {
				continue
			}
			if len(keys[i]) > len(keys[j]) {
				continue
			}
			// Equal sets: keep the first by subject, drop the rest.
			if len(keys[i]) == len(keys[j]) && in[i].Subject < in[j].Subject {
				continue
			}
			contained := true
			for k := range keys[i] {
				if !keys[j][k] {
					contained = false
					break
				}
			}
			if contained {
				dropped = true
				break
			}
		}
		if !dropped {
			out = append(out, in[i])
		}
	}
	return out
}

// --- what a run found -------------------------------------------------------

// Lesson is the model's answer for one cluster.
type Lesson struct {
	// Subject is the lesson's filename stem. The model may narrow the
	// clusterer's term to something a person would name the file.
	Subject string `json:"subject"`
	Title   string `json:"title"`
	// Lesson is the body: what was learned, in the operator's own register.
	Lesson string `json:"lesson"`
	// Why is why this is a lesson rather than a coincidence, written from the
	// recurrence the phase found.
	Why string `json:"why"`
	// Skip, when set, is the model declining: the recurrence is real and the
	// lesson is not. Nothing is written and the reason is reported.
	Skip string `json:"skip,omitempty"`
}

// Written is one lesson as the run record and the morning note read it.
type Written struct {
	Rel     string   `json:"rel"`
	Subject string   `json:"subject"`
	Title   string   `json:"title"`
	Why     string   `json:"why"`
	Sources []string `json:"consolidated_from"`
	// Stamped are the source notes that gained `consolidated_into`. A candidate
	// line has no note to stamp, so this is never simply len(Sources).
	Stamped []string `json:"stamped,omitempty"`
}

// Skipped is a cluster that reached a model and did not become a lesson.
type Skipped struct {
	Subject string `json:"subject"`
	Sources int    `json:"sources"`
	Reason  string `json:"reason"`
}

// Report is what one run did.
type Report struct {
	At         time.Time `json:"at"`
	Sources    int       `json:"sources"`
	Clusters   int       `json:"clusters"`
	Considered int       `json:"considered"`
	Lessons    []Written `json:"lessons,omitempty"`
	Skipped    []Skipped `json:"skipped,omitempty"`
	// NearMisses are the clusters that did not reach a model, with the leg of
	// the bar each missed. The cadence re-audit reads these.
	NearMisses []Skipped `json:"near_misses,omitempty"`
	Errors     []string  `json:"errors,omitempty"`
	DryRun     bool      `json:"dry_run,omitempty"`
}

// --- the prompt -------------------------------------------------------------

// SystemPrompt frames the call. It names the one failure this phase can cause
// that nothing downstream catches.
const SystemPrompt = "You write one hard-learned lesson from several notes " +
	"that turned out to be about the same thing. A lesson is what someone " +
	"would still want to know after every note that taught it has been " +
	"deleted. Answer with one JSON object and nothing else."

// Prompt is the call for one cluster.
func Prompt(c Cluster) string {
	var b strings.Builder
	fmt.Fprintf(&b, "These %d notes all name %q. They were written across %d "+
		"sessions, %.0f days apart.\n\n", len(c.Sources), c.Subject,
		c.Sessions(), c.SpanDays())
	for i, s := range c.Sources {
		b.WriteString(sourceBlock(i, s))
	}
	b.WriteString(`Answer with one JSON object.

If these sources genuinely taught one lesson — the same mechanism, failure or
method, not merely the same word — answer:

{"subject": "<kebab-case filename stem>", "title": "<one line, sentence case>",
 "lesson": "<two to five sentences: what is true, and what to do about it>",
 "why": "<one sentence: why this is a lesson and not a coincidence>"}

If they share a word and not a lesson, or if what they share is too thin to be
worth keeping forever, answer:

{"skip": "<one sentence saying what they actually share>"}

A lesson written here never decays and nothing ages it out, so skipping is the
safe answer and it is the right one more often than not.`)
	return b.String()
}

// sourceBlock is one source as the prompt shows it.
func sourceBlock(i int, s Source) string {
	var b strings.Builder
	fmt.Fprintf(&b, "--- source %d (%s, %s", i+1, s.Kind, s.At.Format("2006-01-02"))
	if s.Title != "" {
		fmt.Fprintf(&b, ", %q", s.Title)
	}
	b.WriteString(")\n")
	b.WriteString(strings.TrimSpace(s.Text))
	b.WriteString("\n\n")
	return b.String()
}

// ParseLesson reads the model's answer.
//
// A fenced block is tolerated because a model that has been told "JSON and
// nothing else" still fences it perhaps one time in twenty, and the alternative
// is throwing away a good lesson over punctuation. Anything else is an error:
// an answer that cannot be read is not a lesson with a missing field, it is a
// call that did something else.
func ParseLesson(out string) (Lesson, error) {
	s := strings.TrimSpace(out)
	if strings.HasPrefix(s, "```") {
		if i := strings.Index(s, "\n"); i >= 0 {
			s = s[i+1:]
		}
		if i := strings.LastIndex(s, "```"); i >= 0 {
			s = s[:i]
		}
		s = strings.TrimSpace(s)
	}
	var l Lesson
	if err := json.Unmarshal([]byte(s), &l); err != nil {
		return Lesson{}, fmt.Errorf("crystallize: the call did not answer with a "+
			"JSON object: %q", truncate(s, 200))
	}
	if l.Skip != "" {
		return l, nil
	}
	if strings.TrimSpace(l.Lesson) == "" || strings.TrimSpace(l.Title) == "" {
		return Lesson{}, fmt.Errorf("crystallize: the answer carried neither a " +
			"lesson nor a skip")
	}
	return l, nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

// --- what gets written ------------------------------------------------------

// Stem is the lesson's filename stem: the model's subject where it gave a
// usable one, else the cluster's term, with the project in front when the
// lesson belongs to one project.
func Stem(l Lesson, c Cluster) string {
	if c.Name != "" {
		return c.Name
	}
	stem := Term(l.Subject)
	if stem == "" {
		stem = c.Subject
	}
	if p := c.Project(); p != "" && !strings.HasPrefix(stem, Term(p)+"-") {
		stem = Term(p) + "-" + stem
	}
	return stem
}

// Render is the lesson as a card, in the field order the card shape locks.
func Render(l Lesson, c Cluster, today string) string {
	var b strings.Builder
	b.WriteString("---\n")
	fmt.Fprintf(&b, "title: %s\n", yamlQuote(strings.TrimSpace(l.Title)))
	b.WriteString("type: insight\n")
	fmt.Fprintf(&b, "why: %s\n", yamlQuote(strings.TrimSpace(l.Why)))
	// A lesson is permanent learning: it never decays or sinks, and changes
	// only when a later lesson supersedes it or the operator edits it.
	b.WriteString("status: active\nlifecycle: pinned\n")
	fmt.Fprintf(&b, "lifecycle_since: %s\n", today)
	b.WriteString("filing_confidence: 1.0\n")
	b.WriteString("source: crystallize\ntrust: derived\n")
	fmt.Fprintf(&b, "created: %s\nupdated: %s\n", today, today)
	fmt.Fprintf(&b, "tags: [%s]\n", c.Subject)
	b.WriteString("consolidated_from:\n")
	for _, s := range c.Sources {
		fmt.Fprintf(&b, "  - \"[[%s]]\"\n", s.Link())
	}
	if p := c.Project(); p != "" {
		fmt.Fprintf(&b, "project: %s\n", p)
	}
	fmt.Fprintf(&b, "slug: %s\n", Stem(l, c))
	b.WriteString("---\n\n")
	b.WriteString(strings.TrimSpace(l.Lesson))
	b.WriteString("\n\n## What taught it\n\n")
	for _, s := range c.Sources {
		fmt.Fprintf(&b, "- [[%s]] — %s, %s\n", s.Link(), s.Kind, s.At.Format("2006-01-02"))
	}
	return b.String()
}

func yamlQuote(s string) string {
	s = strings.ReplaceAll(s, "\n", " ")
	if s == "" {
		return `""`
	}
	if strings.ContainsAny(s, `:#"'[]{},&*?|<>=!%@`) || strings.HasPrefix(s, " ") {
		return `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`).Replace(s) + `"`
	}
	return s
}

var consolidatedIntoRe = regexp.MustCompile(`(?m)^consolidated_into:[ \t]*.*$`)

// Stamp adds `consolidated_into` to a source note, or replaces the one it
// carries. The stamp is what drops the source to ×0.30 in both ranking arms:
// once a lesson lands, the things that taught it must not crowd it out.
//
// Placed after `superseded_by` when the note has one and before `project`
// otherwise, which is where the card shape's read order puts it. A note with
// neither gets it at the end of the frontmatter, which the shape tolerates and
// the next pass's reorder settles.
func Stamp(text, lessonStem string) string {
	line := fmt.Sprintf("consolidated_into: \"[[%s]]\"", lessonStem)
	if consolidatedIntoRe.MatchString(text) {
		return consolidatedIntoRe.ReplaceAllString(text, line)
	}
	if !strings.HasPrefix(text, "---\n") {
		return text
	}
	end := strings.Index(text[4:], "\n---")
	if end < 0 {
		return text
	}
	end += 4
	fm, rest := text[4:end], text[end:]
	lines := strings.Split(fm, "\n")
	at := len(lines)
	for i, l := range lines {
		if strings.HasPrefix(l, "project:") || strings.HasPrefix(l, "task:") ||
			strings.HasPrefix(l, "slug:") {
			at = i
			break
		}
	}
	// Trailing empties before the closing fence are not a place for a key.
	for at > 0 && strings.TrimSpace(lines[at-1]) == "" {
		at--
	}
	out := append([]string{}, lines[:at]...)
	out = append(out, line)
	out = append(out, lines[at:]...)
	return "---\n" + strings.Join(out, "\n") + rest
}

// --- the run ----------------------------------------------------------------

// Caller is the one model call this phase makes per cluster.
type Caller func(prompt string) (string, error)

// Options bound a run.
type Options struct {
	// Root is the memory root — the directory holding `memory/`.
	Root string
	// Vault is the vault root, which is where sources outside the memory root
	// (a project's trackers, a trace) are read from.
	Vault string
	Now   time.Time
	// Topic, when set, narrows the run to clusters whose subject contains it:
	// `/memory crystallize <topic>` runs the same phase on request.
	Topic string
	// Cap bounds the lessons one run writes. A recurrence storm should cost a
	// page, not a class folder. Zero means the default.
	Cap int
	// DryRun plans and never writes, and never calls a model: a dry run that
	// spent money would be the one thing a dry run must not do.
	DryRun bool
}

// DefaultCap is the most lessons one run writes.
const DefaultCap = 5

// Run finds the recurrences, asks for a lesson about each, and writes them.
func Run(opt Options, call Caller) (Report, error) {
	if opt.Cap <= 0 {
		opt.Cap = DefaultCap
	}
	rep := Report{At: opt.Now.UTC(), DryRun: opt.DryRun}

	sources, err := Gather(opt.Root, opt.Vault, opt.Now)
	if err != nil {
		return rep, err
	}
	rep.Sources = len(sources)

	// Near misses first, from the same grouping the clusterer uses, so the
	// re-audit can see what the bar refused.
	rep.NearMisses = nearMisses(sources)

	// The arcs a closing session has marked, first: an arc's synthesis is owed
	// to a specific name, and a recurrence that would have taken that name
	// should be the one that yields.
	arcs := ClosedArcs(opt.Vault)
	rep.NearMisses = append(rep.NearMisses, arcNearMisses(sources, arcs)...)
	clusters := append(ArcClusters(opt.Vault, sources, arcs), Clusters(sources)...)
	if opt.Topic != "" {
		t := Term(opt.Topic)
		var narrowed []Cluster
		for _, c := range clusters {
			if strings.Contains(c.Subject, t) || strings.Contains(t, c.Subject) {
				narrowed = append(narrowed, c)
			}
		}
		clusters = narrowed
	}
	rep.Clusters = len(clusters)

	existing := existingLessons(opt.Root)
	for _, c := range clusters {
		if len(rep.Lessons) >= opt.Cap {
			break
		}
		if existing[c.Subject] {
			rep.Skipped = append(rep.Skipped, Skipped{Subject: c.Subject,
				Sources: len(c.Sources), Reason: "a lesson on this subject exists"})
			continue
		}
		rep.Considered++
		if opt.DryRun {
			rep.Skipped = append(rep.Skipped, Skipped{Subject: c.Subject,
				Sources: len(c.Sources), Reason: "dry run: no call made"})
			continue
		}
		prompt := Prompt(c)
		if c.Arc != "" {
			prompt = arcPrompt(c, opt.Now)
		}
		out, err := call(prompt)
		if err != nil {
			rep.Errors = append(rep.Errors, fmt.Sprintf("%s: %v", c.Subject, err))
			continue
		}
		l, err := ParseLesson(out)
		if err != nil {
			rep.Errors = append(rep.Errors, fmt.Sprintf("%s: %v", c.Subject, err))
			continue
		}
		if l.Skip != "" {
			rep.Skipped = append(rep.Skipped, Skipped{Subject: c.Subject,
				Sources: len(c.Sources), Reason: l.Skip})
			continue
		}
		w, err := write(opt, l, c)
		if err != nil {
			rep.Errors = append(rep.Errors, fmt.Sprintf("%s: %v", c.Subject, err))
			continue
		}
		existing[c.Subject] = true
		rep.Lessons = append(rep.Lessons, w)
	}
	return rep, nil
}

// write lands the lesson and stamps its sources.
//
// The lesson first. A source stamped `consolidated_into` pointing at a note
// that does not exist is a dangling link in the operator's vault and a ×0.30
// dampen with nothing to show for it; a lesson whose sources are unstamped is
// merely a lesson that outranks them by less than it should, and the next run
// stamps them.
func write(opt Options, l Lesson, c Cluster) (Written, error) {
	stem := Stem(l, c)
	rel := filepath.Join(Dir, stem+".md")
	p := filepath.Join(opt.Root, rel)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return Written{}, err
	}
	today := opt.Now.Format("2006-01-02")
	if err := os.WriteFile(p, []byte(Render(l, c, today)), 0o644); err != nil {
		return Written{}, err
	}
	w := Written{Rel: rel, Subject: c.Subject, Title: l.Title, Why: l.Why}
	for _, s := range c.Sources {
		w.Sources = append(w.Sources, s.Link())
		// Only cards are stamped. A tracker is the operator's living head and
		// a trace is one session's record; neither is a thing a lesson demotes,
		// and both are read by passes that would have to learn a field they
		// have no use for.
		if s.Kind != KindCard || s.Path == "" {
			continue
		}
		sp := s.Path
		raw, err := os.ReadFile(sp)
		if err != nil {
			continue
		}
		stamped := Stamp(string(raw), stem)
		if stamped == string(raw) {
			continue
		}
		if err := os.WriteFile(sp, []byte(stamped), 0o644); err != nil {
			continue
		}
		w.Stamped = append(w.Stamped, s.Rel)
	}
	return w, nil
}

// existingLessons is the subjects already crystallized, by tag. A lesson is
// written once; a later run that found the same recurrence adds nothing and
// must not overwrite what a person may have edited.
func existingLessons(root string) map[string]bool {
	out := map[string]bool{}
	dir := filepath.Join(root, Dir)
	entries, err := os.ReadDir(dir)
	if err != nil {
		return out
	}
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".md") {
			continue
		}
		out[Term(strings.TrimSuffix(e.Name(), ".md"))] = true
		raw, err := os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			continue
		}
		for _, t := range tagsOf(string(raw)) {
			out[Term(t)] = true
		}
	}
	return out
}

// nearMisses are the groupings that had at least two sources and missed the
// bar, for the cadence re-audit.
func nearMisses(sources []Source) []Skipped {
	byTerm := map[string][]Source{}
	for _, s := range sources {
		for _, t := range s.Terms {
			byTerm[t] = append(byTerm[t], s)
		}
	}
	terms := make([]string, 0, len(byTerm))
	for t := range byTerm {
		terms = append(terms, t)
	}
	sort.Strings(terms)
	var out []Skipped
	for _, t := range terms {
		c := Cluster{Subject: t, Sources: byTerm[t]}
		if len(c.Sources) < 2 {
			continue
		}
		if ok, why := c.Meets(); !ok {
			out = append(out, Skipped{Subject: t, Sources: len(c.Sources), Reason: why})
		}
	}
	return out
}
