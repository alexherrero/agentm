package dreaming

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/alexherrero/agentm/daemon/internal/rules"
)

// Job "refile" — mutation authority over what write time got wrong, the
// part no Python stage has: a memory whose `type:` the contract routes to
// one class but which sits in another is moved to the class the contract
// names (the same basename; a taken basename is reported, never
// overwritten), and a `near-duplicate` review flag whose `related` twin no
// longer exists is cleared, since a reviewer can no longer act on it.
// Nothing here changes a note's words; a note with no `type` (still in
// enrichment's queue) or a record kind is not a memory and is left alone.

const JobRefile = "refile"

// Refile is one move or re-flag.
type Refile struct {
	Rel     string `json:"rel"`
	To      string `json:"to,omitempty"`
	Kind    string `json:"kind"` // move | unflag
	Summary string `json:"summary"`
}

// RefilePlan is what one pass would (or did) correct.
type RefilePlan struct {
	Intents []Intent `json:"-"`
	Moves   []Refile `json:"moves"`
	Unflags []Refile `json:"unflags"`
	// Blocked names a move whose destination basename is taken.
	Blocked    []Refile `json:"blocked"`
	Considered int      `json:"considered"`
}

// classOf is the class segment of `memory/<class>/…`, "" for anything else.
func classOf(rel string) string {
	parts := strings.Split(rel, "/")
	if len(parts) >= 3 && parts[0] == "memory" {
		return parts[1]
	}
	return ""
}

// PlanRefile reads the memory classes under `root` against the contract
// and decides the moves and re-flags. It writes nothing. A nil contract
// routes nothing and plans no move.
func PlanRefile(root string, r *rules.Rules) (RefilePlan, error) {
	var plan RefilePlan
	rels, err := MemoryNotes(root)
	if err != nil {
		return plan, err
	}
	exists := map[string]bool{}
	for _, rel := range rels {
		exists[rel] = true
	}
	for _, rel := range rels {
		raw, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
		if err != nil {
			continue
		}
		text := string(raw)
		fm, _ := ParseFrontmatter(text)
		if fm["kind"] != "" {
			continue // a record, not a memory
		}
		plan.Considered++
		// The stale flag: a twin that is gone cannot be reviewed against.
		if flags := ListField(fm["review_flags"]); len(flags) > 0 {
			related := strings.TrimSpace(fm["related"])
			hasNear := false
			for _, f := range flags {
				if f == "near-duplicate" {
					hasNear = true
				}
			}
			if hasNear && related != "" && !exists[related] {
				if _, err := os.Stat(filepath.Join(root, filepath.FromSlash(related))); os.IsNotExist(err) {
					after := DropFrontmatterKeys(text, "review_flags", "related")
					item := Refile{Rel: rel, Kind: "unflag",
						Summary: fmt.Sprintf("%s — flagged near-duplicate of %s, which no longer exists; clear the flag", rel, related)}
					plan.Unflags = append(plan.Unflags, item)
					plan.Intents = append(plan.Intents, Intent{Job: JobRefile, Rel: rel, Before: raw, After: []byte(after), Summary: item.Summary})
					continue
				}
			}
		}
		if r == nil {
			continue
		}
		noteType := strings.TrimSpace(fm["type"])
		if noteType == "" {
			continue
		}
		if repl, deprecated := r.ReplacementFor(noteType); deprecated && repl != "" {
			noteType = repl
		}
		class, ok := r.ClassFor(noteType)
		if !ok || class == "" {
			continue
		}
		if classOf(rel) == class {
			continue
		}
		to := "memory/" + class + "/" + filepath.Base(rel)
		item := Refile{Rel: rel, To: to, Kind: "move",
			Summary: fmt.Sprintf("%s — type %s routes to %s, not %s; re-file to %s", rel, noteType, class, classOf(rel), to)}
		if exists[to] {
			item.Summary = fmt.Sprintf("%s — type %s routes to %s, but %s is taken; left for review", rel, noteType, class, to)
			plan.Blocked = append(plan.Blocked, item)
			continue
		}
		plan.Moves = append(plan.Moves, item)
		plan.Intents = append(plan.Intents, Intent{Job: JobRefile, Rel: rel, To: to, Before: raw, After: raw, Summary: item.Summary})
	}
	return plan, nil
}
