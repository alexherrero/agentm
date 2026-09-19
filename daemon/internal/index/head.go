package index

import (
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

// A hit is the card's readable head (agentm-vault § Surfaces).
//
// Before this a hit was a path, a score and a 24-token snippet. A session asking
// what it had found got an address and a fragment, and had to open five files to
// learn what any of them were. The design's answer is that a hit carries the
// fields a person reads — the card's own order, `title` first — and never the
// body, which the surface reads from the file, and never `why`, which is the
// room's reasoning written for the operator rather than about the note.
//
// # Read at serve time, not stored in the index
//
// The obvious alternative is columns on `docmeta`, filled at index time. This
// reads the file instead, for the k rows actually being returned, and that is
// the cheaper *and* the more correct choice:
//
//   - **Correct**, because a stored copy of `summary` is stale the moment the
//     operator edits the note, and the index reconciles on a timer. A hit would
//     carry the summary the note had this morning and say nothing about it.
//   - **Cheap**, because k is five. Five small reads against a warm page cache
//     are microseconds on a path that budgets 300ms, and the prompt hook already
//     reads every hit it injects.
//   - **Small**, because it needs no schema change, no backfill and no reindex —
//     a migration whose only purpose is to cache something already on disk.
//
// The read happens after ranking, over the rows that survived every wall, so a
// note nobody was shown costs nothing.

// The separator is `[ \t]*`, never `\s*`.
//
// Go's `\s` includes `\n`, so `^project:\s*(.+)$` against a field the operator
// left empty —
//
//	project:
//	parent_design: wiki/designs/agentm-vault.md
//
// — steps over the line break and captures the *next* field's text. Found on a
// live card, which reported `project: "parent_design: wiki/designs/…"`. Every
// field here was written that way, so an empty one anywhere in a card's
// frontmatter would have taken whatever followed it.
var (
	headTypeRe       = regexp.MustCompile(`(?mi)^type:[ \t]*(.+)$`)
	headKindRe       = regexp.MustCompile(`(?mi)^kind:[ \t]*(.+)$`)
	headSummaryRe    = regexp.MustCompile(`(?mi)^summary:[ \t]*(.+)$`)
	headTitleRe      = regexp.MustCompile(`(?mi)^title:[ \t]*(.+)$`)
	headStatusRe     = regexp.MustCompile(`(?mi)^status:[ \t]*(.+)$`)
	headLifecycleRe  = regexp.MustCompile(`(?mi)^lifecycle:[ \t]*(.+)$`)
	headImportanceRe = regexp.MustCompile(`(?mi)^importance:[ \t]*(\d+)`)
	headProjectRe    = regexp.MustCompile(`(?mi)^project:[ \t]*(.+)$`)
)

// headProbeBytes is how far into a file to look for its frontmatter.
//
// Counted generously and in bytes rather than lines because this is one read
// either way. A session trace's `touched:` list is a single line running past a
// thousand characters, and a cap that clipped it would end inside the
// frontmatter — the same mistake the prompt hook's own kind probe made, found
// by running it against the live corpus rather than against a fixture.
const headProbeBytes = 16 * 1024

func headValue(head string, re *regexp.Regexp) string {
	m := re.FindStringSubmatch(head)
	if m == nil {
		return ""
	}
	return strings.Trim(strings.TrimSpace(m[1]), `'"`)
}

// fillHeads reads each served row's frontmatter and fills its readable fields.
//
// Best-effort per row: a note that cannot be read still comes back as a hit with
// its path and score, because an address is worth more than nothing and a search
// that failed because one file was unreadable would be worse than one that
// answered plainly.
func (x *Index) fillHeads(rows []Result) {
	for i := range rows {
		abs := filepath.Join(x.vault, filepath.FromSlash(rows[i].Path))
		f, err := os.Open(abs)
		if err != nil {
			continue
		}
		buf := make([]byte, headProbeBytes)
		n, _ := f.Read(buf)
		f.Close()
		if n <= 0 {
			continue
		}
		text := string(buf[:n])
		if !strings.HasPrefix(text, "---") {
			continue
		}
		end := strings.Index(text[3:], "\n---")
		if end < 0 {
			// The frontmatter did not close inside the probe. Read what is
			// there rather than nothing: every field below is line-anchored,
			// so a clipped block yields the fields it did contain.
			end = len(text) - 3
		}
		head := text[3 : 3+end]

		rows[i].Title = headValue(head, headTitleRe)
		// `type` or `kind`, never both — the contract's own rule, and the
		// reason this is two fields rather than one: a reader can tell a memory
		// from a record without knowing the vocabulary.
		rows[i].Type = headValue(head, headTypeRe)
		rows[i].Kind = headValue(head, headKindRe)
		rows[i].Summary = headValue(head, headSummaryRe)
		rows[i].Status = headValue(head, headStatusRe)
		rows[i].Lifecycle = headValue(head, headLifecycleRe)
		rows[i].Project = headValue(head, headProjectRe)
		if m := headImportanceRe.FindStringSubmatch(head); m != nil {
			if v, err := strconv.Atoi(strings.TrimSpace(m[1])); err == nil {
				rows[i].Importance = v
			}
		}
		// `why` is deliberately not read. It is the reason the operator kept
		// the note, written for them, and a hit list is not where it belongs;
		// a surface that wants it opens the file. The body is not read for the
		// same reason plus a plainer one: five bodies is the whole budget the
		// prompt hook has, and five heads are a screen.
	}
}
