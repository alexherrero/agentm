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

// StampFormat is the layout `enriched_at` is written in.
//
// The same layout the index stores `captured` in, and for the same reason: a
// lexicographic compare over these strings is also a chronological one, so a
// query for "everything enriched before the prompt changed" is a plain range
// scan rather than a parse of fifteen thousand values.
const StampFormat = "2006-01-02T15:04:05Z"

// Stamp is the durable record of a judgment, written into the note it judged.
//
// Three fields, and all three are load-bearing. The version says which pass
// produced this body. The rules hash says which filing contract it was judged
// under, so a contract edit can name exactly the population it invalidated
// rather than the whole corpus. The time says when.
//
// This is the one stamp that lives in the file rather than in the coverage
// ledger, and the reason is that it is a durable judgment about the note rather
// than machine state about a queue. Everything else a stage knows is a cache and
// belongs in one; this survives losing the cache, and it is what a rebuild reads
// back.
type Stamp struct {
	// Version defaults to PassVersion when empty, because every real caller
	// wants the current pass and a forgotten field should not produce a note
	// that claims nothing wrote it.
	Version string
	// RulesHash is the filing contract this judgment was made under. Omitted
	// from the note when empty rather than written as a blank, because an empty
	// hash reads as "judged under no contract" and that is never true.
	RulesHash string
	// At is when. A zero time writes no `enriched_at` at all — a note that does
	// not know when it was enriched should say nothing rather than guess, and
	// leaving it out is what keeps a rendered note byte-identical across calls
	// when a test needs it to be.
	At time.Time
}

// RenderNote turns an enriched response into the bytes that go on disk.
//
// Frontmatter is written in a fixed field order rather than whatever order a map
// iterates. Two enrichments of the same note that differed only in key order
// would show up as a diff in git and as a change to anything hashing the file,
// which would make every review of the corpus's history noisier for no reason.
func RenderNote(r Response, s Stamp) string {
	var b strings.Builder
	b.WriteString("---\n")
	writeScalar(&b, "title", r.Title)
	writeScalar(&b, "type", r.Type)
	writeScalar(&b, "altitude", r.Altitude)
	writeScalar(&b, "status", StatusFor(r.Confidence))
	fmt.Fprintf(&b, "confidence: %.2f\n", r.Confidence)
	// The categorical twin of the number, in the vocabulary every writer
	// shares (filing v2): the needs-review reading selects on it without
	// knowing this pass's floor.
	writeScalar(&b, "filing_confidence", FilingConfidenceFor(r.Confidence))
	writeList(&b, "tags", r.Tags)
	writeList(&b, "aliases", r.Aliases)
	if r.Summary != "" {
		writeScalar(&b, "summary", r.Summary)
	}
	// `updated` takes the stamp's moment when there is one, so the date the note
	// claims and the timestamp the ledger holds describe the same event rather
	// than two clock reads a few microseconds apart.
	when := s.At
	if when.IsZero() {
		when = time.Now()
	}
	writeScalar(&b, "updated", when.UTC().Format("2006-01-02"))
	version := s.Version
	if version == "" {
		version = PassVersion
	}
	writeScalar(&b, "enriched_by", version)
	writeScalar(&b, "rules_hash", s.RulesHash)
	if !s.At.IsZero() {
		writeScalar(&b, "enriched_at", s.At.UTC().Format(StampFormat))
	}
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

// FilingConfidenceFor is the write-time confidence stamp an enrichment earns —
// `high` at or above the floor, `low` below it. Two values on purpose: the
// floor is the one judgment this pass makes about its own number, and a third
// band would be a threshold nobody measured.
func FilingConfidenceFor(confidence float64) string {
	if confidence >= ConfidenceFloor {
		return "high"
	}
	return "low"
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
