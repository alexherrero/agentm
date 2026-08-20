package extract

import (
	"regexp"
	"strings"
)

// HeaderChunk is one section of a note, with the heading ancestry that led to it.
//
// The ancestry is the point. A match inside a long document should tell you
// *where* it matched — `Architecture > Ingestion Pipeline` — rather than naming
// the file and leaving you to find the paragraph. This is the direct fix for the
// measured failure where a 38KB design document took all five top slots from a
// 1.1KB focused note on term-frequency mass alone: the long document does not
// stop matching, it stops matching *as a whole*.
type HeaderChunk struct {
	// HeaderPath is the heading ancestry, joined with " > ". Empty for content
	// that appears before the note's first heading.
	HeaderPath string
	// Content is the section's text, including its own heading line, so a chunk
	// read on its own still says what it is about.
	Content string
}

var (
	// ATX headings only. Setext (`Title\n=====`) is not used anywhere in this
	// corpus and supporting it would mean lookahead on every line for a form
	// nothing writes.
	headingRe = regexp.MustCompile(`^(#{1,6})\s+(.*?)\s*$`)

	// A fence opener or closer. Tracked because a `#` inside a fenced block is a
	// comment or a shell prompt, not a heading — and a chunker that split on one
	// would cut a code block in half and label the remainder as a section.
	fenceRe = regexp.MustCompile("^\\s{0,3}(```|~~~)")
)

// HeaderChunks splits a note body along markdown heading boundaries.
//
// A note with no headings returns a single chunk with an empty `HeaderPath` —
// which is what keeps this compatible with everything that came before it, since
// the overwhelming majority of captures are a paragraph with no structure at all.
func HeaderChunks(body string) []HeaderChunk {
	lines := strings.Split(body, "\n")

	var (
		chunks  []HeaderChunk
		stack   []string // heading text by depth-1
		current strings.Builder
		path    string
		inFence bool
	)

	flush := func() {
		text := strings.TrimRight(current.String(), "\n")
		current.Reset()
		if strings.TrimSpace(text) == "" {
			return
		}
		chunks = append(chunks, HeaderChunk{HeaderPath: path, Content: text})
	}

	for _, line := range lines {
		if fenceRe.MatchString(line) {
			inFence = !inFence
			current.WriteString(line)
			current.WriteString("\n")
			continue
		}
		if inFence {
			current.WriteString(line)
			current.WriteString("\n")
			continue
		}

		m := headingRe.FindStringSubmatch(line)
		if m == nil {
			current.WriteString(line)
			current.WriteString("\n")
			continue
		}

		// A heading ends the previous section and starts a new one.
		flush()

		depth := len(m[1])
		title := strings.TrimSpace(m[2])

		// Grow or truncate the stack to this depth. A jump from `#` straight to
		// `###` leaves the intermediate level empty rather than inventing one:
		// the ancestry should describe the document as written, and a synthesized
		// heading is a claim about structure the author did not make.
		for len(stack) < depth {
			stack = append(stack, "")
		}
		stack = stack[:depth]
		stack[depth-1] = title

		var parts []string
		for _, s := range stack {
			if s != "" {
				parts = append(parts, s)
			}
		}
		path = strings.Join(parts, " > ")

		current.WriteString(line)
		current.WriteString("\n")
	}
	flush()

	if len(chunks) == 0 {
		return nil
	}
	return chunks
}
