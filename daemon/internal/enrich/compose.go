package enrich

import (
	"fmt"
	"strings"
	"time"
)

// Composing the note a judgment writes.
//
// The card's text is the evidence and is never rewritten (agentm-vault
// § Dreaming, "Dreaming enriches a card beyond what the session had"). What a
// judgment changes is the frontmatter above it; what a deep pass adds goes
// under a dated `## Added by dreaming` heading below it; and everything the
// session wrote sits between the two byte for byte. That is checked here on
// every write, not assumed: a composition that would change one byte of the
// captured text is refused.
//
// Why additive and never a rewrite: a rewrite that reads well is how residue
// survived the last purge, and the captured text is the only record of what the
// session actually said.

// DreamingHeading opens the section the deep pass adds. The date follows it in
// parentheses, so a reader sees when the machine wrote what it wrote.
const DreamingHeading = "## Added by dreaming"

// splitNote returns the frontmatter block — both fences and the newline after
// the closing one — and everything after it. A note with no frontmatter is all
// body.
func splitNote(raw string) (frontmatter, body string) {
	if !strings.HasPrefix(raw, "---\n") {
		return "", raw
	}
	end := strings.Index(raw[4:], "\n---")
	if end < 0 {
		return "", raw
	}
	close := 4 + end + len("\n---")
	if nl := strings.IndexByte(raw[close:], '\n'); nl >= 0 {
		close += nl + 1
	} else {
		close = len(raw)
	}
	return raw[:close], raw[close:]
}

// splitDreaming finds the section a deep pass added, if the body has one. The
// section runs from its heading to the next heading of level one or two, or to
// the end; text after it — something the operator wrote below it — is kept as
// `after`.
func splitDreaming(body string) (captured, section, after string) {
	idx := -1
	if strings.HasPrefix(body, DreamingHeading) {
		idx = 0
	} else if i := strings.Index(body, "\n"+DreamingHeading); i >= 0 {
		idx = i + 1
	}
	if idx < 0 {
		return body, "", ""
	}
	captured, rest := body[:idx], body[idx:]
	lineEnd := strings.IndexByte(rest, '\n')
	if lineEnd < 0 {
		return captured, rest, ""
	}
	tail := rest[lineEnd:]
	end := -1
	for _, marker := range []string{"\n# ", "\n## "} {
		if j := strings.Index(tail, marker); j >= 0 && (end < 0 || j < end) {
			end = j
		}
	}
	if end < 0 {
		return captured, rest, ""
	}
	cut := lineEnd + end + 1
	return captured, rest[:cut], rest[cut:]
}

// addition turns the model's body into the section's text: trimmed, and with
// any heading of level one or two stepped down to three, so the section's own
// boundary stays the only `## ` it contains and the next deep pass finds where
// it ends.
func addition(body string) string {
	body = strings.TrimSpace(body)
	if body == "" {
		return ""
	}
	lines := strings.Split(body, "\n")
	for i, l := range lines {
		if strings.HasPrefix(l, "# ") || strings.HasPrefix(l, "## ") {
			lines[i] = "### " + strings.TrimLeft(l, "# ")
		}
	}
	return strings.Join(lines, "\n")
}

// relatedIDs keeps the `related` ids that name an offered neighbour, in the
// model's order, without duplicates. An id the prompt did not offer is dropped
// — this is the guard that makes "chosen from the neighbours, never invented"
// true whatever the model returned.
func relatedIDs(ids []string, offered []Neighbour) []string {
	allowed := map[string]bool{}
	for _, n := range offered {
		allowed[n.ID] = true
	}
	seen := map[string]bool{}
	var out []string
	for _, id := range ids {
		id = strings.TrimSpace(id)
		id = strings.TrimSuffix(strings.TrimPrefix(id, "[["), "]]")
		id = strings.TrimSuffix(id, ".md")
		if i := strings.LastIndexByte(id, '/'); i >= 0 {
			id = id[i+1:]
		}
		if allowed[id] && !seen[id] {
			seen[id] = true
			out = append(out, id)
		}
	}
	return out
}

// Compose builds the note a judgment writes from the note as it stood.
//
// The deep pass writes every field it returned and replaces any section an
// earlier deep pass added with its own (or with none, when it had nothing to
// add); the machine's section is the machine's to regenerate. The light pass
// moves summary, tags, related and confidence; title and type only at or above
// the floor; importance not at all; and it leaves the body exactly as it was.
//
// It refuses rather than writes when the captured text would not survive byte
// for byte.
func Compose(previous string, r Response, s Stamp, depth Depth, offered []Neighbour) (string, FilingVerdict, error) {
	_, prevBody := splitNote(previous)
	captured, oldSection, after := splitDreaming(prevBody)
	r.Related = relatedIDs(r.Related, offered)

	when := s.At
	if when.IsZero() {
		when = time.Now()
	}

	var section string
	switch depth {
	case DepthDeep:
		if add := addition(r.Body); add != "" {
			section = fmt.Sprintf("%s (%s)\n\n%s\n", DreamingHeading,
				when.UTC().Format("2006-01-02"), add)
		}
	default:
		// The light pass: what ranks the card does not move below the floor,
		// importance does not move at all, and the body is left as it was.
		section = oldSection
		if r.Confidence < Floor(s.ConfidenceFloor) {
			if t := frontmatterValue(previous, "title"); t != "" {
				r.Title = t
			}
			if t := frontmatterValue(previous, "type"); t != "" {
				r.Type = t
			}
		}
		r.ImportanceProposed = 0
		r.Body = ""
	}

	verdict := VerdictFor(previous, r, s.ConfidenceFloor)
	body := joinBody(captured, section, after)
	next := CarryProvenance(previous, RenderFrontmatter(r, s, verdict)+separator(body)+body)

	// The guard, checked on the bytes about to be written rather than on the
	// intent: everything the session wrote is still there, unchanged, directly
	// under the frontmatter.
	_, nextBody := splitNote(next)
	if !strings.HasPrefix(strings.TrimPrefix(nextBody, separator(body)), captured) {
		return "", verdict, fmt.Errorf("enrich: refusing to write — composing the " +
			"note would change the text the session wrote")
	}
	return next, verdict, nil
}

// joinBody puts the captured text, the section and whatever followed the old
// section back together, adding only the blank line a heading needs and never
// touching the captured bytes.
func joinBody(captured, section, after string) string {
	if section == "" {
		return captured + after
	}
	var gap string
	switch {
	case captured == "" || strings.HasSuffix(captured, "\n\n"):
	case strings.HasSuffix(captured, "\n"):
		gap = "\n"
	default:
		gap = "\n\n"
	}
	if after != "" && !strings.HasSuffix(section, "\n\n") {
		section += "\n"
	}
	return captured + gap + section + after
}

// separator is what goes between the frontmatter and a body that does not
// already begin with a blank line: a note with no frontmatter of its own gets
// one, so its first line is not read as part of the block.
func separator(body string) string {
	if body == "" || strings.HasPrefix(body, "\n") {
		return ""
	}
	return "\n"
}
