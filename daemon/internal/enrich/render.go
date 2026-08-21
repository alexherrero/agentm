package enrich

import (
	"fmt"
	"strings"
	"time"
)

// Turning a response back into a note on disk.
//
// This is where the review queue stops being a directory and becomes a query.
// A low-confidence enrichment lands in its class folder like any other, carrying
// `status: unfiled` and the number that made it low — so "what needs review" is
// a search rather than a place, and nothing has to be moved back out of anywhere
// once it is judged.

// ConfidenceFloor is the score below which an enrichment is filed for review
// rather than marked active.
//
// A note landing `unfiled` is not a rejection: it is fully indexed, fully
// searchable, and carries a rank penalty. The distinction that matters is
// between "the system is unsure" and "the system dropped it", and the second
// never happens here.
const ConfidenceFloor = 0.6

// RenderNote turns an enriched response into the bytes that go on disk.
//
// Frontmatter is written in a fixed field order rather than whatever order a map
// iterates. Two enrichments of the same note that differed only in key order
// would show up as a diff in git and as a change to anything hashing the file,
// which would make every review of the corpus's history noisier for no reason.
func RenderNote(r Response) string {
	var b strings.Builder
	b.WriteString("---\n")
	writeScalar(&b, "title", r.Title)
	writeScalar(&b, "type", r.Type)
	writeScalar(&b, "altitude", r.Altitude)
	writeScalar(&b, "status", StatusFor(r.Confidence))
	fmt.Fprintf(&b, "confidence: %.2f\n", r.Confidence)
	writeList(&b, "tags", r.Tags)
	writeList(&b, "aliases", r.Aliases)
	if r.Summary != "" {
		writeScalar(&b, "summary", r.Summary)
	}
	writeScalar(&b, "updated", time.Now().UTC().Format("2006-01-02"))
	writeScalar(&b, "enriched_by", PassVersion)
	b.WriteString("---\n\n")
	b.WriteString(strings.TrimRight(r.Body, "\n"))
	b.WriteString("\n")
	return b.String()
}

// StatusFor is the lifecycle status an enrichment earns.
//
// Above the floor a note is `active`: the pass distilled it, the deterministic
// gates agreed, and a judge found nothing invented. Below it the note is
// `unfiled` — the same state capture leaves an unattended note in, and the state
// the review queue is a query over.
func StatusFor(confidence float64) string {
	if confidence >= ConfidenceFloor {
		return "active"
	}
	return "unfiled"
}

func writeScalar(b *strings.Builder, key, value string) {
	if strings.TrimSpace(value) == "" {
		return
	}
	fmt.Fprintf(b, "%s: %s\n", key, yamlScalar(value))
}

func writeList(b *strings.Builder, key string, values []string) {
	if len(values) == 0 {
		return
	}
	quoted := make([]string, 0, len(values))
	for _, v := range values {
		if strings.TrimSpace(v) != "" {
			quoted = append(quoted, yamlScalar(v))
		}
	}
	if len(quoted) == 0 {
		return
	}
	fmt.Fprintf(b, "%s: [%s]\n", key, strings.Join(quoted, ", "))
}

// yamlScalar quotes a value when it would otherwise change meaning.
//
// A title beginning with `#` is a comment, one containing `: ` is a mapping, and
// one that reads as `true` or a number stops being a string. The corpus has
// notes titled exactly these things, because the corpus is largely about
// software.
func yamlScalar(s string) string {
	s = strings.TrimSpace(s)
	needsQuote := s == "" ||
		strings.ContainsAny(s, ":#[]{}&*!|>%@`\"'\n") ||
		strings.HasPrefix(s, "-") ||
		looksScalar(s)
	if !needsQuote {
		return s
	}
	return `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`, "\n", " ").Replace(s) + `"`
}

func looksScalar(s string) bool {
	switch strings.ToLower(s) {
	case "true", "false", "null", "yes", "no", "on", "off", "~":
		return true
	}
	for _, r := range s {
		if (r < '0' || r > '9') && r != '.' && r != '-' && r != '+' && r != 'e' {
			return false
		}
	}
	return true
}
