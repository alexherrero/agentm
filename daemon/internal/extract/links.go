package extract

import (
	"regexp"
	"strings"
)

// Link is one outbound reference from a note.
type Link struct {
	// Target is the reference as written, with any display text and anchor
	// stripped — `[[capture|the capture path]]` targets `capture`.
	Target string
	// Text is what the reader sees. For a bare wikilink that is the target
	// itself; for a piped or markdown link it is the display text.
	Text string
	// Context is the surrounding line, trimmed. A backlink without context tells
	// you two notes are connected and nothing about how, which is most of what
	// makes a backlink worth having.
	Context string
	// Wiki records which form the link was written in. Kept because the two
	// resolve differently: a wikilink resolves by basename against the whole
	// vault, a markdown link is a path relative to the linking note.
	Wiki bool
}

var (
	// `[[target]]`, `[[target|display]]`, `[[target#anchor]]`.
	wikiLinkRe = regexp.MustCompile(`\[\[([^\]\[|#]+)(?:#([^\]\[|]*))?(?:\|([^\]\[]*))?\]\]`)

	// `[display](target)`. Bare autolinks and image embeds are deliberately not
	// matched: an image is not a reference between notes, and an autolink is a
	// URL rather than a vault path.
	mdLinkRe = regexp.MustCompile(`(^|[^!])\[([^\]\[]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)`)

	// A target that leaves the vault. Recorded by nobody: the backlink index is
	// about the shape of the corpus, and an external URL is not part of it.
	externalRe = regexp.MustCompile(`^(?:[a-z][a-z0-9+.-]*:|//)`)
)

// Links pulls every outbound reference out of a note body.
//
// Both forms, because the corpus uses both: Obsidian writes wikilinks and
// everything generated writes markdown links. A extractor that read one form
// would report half the graph, and half a graph is worse than none — it looks
// complete.
//
// Fenced code is skipped. A link inside a code block is a sample, not a
// reference, and indexing it would connect a page to whatever its examples
// happen to mention.
func Links(body string) []Link {
	var out []Link
	seen := map[string]bool{}

	inFence := false
	for _, line := range strings.Split(body, "\n") {
		if fenceRe.MatchString(line) {
			inFence = !inFence
			continue
		}
		if inFence {
			continue
		}

		context := strings.TrimSpace(line)

		for _, m := range wikiLinkRe.FindAllStringSubmatch(line, -1) {
			target := strings.TrimSpace(m[1])
			if target == "" || externalRe.MatchString(target) {
				continue
			}
			text := strings.TrimSpace(m[3])
			if text == "" {
				text = target
			}
			key := "w\x00" + target + "\x00" + text
			if seen[key] {
				continue
			}
			seen[key] = true
			out = append(out, Link{Target: target, Text: text, Context: context, Wiki: true})
		}

		for _, m := range mdLinkRe.FindAllStringSubmatch(line, -1) {
			target := strings.TrimSpace(m[3])
			if target == "" || externalRe.MatchString(target) || strings.HasPrefix(target, "#") {
				continue
			}
			target = strings.SplitN(target, "#", 2)[0]
			if target == "" {
				continue
			}
			text := strings.TrimSpace(m[2])
			key := "m\x00" + target + "\x00" + text
			if seen[key] {
				continue
			}
			seen[key] = true
			out = append(out, Link{Target: target, Text: text, Context: context})
		}
	}
	return out
}

// ResolveTarget picks which known path a link refers to.
//
// The disambiguation the design calls for: `[[capture]]` is ambiguous when two
// files share a basename, and the rule is longest-matching-path-suffix. A target
// written with more path than a bare name is more specific, and the candidate
// that matches more of it is the one meant.
//
// `known` is every indexable path in the vault, and `from` is the linking note's
// own path — used only to break a tie, since a link is far more likely to mean
// the sibling than the far-away file with the same name.
//
// Returns "" when nothing matches. That is a real state and the caller records
// it rather than dropping it: a dangling link is a fact about the corpus, and it
// is exactly what the stub synthesis in a later part reads.
func ResolveTarget(target string, from string, known []string) string {
	target = strings.TrimSuffix(strings.TrimSpace(target), ".md")
	if target == "" {
		return ""
	}
	norm := strings.ToLower(strings.TrimPrefix(target, "./"))

	var best string
	var bestScore int
	for _, path := range known {
		stripped := strings.ToLower(strings.TrimSuffix(path, ".md"))
		var score int
		switch {
		case stripped == norm:
			score = 1000
		case strings.HasSuffix(stripped, "/"+norm):
			// How much of the candidate the target accounts for. A target naming
			// two segments beats one naming a bare basename.
			score = 100 + strings.Count(norm, "/")
		default:
			continue
		}
		if score > bestScore {
			best, bestScore = path, score
			continue
		}
		if score == bestScore && best != "" && siblingDistance(from, path) < siblingDistance(from, best) {
			best = path
		}
	}
	return best
}

// siblingDistance counts how far apart two paths are in the tree. Used only to
// break a tie between two equally-specific candidates.
func siblingDistance(from, to string) int {
	a := strings.Split(from, "/")
	b := strings.Split(to, "/")
	shared := 0
	for shared < len(a)-1 && shared < len(b)-1 && a[shared] == b[shared] {
		shared++
	}
	return (len(a) - 1 - shared) + (len(b) - 1 - shared)
}
