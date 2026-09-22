package dreaming

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/config"
)

// `Ideas.md` — the operator's list of ideas, generated over the idea cards in
// the vault root's `personal/ideas/` (agentm-vault part 13) and rewritten with
// the other maps each night.
//
// The file has two halves and the line between them is a pair of markers. Above
// and between them is the operator's: the title, and the introduction they
// approved. It is copied into every rewrite byte for byte — not re-rendered from
// a template, copied — so no change to this file can move a character of it.
// Below the end marker is the machine's: one `## <area>` heading per group name
// the cards actually carry, each idea one line linking to its card with its
// title and summary, and the dismissed ideas in one collapsed callout at the
// end.
//
// A file with no markers is never written. That is not a fallback, it is the
// adoption rule: the operator's own `Ideas.md` predates this job, and the first
// rewrite replaces it wholesale, so it happens once, on purpose, through
// `agentmdream ideas -intro <file> -write`, after the operator has read the
// dry run. From then on the markers are the operator's standing permission, and
// deleting them withdraws it.
//
// Nothing here carries a date or a count that moves without a card moving, so a
// rewrite over an unchanged folder is byte-identical and plans nothing.

const (
	// IdeasFile is the list, at the vault root.
	IdeasFile = "Ideas.md"
	// IdeasDirRel is the folder of idea cards, vault-relative. It is the same
	// folder enrich.IdeasDir names for the night's queue; spelled here too
	// rather than imported, because this package does not depend on the
	// enrichment one.
	IdeasDirRel = "personal/ideas"
	// IdeasIntroStart and IdeasIntroEnd are the marker lines, as the adoption
	// writes them. They are found by their prefixes, so the operator may reword
	// a marker's tail without losing it.
	IdeasIntroStart = "<!-- ideas:intro:start — the introduction above the list is yours; the night copies it as it is -->"
	IdeasIntroEnd   = "<!-- ideas:intro:end — everything below is rebuilt every night from personal/ideas/ -->"

	ideasStartPrefix = "<!-- ideas:intro:start"
	ideasEndPrefix   = "<!-- ideas:intro:end"
	// ideasNoGroup heads the cards that carry no `area:` yet. Last, whatever
	// the group names are.
	ideasNoGroup = "no group yet"
)

// ErrIdeasNotAdopted is a file with no markers, or no file at all: there is no
// operator text to keep, so there is no permission to write.
var ErrIdeasNotAdopted = errors.New("Ideas.md carries no markers, so it has not been adopted and is not written")

// IdeaCard is one card as the list shows it.
type IdeaCard struct {
	Rel       string `json:"rel"`
	Slug      string `json:"slug"`
	Title     string `json:"title"`
	Summary   string `json:"summary,omitempty"`
	Area      string `json:"area,omitempty"`
	Dismissed string `json:"dismissed,omitempty"`
}

