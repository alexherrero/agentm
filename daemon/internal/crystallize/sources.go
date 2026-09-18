package crystallize

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

// What the phase reads: the Outcomes of closed tasks, the cards sharing a
// `project:` or a subject, and the candidate lines the traces accumulate
// (agentm-vault § Projects and tasks). Each becomes a Source with a session
// and a date, because the bar counts sittings and days and not mentions.

var (
	fmFenceRe = regexp.MustCompile(`(?s)\A---\n(.*?)\n---`)
	sectionRe = regexp.MustCompile(`(?m)^##+ `)
	// candidateLineRe is one `## Candidates` bullet as episodic_trace.py writes
	// it: `- <rule> (×N) — “<excerpt>”`, the count and the excerpt optional.
	candidateLineRe = regexp.MustCompile(`^-\s+(.+?)(?:\s+\(×\d+\))?(?:\s+—\s+[“"](.*)[”"])?\s*$`)
	fenceLineRe     = regexp.MustCompile("^```")
)

// classDirs are the memory classes whose cards the phase reads. Crystallized
// lessons are not sources for lessons — a lesson consolidating lessons is the
// shape that runs away — and `mocs` and `entities` are generated.
var classDirs = []string{"semantic", "procedural", "episodic"}

// frontmatter is the top-level keys of a note, unparsed values.
//
// A small reader rather than a YAML library: these are flat `key: value` lines
// and one list form, the corpus is written by this repo's own writers, and a
// parser that accepts more than the writers emit would accept a note shape
// nobody checks.
func frontmatter(text string) map[string]string {
	m := fmFenceRe.FindStringSubmatch(text)
	if m == nil {
		return map[string]string{}
	}
	out := map[string]string{}
	key := ""
	for _, line := range strings.Split(m[1], "\n") {
		if strings.HasPrefix(line, "  - ") || strings.HasPrefix(line, "- ") {
			if key != "" {
				v := strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(line), "- "))
				if out[key] == "" {
					out[key] = v
				} else {
					out[key] += ", " + v
				}
			}
			continue
		}
		i := strings.Index(line, ":")
		if i <= 0 || strings.HasPrefix(line, " ") {
			continue
		}
		key = strings.TrimSpace(line[:i])
		out[key] = strings.TrimSpace(line[i+1:])
	}
	return out
}

// unquote strips the quoting a value may carry.
func unquote(s string) string {
	return strings.Trim(strings.TrimSpace(s), `"'`)
}

// tagsOf is a note's tags, whether written inline (`[a, b]`) or as a list.
func tagsOf(text string) []string {
	raw := frontmatter(text)["tags"]
	if raw == "" {
		return nil
	}
	var out []string
	for _, t := range strings.Split(strings.Trim(raw, "[]"), ",") {
		if t = unquote(t); t != "" && t != "[]" {
			out = append(out, t)
		}
	}
	return out
}

// section is the body of one `## Heading`, to the next heading of any depth.
func section(text, heading string) string {
	idx := strings.Index(text, "\n## "+heading)
	if idx < 0 {
		if !strings.HasPrefix(text, "## "+heading) {
			return ""
		}
		idx = -1
	}
	rest := text[idx+1:]
	if i := strings.Index(rest, "\n"); i >= 0 {
		rest = rest[i+1:]
	}
	if loc := sectionRe.FindStringIndex(rest); loc != nil {
		rest = rest[:loc[0]]
	}
	return strings.TrimSpace(rest)
}

// vaultRel is `path` relative to the vault root, falling back to `fallback`
// when the two are not under one another — a flat export, or a memory root
// mounted somewhere else entirely. One base for every source, because a link
// built from a card's base and a tracker's base is two different links, and a
// writer that joined the wrong base would silently fail to write and look
// exactly like a rule that had held.
func vaultRel(vault, path, fallback string) string {
	if vault == "" {
		return fallback
	}
	rel, err := filepath.Rel(vault, path)
	if err != nil || strings.HasPrefix(rel, "..") {
		return fallback
	}
	return filepath.ToSlash(rel)
}

