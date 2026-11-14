package cardshape

import "strings"

// A note's `status` says where it stands, in one of the contract's short list
// of words (rules.DefaultStatuses). Four of them are a note's state rather
// than a choice anyone makes, and they follow the state in the same write that
// changes it (task 205's sibling, task 204): until then every archive,
// supersede and merge path moved `lifecycle` or stamped a pointer and left
// `status: active`, so 17 archived cards, 26 merged cards and 19 dismissed
// ideas all said `active` on 2026-11-13.
const (
	StatusActive     = "active"
	StatusArchived   = "archived"
	StatusSuperseded = "superseded"
	StatusMerged     = "merged"
	StatusDropped    = "dropped"
)

// StateStatuses are the words SyncStatus writes from a note's own state. A
// note that carries one has left the ordinary lifecycle, and only the state
// that put it there takes it back out.
var StateStatuses = []string{StatusArchived, StatusSuperseded, StatusMerged}

// IsStateStatus reports whether a status is one SyncStatus owns.
func IsStateStatus(status string) bool {
	s := strings.ToLower(strings.TrimSpace(status))
	for _, v := range StateStatuses {
		if s == v {
			return true
		}
	}
	return false
}

// StatusFor is the status a note's state calls for, or "" to leave the
// current one alone:
//
//   - `lifecycle: archived` is `archived`, and `lifecycle: superseded` is
//     `superseded`;
//   - a note whose `consolidated_into` names a lesson is `merged`;
//   - a note on the lifecycle axis that carried one of those three words and
//     has left that state — revived, un-stamped — is `active` again. A note
//     with no `lifecycle` at all keeps whatever it says: in a project a person
//     writes `status: superseded` on a plan that was overtaken, and that is
//     the record, not a contradiction;
//   - anything else (`dormant`, `pinned`, a draft, a tracker's word) is the
//     note's own business and left as it is.
func StatusFor(lifecycle, current string, consolidated bool) string {
	lc := strings.ToLower(strings.TrimSpace(lifecycle))
	switch {
	case lc == "archived":
		return StatusArchived
	case lc == "superseded":
		return StatusSuperseded
	case consolidated:
		return StatusMerged
	case lc != "" && IsStateStatus(current):
		return StatusActive
	}
	return ""
}

// SyncStatus is text with its `status` set to what its state calls for
// (StatusFor), in place. A note with no frontmatter, no `status` line, or a
// status that already agrees comes back unchanged: a missing status is the
// card-shape gate's to report, and a line added here would land out of the
// card's order.
func SyncStatus(text string) string {
	if !strings.HasPrefix(text, "---\n") {
		return text
	}
	end := strings.Index(text[4:], "\n---")
	if end < 0 {
		return text
	}
	end += 4
	lines := strings.Split(text[4:end], "\n")
	statusAt := -1
	var lifecycle, current string
	consolidated := false
	for i, line := range lines {
		if line == "" || strings.ContainsRune(" \t#-", rune(line[0])) {
			continue
		}
		key, val, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		val = strings.Trim(strings.TrimSpace(val), `"'`)
		switch strings.TrimSpace(key) {
		case "status":
			if statusAt < 0 {
				statusAt, current = i, val
			}
		case "lifecycle":
			if lifecycle == "" {
				lifecycle = val
			}
		case "consolidated_into":
			// A flow list, a link, or a block list whose items follow: anything
			// but an empty value or an empty list names a lesson.
			if val != "" && val != "[]" {
				consolidated = true
			} else if i+1 < len(lines) && strings.HasPrefix(strings.TrimSpace(lines[i+1]), "- ") {
				consolidated = true
			}
		}
	}
	if statusAt < 0 {
		return text
	}
	want := StatusFor(lifecycle, current, consolidated)
	if want == "" || want == strings.ToLower(current) {
		return text
	}
	lines[statusAt] = "status: " + want
	return text[:4] + strings.Join(lines, "\n") + text[end:]
}