// ReadIdeaCards is every card directly in `personal/ideas/`: markdown files,
// dotfiles and subfolders left out, in slug order. No folder is no cards.
func ReadIdeaCards(vault string) ([]IdeaCard, error) {
	dir := filepath.Join(vault, filepath.FromSlash(IdeasDirRel))
	if !isDirExact(dir) {
		return nil, nil
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	var out []IdeaCard
	for _, e := range entries {
		name := e.Name()
		if e.IsDir() || strings.HasPrefix(name, ".") || !strings.HasSuffix(name, ".md") {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil {
			return nil, err
		}
		fm, _ := ParseFrontmatter(string(raw))
		slug := strings.TrimSuffix(name, ".md")
		c := IdeaCard{
			Rel:       IdeasDirRel + "/" + name,
			Slug:      slug,
			Title:     oneLine(fm["title"]),
			Summary:   oneLine(fm["summary"]),
			Area:      strings.TrimSpace(fm["area"]),
			Dismissed: strings.TrimSpace(fm["dismissed"]),
		}
		if c.Title == "" {
			c.Title = slug
		}
		out = append(out, c)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Slug < out[j].Slug })
	return out, nil
}

// oneLine is a frontmatter scalar as one line of the list: whitespace runs
// folded, and the two characters a wikilink's label cannot hold made safe.
func oneLine(s string) string {
	s = strings.Join(strings.Fields(s), " ")
	s = strings.ReplaceAll(s, "|", "/")
	s = strings.ReplaceAll(s, "[[", "[")
	return strings.ReplaceAll(s, "]]", "]")
}

// IdeasHead is the operator's half of an existing `Ideas.md`: every byte from
// the start of the file through the end marker's line and its newline. It
// errors on a file without both markers in order, since keeping "the text
// between the markers" is only a promise when there are markers.
func IdeasHead(current string) (string, error) {
	lines := strings.SplitAfter(current, "\n")
	start, end := -1, -1
	for i, l := range lines {
		t := strings.TrimSpace(l)
		switch {
		case strings.HasPrefix(t, ideasStartPrefix):
			if start >= 0 {
				return "", fmt.Errorf("Ideas.md carries two start markers; it is left as it is until one is removed")
			}
			start = i
		case strings.HasPrefix(t, ideasEndPrefix):
			if end >= 0 {
				return "", fmt.Errorf("Ideas.md carries two end markers; it is left as it is until one is removed")
			}
			end = i
		}
	}
	if start < 0 || end < 0 {
		return "", ErrIdeasNotAdopted
	}
	if end < start {
		return "", fmt.Errorf("Ideas.md's end marker comes before its start marker; it is left as it is")
	}
	head := strings.Join(lines[:end+1], "")
	if !strings.HasSuffix(head, "\n") {
		head += "\n"
	}
	return head, nil
}

// AdoptedHead is the head the first write gives the file: the title, the start
// marker, the introduction the operator approved, and the end marker. The
// introduction goes in as it was given, trimmed of blank lines at either end.
func AdoptedHead(intro string) string {
	intro = strings.Trim(strings.ReplaceAll(intro, "\r\n", "\n"), "\n")
	return "# Ideas\n\n" + IdeasIntroStart + "\n\n" + intro + "\n\n" + IdeasIntroEnd + "\n"
}

// RenderIdeas is the whole file: the head, as given, and the list under it.
func RenderIdeas(head string, cards []IdeaCard) string {
	groups := map[string][]IdeaCard{}
	var dismissed []IdeaCard
	for _, c := range cards {
		if c.Dismissed != "" {
			dismissed = append(dismissed, c)
			continue
		}
		area := c.Area
		if area == "" {
			area = ideasNoGroup
		}
		groups[area] = append(groups[area], c)
	}
	var names []string
	for name := range groups {
		if name != ideasNoGroup {
			names = append(names, name)
		}
	}
	sort.Slice(names, func(i, j int) bool {
		a, b := strings.ToLower(names[i]), strings.ToLower(names[j])
		if a != b {
			return a < b
		}
		return names[i] < names[j]
	})
	if len(groups[ideasNoGroup]) > 0 {
		names = append(names, ideasNoGroup)
	}
	var b strings.Builder
	b.WriteString(head)
	for _, name := range names {
		ms := groups[name]
		sortByTitle(ms)
		fmt.Fprintf(&b, "\n## %s\n\n", name)
		for _, c := range ms {
			b.WriteString(ideaLine(c) + "\n")
		}
	}
	if len(dismissed) > 0 {
		sortByTitle(dismissed)
		fmt.Fprintf(&b, "\n> [!note]- Dismissed (%d)\n>\n", len(dismissed))
		for _, c := range dismissed {
			b.WriteString("> " + ideaLine(c) + " · dismissed " + c.Dismissed + "\n")
		}
	}
	return b.String()
}

func sortByTitle(cs []IdeaCard) {
	sort.SliceStable(cs, func(i, j int) bool {
		a, b := strings.ToLower(cs[i].Title), strings.ToLower(cs[j].Title)
		if a != b {
			return a < b
		}
		return cs[i].Slug < cs[j].Slug
	})
}

func ideaLine(c IdeaCard) string {
	line := "- [[" + c.Slug + "|" + c.Title + "]]"
	if c.Summary != "" {
		line += " — " + c.Summary
	}
	return line
}

// IdeasPlan is what the job would write, or why it would not.
type IdeasPlan struct {
	Intents []Intent `json:"-"`
	// Rel is the file, memory-root relative, the way an intent names it.
	Rel     string `json:"rel"`
	Cards   int    `json:"cards"`
	Groups  int    `json:"groups"`
	Changed bool   `json:"changed"`
	// NotWritten says why nothing was planned for a file that exists: it has
	// not been adopted, or its markers are not a pair. Empty when the plan
	// writes, or when there is nothing to write because nothing moved.
	NotWritten string `json:"not_written,omitempty"`
	// Text is the rendering, for the dry run to show.
	Text string `json:"-"`
}

// PlanIdeas decides `Ideas.md`. With `intro` empty it regenerates under the
// file's own head and plans nothing for a file without markers; with `intro`
// set it is the adoption, and the head is built from the introduction. It
// writes nothing.
func PlanIdeas(root, intro string) (IdeasPlan, error) {
	vault := vaultRootOf(root)
	abs := filepath.Join(vault, IdeasFile)
	plan := IdeasPlan{Rel: memoryRel(root, abs)}
	cards, err := ReadIdeaCards(vault)
	if err != nil {
		return plan, err
	}
	plan.Cards = len(cards)
	before, err := os.ReadFile(abs)
	if err != nil && !os.IsNotExist(err) {
		return plan, err
	}
	var head string
	if intro != "" {
		head = AdoptedHead(intro)
	} else {
		if before == nil {
			plan.NotWritten = ErrIdeasNotAdopted.Error()
			return plan, nil
		}
		if head, err = IdeasHead(string(before)); err != nil {
			plan.NotWritten = err.Error()
			return plan, nil
		}
	}
	text := RenderIdeas(head, cards)
	plan.Text = text
	groups := map[string]bool{}
	for _, c := range cards {
		if c.Dismissed == "" {
			groups[c.Area] = true
		}
	}
	plan.Groups = len(groups)
	if before != nil && string(before) == text {
		return plan, nil
	}
	plan.Changed = true
	plan.Intents = []Intent{{Job: JobMocs, Rel: plan.Rel, Before: before, After: []byte(text),
		Summary: fmt.Sprintf("Ideas.md rebuilt over %d idea card(s) in %d group(s)", len(cards), len(groups))}}
	return plan, nil
}

// IdeasOptions is the `agentmdream ideas` command's.
type IdeasOptions struct {
	// Intro is the approved introduction for the adoption; empty regenerates.
	Intro string
	// Write makes the write, through the journal and under the dreaming lock.
	// Without it the command is the dry run.
	Write    bool
	Now      time.Time
	LockWait time.Duration
}

// Ideas is the dry run, or the one deliberate write: the plan, and when asked,
// the plan applied the way the night applies it — journaled first, under the
// same lock, so a crash half-way is resumed by the next pass like any other.
func Ideas(cfg *config.Config, opt IdeasOptions) (IdeasPlan, error) {
	root := filepath.Join(cfg.VaultPath, filepath.FromSlash(cfg.MemoryRoot))
	plan, err := PlanIdeas(root, opt.Intro)
	if err != nil || !opt.Write || len(plan.Intents) == 0 {
		return plan, err
	}
	now := opt.Now
	if now.IsZero() {
		now = time.Now().UTC()
	}
	if opt.LockWait <= 0 {
		opt.LockWait = 2 * time.Second
	}
	lock, err := Acquire(SingletonLockDir(cfg.EngineStateDir), 30*time.Second, opt.LockWait)
	if err != nil {
		return plan, err
	}
	defer lock.Release()
	journal, err := OpenJournal(cfg.EngineStateDir)
	if err != nil {
		return plan, err
	}
	runID := "ideas-" + newRunID(now)
	if err := journal.Append(Entry{Kind: KindRunStart, RunID: runID, TS: now, Mode: "apply"}); err != nil {
		return plan, err
	}
	var rep Report
	if err := applyAll(journal, root, runID, plan.Intents, now, 0, &rep); err != nil {
		return plan, err
	}
	outcome := fmt.Sprintf("Ideas.md written: %d applied, %d skipped", rep.Applied, rep.Skipped)
	if rep.Skipped > 0 {
		err = fmt.Errorf("Ideas.md changed between the plan and the write, so nothing was written; run it again")
	}
	if jerr := journal.Append(Entry{Kind: KindRunDone, RunID: runID, TS: now, Outcome: outcome}); jerr != nil && err == nil {
		err = jerr
	}
	return plan, err
}
