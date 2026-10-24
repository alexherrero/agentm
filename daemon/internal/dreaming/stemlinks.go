package dreaming

import (
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"strings"

	"golang.org/x/text/unicode/norm"
)

// The unique-stem rule (task 193 LD-4, agentm-vault): a generated link names a
// note by its bare file name only when no other note in the vault has that
// name. Obsidian resolves a bare `[[name]]` against every note in the vault,
// and when several share the name it lands on whichever one it prefers, so a
// shared name is linked by the note's vault-relative path with the name as its
// label: `[[agent/memory/semantic/_index|semantic]]`. A unique name keeps its
// bare link, so a page whose names are all unique renders as it did before.
//
// The names counted are Obsidian's: every `.md` file outside the dot-folders,
// matched case-insensitively in NFC. The walk lists names and opens no note.
// The Python side is `harness/skills/memory/scripts/stem_links.py`, held to
// the same cases.

// StemIndex is how many notes in the vault carry each file name.
type StemIndex struct {
	vault  string
	counts map[string]int
}

func stemKey(stem string) string {
	return strings.ToLower(norm.NFC.String(stem))
}

// NewStemIndex counts the vault's note names. A vault it cannot walk counts
// none, and every name then reads as unique: the links fall back to the bare
// names the maps wrote before the rule.
func NewStemIndex(vault string) *StemIndex {
	s := &StemIndex{vault: vault, counts: map[string]int{}}
	_ = filepath.WalkDir(vault, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		name := d.Name()
		if d.IsDir() {
			if p != vault && strings.HasPrefix(name, ".") {
				return filepath.SkipDir
			}
			return nil
		}
		if strings.HasSuffix(name, ".md") && !strings.HasPrefix(name, ".") {
			s.counts[stemKey(strings.TrimSuffix(name, ".md"))]++
		}
		return nil
	})
	return s
}

// Plan counts a page at vault-relative `rel` that this pass writes and the
// vault does not hold yet, so a link written the night the page first appears
// already allows for it.
func (s *StemIndex) Plan(rel string) {
	if s == nil {
		return
	}
	if st, err := os.Stat(filepath.Join(s.vault, filepath.FromSlash(rel))); err == nil && !st.IsDir() {
		return
	}
	s.counts[stemKey(path.Base(strings.TrimSuffix(filepath.ToSlash(rel), ".md")))]++
}

// Unique says no other note shares the name. A note not on disk yet counts as
// unique while nothing else carries its name; a nil index finds every name
// unique.
func (s *StemIndex) Unique(stem string) bool {
	return s == nil || s.counts[stemKey(stem)] <= 1
}

// Link is the wikilink to the note at vault-relative `rel`: `[[stem]]` when its
// name is the vault's only one and the label is the name, `[[stem|label]]`
// when unique under another label, else `[[path|label]]`. An empty label is
// the name.
func (s *StemIndex) Link(rel, label string) string {
	target := strings.TrimSuffix(filepath.ToSlash(rel), ".md")
	stem := path.Base(target)
	if label == "" {
		label = stem
	}
	label = strings.TrimSpace(projLabelReplacer.Replace(label))
	if s.Unique(stem) {
		if label == stem {
			return "[[" + stem + "]]"
		}
		return "[[" + stem + "|" + label + "]]"
	}
	return "[[" + target + "|" + label + "]]"
}
