package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

// Sequence: a project's documents carry the order they were written in.
//
// A task directory is `tasks/001-refine-the-vault/`, assigned at `/plan` as one
// more than the highest in the folder. The design gives the project's own
// documents the same treatment — `decisions/007-tasks-are-directories.md`,
// `research/012-filing-v2-synthesis.md` — so `ls`, Obsidian and the map all
// show them in the order the work happened, newest at the bottom.
//
// Why a number and not a date: a date is nine characters on every name and
// still needs a tie-break for two files from one day; the tracker carries the
// dates, and the name only has to carry the order. Why three digits: two
// overflow at a hundred, and one project's research folder already holds 233
// files.
//
// **Whichever writer creates a file numbers it.** This job is the catch: a file
// dropped into one of those folders without a number is given the next one by
// its own `created` date, so the order it lands in is the order it was written
// in rather than the order it was noticed.
//
// Its first run is a data run and not a nightly one. No project document
// carries a prefix today, so the first pass renames some hundreds of files at
// once — and a rename changes a note's ledger key and its embeddings as well as
// its name. That run is supervised, under quiesce, with a manifest and a cap,
// in the order the migration runbook sets. From then on there is nothing for it
// to do but the occasional file dropped in by hand.

const (
	JobSequence = "sequence"
	// SequenceWidth is the zero-padding: three digits, because two overflow at
	// a hundred.
	SequenceWidth = 3
)

// sequencedFolders are the per-project folders whose files carry the order
// prefix. The root files — `charter`, `tracker`, `followups`, `roadmap`, the
// map — and `desk/` carry no number: they are the project's standing documents,
// not a sequence of work.
var sequencedFolders = []string{"decisions", "designs", "research", "drafts"}

var sequencePrefix = regexp.MustCompile(`^(\d{3})-`)
var createdRe = regexp.MustCompile(`(?m)^created:[ \t]*"?'?(\d{4}-\d{2}-\d{2})`)

// SequencePlan is what one pass would (or did) number.
type SequencePlan struct {
	Intents  []Intent      `json:"-"`
	Numbered []SequenceRow `json:"numbered"`
	// Already is how many files already carry a prefix. On a settled vault this
	// is every file and `Numbered` is empty, which is the steady state.
	Already int    `json:"already"`
	Capped  int    `json:"skipped_by_cap"`
	Skipped string `json:"skipped,omitempty"`
}

// SequenceRow is one file and the name it takes.
type SequenceRow struct {
	From string `json:"from"`
	To   string `json:"to"`
}

// ProjectsRoot is the vault's `projects/` space, resolved from the memory root
// the way every other job here resolves a sibling space, and empty when the
// vault has none.
func ProjectsRoot(root string) string {
	space := filepath.Join(vaultRootOf(root), projectsSpaceName)
	if !isDirExact(space) {
		return ""
	}
	return space
}

// PlanSequence numbers the project documents that carry no prefix.
//
// `projectsRoot` is the vault's `projects/` space as an absolute path, and the
// rels this returns are relative to `root` — the memory root — because that is
// what the journal applies intents against.
func PlanSequence(root, projectsRoot string, now time.Time, cap int) (SequencePlan, error) {
	var plan SequencePlan
	if projectsRoot == "" {
		plan.Skipped = "no projects/ space"
		return plan, nil
	}
	if cap <= 0 {
		cap = DefaultDemotionCap
	}
	projects, err := os.ReadDir(projectsRoot)
	if err != nil {
		plan.Skipped = "the projects space is unreadable"
		return plan, nil
	}
	for _, project := range projects {
		if !project.IsDir() || strings.HasPrefix(project.Name(), ".") {
			continue
		}
		for _, folder := range sequencedFolders {
			dir := filepath.Join(projectsRoot, project.Name(), folder)
			entries, err := os.ReadDir(dir)
			if err != nil {
				continue
			}
			// The numbers already taken in this folder, and the files owed one.
			taken := map[int]bool{}
			var owed []string
			highest := 0
			for _, e := range entries {
				if e.IsDir() || filepath.Ext(e.Name()) != ".md" {
					continue
				}
				if m := sequencePrefix.FindStringSubmatch(e.Name()); m != nil {
					n := 0
					fmt.Sscanf(m[1], "%d", &n)
					taken[n] = true
					if n > highest {
						highest = n
					}
					plan.Already++
					continue
				}
				owed = append(owed, filepath.Join(dir, e.Name()))
			}
			if len(owed) == 0 {
				continue
			}
			// By the file's own `created` date, so the order the numbers give
			// is the order the work happened rather than the order the
			// directory listing happened to be in. A file with no date sorts
			// last, by name, which is stable.
			sort.Slice(owed, func(i, j int) bool {
				di, dj := createdOf(owed[i]), createdOf(owed[j])
				if di != dj {
					return di < dj
				}
				return owed[i] < owed[j]
			})
			for _, p := range owed {
				if len(plan.Numbered) >= cap {
					plan.Capped++
					continue
				}
				highest++
				for taken[highest] {
					highest++
				}
				taken[highest] = true
				name := fmt.Sprintf("%0*d-%s", SequenceWidth, highest, filepath.Base(p))
				dst := filepath.Join(filepath.Dir(p), name)
				raw, err := os.ReadFile(p)
				if err != nil {
					continue
				}
				fromRel, err1 := relTo(root, p)
				toRel, err2 := relTo(root, dst)
				if err1 != nil || err2 != nil {
					continue
				}
				summary := "numbered by its created date"
				plan.Intents = append(plan.Intents, Intent{Job: JobSequence, Rel: fromRel,
					To: toRel, Before: raw, After: raw, Summary: summary,
					Meta: map[string]string{"from": "unnumbered", "to": "numbered", "reason": summary}})
				plan.Numbered = append(plan.Numbered, SequenceRow{From: fromRel, To: toRel})
			}
		}
	}
	return plan, nil
}

// createdOf is a note's own `created:` date, or the empty string.
func createdOf(p string) string {
	raw, err := os.ReadFile(p)
	if err != nil {
		return ""
	}
	head := string(raw)
	if len(head) > 4000 {
		head = head[:4000]
	}
	if m := createdRe.FindStringSubmatch(head); m != nil {
		return m[1]
	}
	// No date sorts last, which `zzzz` does against any ISO date.
	return "zzzz"
}

// relTo renders `p` relative to `root`, in POSIX form — including the `../`
// spelling a vault-root sibling like `projects/` takes from the memory root.
func relTo(root, p string) (string, error) {
	rel, err := filepath.Rel(root, p)
	if err != nil {
		return "", err
	}
	return filepath.ToSlash(rel), nil
}