// dateOf reads the first of these keys that parses as a date, falling back to
// the file's own modification time.
//
// A date is load-bearing here — it is two thirds of the bar — so a source with
// no readable date is dated by the filesystem rather than dropped. Dropping it
// would quietly lower the count that decides whether a lesson gets written.
func dateOf(fm map[string]string, path string, keys ...string) time.Time {
	for _, k := range keys {
		v := unquote(fm[k])
		if len(v) >= 10 {
			if t, err := time.Parse("2006-01-02", v[:10]); err == nil {
				return t
			}
		}
	}
	if st, err := os.Stat(path); err == nil {
		return st.ModTime().UTC().Truncate(24 * time.Hour)
	}
	return time.Time{}
}

// Gather reads every source the phase judges.
//
// `root` is the memory root (the directory holding `memory/`) and `vault` is
// the vault root (the one holding `projects/`). They are different directories
// on the shipped layout and the same one on a flat export, which is why both
// are passed rather than derived.
func Gather(root, vault string, now time.Time) ([]Source, error) {
	var out []Source
	out = append(out, outcomes(vault)...)
	cards, traces := cardsAndTraces(root, vault)
	out = append(out, cards...)
	out = append(out, traces...)
	sort.Slice(out, func(i, j int) bool {
		if !out[i].At.Equal(out[j].At) {
			return out[i].At.Before(out[j].At)
		}
		return out[i].Rel < out[j].Rel
	})
	return out, nil
}

// outcomes are the closed tasks' Outcomes, and the closed projects' own.
//
// A project's tracker is read as well as its tasks': when an arc closes, the
// closing session marks it in the project tracker and the phase's next run
// writes the arc's synthesis from the arc's Outcomes. That synthesis is an
// ordinary lesson here — the same bar, the same writer — clustered with the
// task Outcomes it is made of, because a synthesis built from anything looser
// than the bar is a summary and the design asked for a lesson.
func outcomes(vault string) []Source {
	var out []Source
	roots := []string{
		filepath.Join(vault, "projects"),
		filepath.Join(vault, "projects", "completed"),
	}
	seen := map[string]bool{}
	for _, base := range roots {
		entries, err := os.ReadDir(base)
		if err != nil {
			continue
		}
		for _, e := range entries {
			if !e.IsDir() || e.Name() == "completed" || strings.HasPrefix(e.Name(), ".") ||
				strings.HasPrefix(e.Name(), "_") {
				continue
			}
			slug := e.Name()
			dir := filepath.Join(base, slug)
			trackers := []string{filepath.Join(dir, "tracker.md")}
			if tasks, err := os.ReadDir(filepath.Join(dir, "tasks")); err == nil {
				for _, t := range tasks {
					if t.IsDir() {
						trackers = append(trackers,
							filepath.Join(dir, "tasks", t.Name(), "tracker.md"))
					}
				}
			}
			for _, p := range trackers {
				if seen[p] {
					continue
				}
				seen[p] = true
				if s, ok := outcomeSource(vault, slug, p); ok {
					out = append(out, s)
				}
			}
		}
	}
	return out
}

// outcomeSource is one tracker's Outcome, when it has closed and written one.
func outcomeSource(vault, slug, path string) (Source, bool) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return Source{}, false
	}
	text := string(raw)
	fm := frontmatter(text)
	if strings.ToLower(unquote(fm["status"])) != "done" {
		return Source{}, false
	}
	body := section(text, "Outcome")
	if body == "" {
		return Source{}, false
	}
	rel, err := filepath.Rel(vault, path)
	if err != nil {
		rel = path
	}
	// The session is the task directory: everything one task wrote is one
	// sitting, however many files it left behind.
	sessionDir := filepath.Dir(path)
	s := Source{
		Rel: filepath.ToSlash(rel), Path: path, Kind: KindOutcome, Project: slug,
		Session: filepath.ToSlash(filepath.Base(sessionDir)),
		At:      dateOf(fm, path, "closed", "updated", "created"),
		Title:   unquote(fm["title"]), Text: body,
	}
	s.Terms = termsOf(tagsOf(text), body)
	return s, len(s.Terms) > 0
}

