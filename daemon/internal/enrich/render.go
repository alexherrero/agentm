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

// DefaultConfidenceFloor is the score below which an enrichment is filed for
// review rather than marked active, when no contract answers.
//
// The contract's `thresholds.low_confidence` is the real number and a Stamp
// carries it, because the design wants a threshold moved by editing the
// contract rather than by a release. This constant is what a pass uses when
// the contract could not be read at all — it matches what the packaged
// contract says, so an unreadable contract does not quietly loosen the bar.
//
// A note landing `unfiled` is not a rejection: it is fully indexed, fully
// searchable, and carries a rank penalty. The distinction that matters is
// between "the system is unsure" and "the system dropped it", and the second
// never happens here.
const DefaultConfidenceFloor = 0.65

// ConfidenceFloorThreshold is the contract key holding the real floor.
const ConfidenceFloorThreshold = "low_confidence"

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
	// ConfidenceFloor is the contract's `thresholds.low_confidence` as it read
	// at the moment of the judgment. Zero means no contract answered, and the
	// packaged default stands in — never "everything clears the bar".
	ConfidenceFloor float64
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
	return RenderFrontmatter(r, s, VerdictFor("", r, s.ConfidenceFloor)) + "\n" +
		strings.TrimRight(r.Body, "\n") + "\n"
}

// RenderFrontmatter is the frontmatter block a judgment writes, both fences
// included, ending in the closing `---` and its newline. The body is the
// caller's: Compose puts the card's own text under it byte for byte.
//
// `altitude` is gone (agentm-vault § Dreaming: the deep pass drops it), and
// `why` was never here — only a writer that knows writes it, and CarryProvenance
// carries the one the card came with.
func RenderFrontmatter(r Response, s Stamp, v FilingVerdict) string {
	var b strings.Builder
	b.WriteString("---\n")
	writeScalar(&b, "title", r.Title)
	writeScalar(&b, "type", r.Type)
	writeScalar(&b, "status", v.Status)
	// A second verdict below the floor sinks the card in place: it leaves the
	// queue, stays on disk and in search at the dormant rank, and a genuine
	// recall brings it back (agentm-vault § Capture, "the 'no' is a demotion").
	when := s.At
	if when.IsZero() {
		when = time.Now()
	}
	if v.Sank {
		writeScalar(&b, "lifecycle", "dormant")
		writeScalar(&b, "lifecycle_since", when.UTC().Format("2006-01-02"))
	}
	fmt.Fprintf(&b, "confidence: %.2f\n", r.Confidence)
	// The categorical twin of the number, in the vocabulary every writer
	// shares (filing v2): the needs-review reading selects on it without
	// knowing this pass's floor.
	writeScalar(&b, "filing_confidence", v.FilingConfidence)
	// Both halves from one proposal. `importance` is what the operator reads
	// and edits; writing it equal to the proposal is what makes a later edit
	// legible as theirs, and CarryProvenance puts back an `importance` that
	// already differed. Zero means nothing was proposed — the light pass — and
	// both are carried across as they stood.
	if r.ImportanceProposed > 0 {
		fmt.Fprintf(&b, "importance: %d\n", r.ImportanceProposed)
		fmt.Fprintf(&b, "importance_proposed: %d\n", r.ImportanceProposed)
	}
	writeList(&b, "tags", r.Tags)
	writeList(&b, "aliases", r.Aliases)
	// Wikilinks in a quoted flow list, the shape the capture door writes: a
	// bare `[[a]]` is a nested YAML sequence rather than a link.
	if len(r.Related) > 0 {
		links := make([]string, 0, len(r.Related))
		for _, id := range r.Related {
			links = append(links, `"[[`+id+`]]"`)
		}
		fmt.Fprintf(&b, "related: [%s]\n", strings.Join(links, ", "))
	}
	if r.Summary != "" {
		writeScalar(&b, "summary", r.Summary)
	}
	// `updated` takes the stamp's moment when there is one, so the date the note
	// claims and the timestamp the ledger holds describe the same event rather
	// than two clock reads a few microseconds apart.
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
	b.WriteString("---\n")
	return b.String()
}

// FilingVerdict is what a judgment decides about where a card stands.
type FilingVerdict struct {
	// Status is `active` at or above the floor and `unfiled` below it.
	Status string
	// FilingConfidence is `high` or `low`, the categorical twin.
	FilingConfidence string
	// Sank is a second verdict below the floor: the card moves to
	// `lifecycle: dormant`.
	Sank bool
}

// VerdictFor is session 2's verdict, unchanged (agentm-vault § Dreaming): at or
// above the floor a card lands `active` at `filing_confidence: high`; below it
// stays `unfiled` and is listed; on a second verdict below the floor it sinks
// to `dormant`.
//
// "A second verdict" is read from the note as it stood: it carries an
// enrichment stamp and is `unfiled`, which a judged card only is when the last
// judgment was below the floor. That happens only when the body, the prompt or
// the contract changed, because an unchanged card is never judged twice.
//
// Three kinds never sink, because the lifecycle contract says so and a filing
// verdict does not get to overrule it: a `pinned` card, and the two rule types,
// `preference` and `convention` — a rule nobody has read in a while is still
// the rule. They stay `unfiled` and listed for the operator instead.
func VerdictFor(previous string, r Response, floor float64) FilingVerdict {
	if r.Confidence >= Floor(floor) {
		return FilingVerdict{Status: "active", FilingConfidence: "high"}
	}
	v := FilingVerdict{Status: "unfiled", FilingConfidence: "low"}
	judgedBelow := strings.TrimSpace(frontmatterValue(previous, "enriched_at")) != "" &&
		frontmatterValue(previous, "status") == "unfiled"
	if !judgedBelow {
		return v
	}
	if frontmatterValue(previous, "lifecycle") == "pinned" {
		return v
	}
	for _, t := range []string{r.Type, frontmatterValue(previous, "type")} {
		if t == "preference" || t == "convention" {
			return v
		}
	}
	v.Sank = true
	return v
}

// StatusFor is the lifecycle status an enrichment earns.
//
// Above the floor a note is `active`: the pass distilled it, the deterministic
// gates agreed, and a judge found nothing invented. Below it the note is
// `unfiled` — the same state capture leaves an unattended note in, and the state
// the review queue is a query over.
func StatusFor(confidence, floor float64) string {
	if confidence >= Floor(floor) {
		return "active"
	}
	return "unfiled"
}

// Floor is the floor a caller should apply: the contract's number when it
// answered, the packaged default when it did not. A zero or negative value
// means "no contract answered" — a floor of zero would file everything
// active, which is the one reading that must never come from silence.
func Floor(floor float64) float64 {
	if floor > 0 {
		return floor
	}
	return DefaultConfidenceFloor
}

// FilingConfidenceFor is the write-time confidence stamp an enrichment earns —
// `high` at or above the floor, `low` below it. Two values on purpose: the
// floor is the one judgment this pass makes about its own number, and a third
// band would be a threshold nobody measured.
func FilingConfidenceFor(confidence, floor float64) string {
	if confidence >= Floor(floor) {
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