// cardsAndTraces are the memory cards, and the candidate lines inside the
// session traces.
func cardsAndTraces(root, vault string) (cards, candidates []Source) {
	for _, class := range classDirs {
		dir := filepath.Join(root, "memory", class)
		entries, err := os.ReadDir(dir)
		if err != nil {
			continue
		}
		names := make([]string, 0, len(entries))
		for _, e := range entries {
			if !e.IsDir() && strings.HasSuffix(e.Name(), ".md") {
				names = append(names, e.Name())
			}
		}
		sort.Strings(names)
		for _, name := range names {
			path := filepath.Join(dir, name)
			raw, err := os.ReadFile(path)
			if err != nil {
				continue
			}
			text := string(raw)
			fm := frontmatter(text)
			rel := vaultRel(vault, path, "memory/"+class+"/"+name)
			if unquote(fm["kind"]) == "session-trace" {
				candidates = append(candidates, traceCandidates(rel, path, text, fm)...)
				continue
			}
			// A source already consolidated has taught its lesson. Reading it
			// again would let one recurrence write a second lesson about
			// itself every week for as long as the sources exist.
			if unquote(fm["consolidated_into"]) != "" {
				continue
			}
			body := strings.TrimSpace(fmFenceRe.ReplaceAllString(text, ""))
			s := Source{
				Rel: rel, Path: path, Kind: KindCard, Project: unquote(fm["project"]),
				Session: sessionOf(fm, rel),
				At:      dateOf(fm, path, "created", "updated"),
				Title:   unquote(fm["title"]), Text: body,
			}
			s.Terms = termsOf(tagsOf(text), body)
			if len(s.Terms) > 0 {
				cards = append(cards, s)
			}
		}
	}
	return cards, candidates
}

// sessionOf is the sitting a card belongs to: the session it was captured in
// when it says, else the card itself. A card with no session is its own
// sitting rather than everybody's — the alternative folds every unattributed
// card into one session and makes MinSessions unreachable.
func sessionOf(fm map[string]string, rel string) string {
	for _, k := range []string{"source_session", "session", "source_id"} {
		if v := unquote(fm[k]); v != "" {
			return v
		}
	}
	return rel
}

// traceCandidates are one trace's `## Candidates` bullets: what a session said
// in passing that might be worth keeping. The trace is the session.
func traceCandidates(rel, path, text string, fm map[string]string) []Source {
	at := dateOf(fm, path, "created", "updated")
	project := unquote(fm["project"])
	session := sessionOf(fm, rel)
	var out []Source
	inFence := false
	inSection := false
	for _, line := range strings.Split(text, "\n") {
		trimmed := strings.TrimSpace(line)
		if fenceLineRe.MatchString(trimmed) {
			inFence = !inFence
			continue
		}
		if inFence {
			continue
		}
		if strings.HasPrefix(line, "#") {
			inSection = strings.TrimSpace(strings.TrimLeft(line, "# ")) == "Candidates"
			continue
		}
		if !inSection || !strings.HasPrefix(trimmed, "- ") {
			continue
		}
		m := candidateLineRe.FindStringSubmatch(trimmed)
		if m == nil || strings.TrimSpace(m[2]) == "" {
			continue
		}
		s := Source{
			// Rel is the trace, so `consolidated_from` links something a
			// reader can open. It is never stamped: a trace is one session's
			// record and several of its lines may teach different lessons.
			Rel: rel, Path: path, Kind: KindCandidate, Project: project,
			Session: session, At: at, Title: strings.TrimSpace(m[1]),
			Text: strings.TrimSpace(m[2]),
		}
		s.Terms = termsOf(nil, s.Text)
		if len(s.Terms) > 0 {
			out = append(out, s)
		}
	}
	return out
}
