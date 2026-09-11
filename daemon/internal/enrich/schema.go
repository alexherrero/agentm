package enrich

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
)

// What the model is asked to return, and the gate that holds it to it.
//
// # Why a struct and not free-form markdown
//
// Enrichment's product is the whole note, which makes "return me the note"
// the obvious prompt and the wrong one. A model handed that returns a file, and
// then every downstream gate is a parser guessing which part of the file was the
// title and which the body. Asking for fields means the gates compare fields.
//
// # Why the enum comes from the contract at runtime
//
// The six memory types live in `standards/storage-rules.md`, which the operator
// edits and the daemon reads on the next capture with no rebuild. A validator
// with the types compiled into it would accept a retired one and reject a new
// one until somebody shipped a binary, which is the exact arrangement part 1
// existed to end.

// Response is the shape enrichment asks for (agentm-vault § Dreaming: one
// prompt, two shapes).
//
// It no longer carries `altitude`: the deep pass drops it. And it never
// carries `why` — see strippedFields.
type Response struct {
	// Title is the note's title. Corrected, not invented — a note that already
	// has a good one gets it back unchanged.
	Title string `json:"title"`
	// Slug is the filename stem. Only ever applied while nothing links to the
	// note; see the while-unlinked rule.
	Slug string `json:"slug,omitempty"`
	// Type is one of the contract's memory types.
	Type string `json:"type"`
	// Tags and Aliases are retrieval surface.
	Tags    []string `json:"tags,omitempty"`
	Aliases []string `json:"aliases,omitempty"`
	// Summary is one sentence saying what the card is for.
	Summary string `json:"summary,omitempty"`
	// Related are neighbours the model judged to bear on the card, by id —
	// chosen from the five the prompt offered and never from anywhere else.
	// Compose keeps only ids that were offered, so a link cannot be invented.
	Related []string `json:"related,omitempty"`
	// ImportanceProposed is the model's 1–10 against the contract's rubric.
	// Zero means none was proposed, which is what a light pass returns.
	ImportanceProposed int `json:"importance_proposed,omitempty"`
	// Body is prose to add below what the session wrote, under a dated
	// `## Added by dreaming` heading. Empty when there is nothing worth adding.
	// It is never a rewrite: the captured text is the evidence and is kept
	// byte for byte (compose.go).
	Body string `json:"body"`
	// Confidence is the model's own account of whether the card is a durable
	// memory worth filing and its fields right, and it is the field the review
	// queue is a query over. Below the floor the card stays `unfiled`, listed
	// for the operator; a second verdict below it sinks the card to `dormant`.
	Confidence float64 `json:"confidence"`
}

// strippedFields are fields a model may return out of habit and the pass
// never writes. They are removed before the strict decode rather than failing
// it, so a response that is otherwise good is not thrown away over a field that
// was never going to land.
//
//   - `why` is what was happening when a card was kept, written by whoever
//     kept it. No pass was there, and a reason guessed from the note reads
//     exactly like a real one. The prompt never asks for it; this is the gate
//     that holds when the model offers one anyway.
//   - `altitude` is the axis the deep pass dropped (agentm-vault § Dreaming).
var strippedFields = []string{"why", "altitude"}

// decodeResponse extracts the response object, strips what the pass never
// writes, and decodes the rest strictly. The second value names what was
// stripped, for a caller that reports it.
func decodeResponse(raw string) (Response, []string, error) {
	var r Response
	obj, err := extractJSON(raw)
	if err != nil {
		return r, nil, err
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal([]byte(obj), &fields); err != nil {
		return r, nil, fmt.Errorf("enrich: the response is not a JSON object: %w", err)
	}
	var stripped []string
	for k := range fields {
		for _, s := range strippedFields {
			if strings.EqualFold(k, s) {
				delete(fields, k)
				stripped = append(stripped, k)
			}
		}
	}
	clean, err := json.Marshal(fields)
	if err != nil {
		return r, nil, err
	}
	// Unknown fields are refused rather than ignored. A model that invents a
	// field is a model that misunderstood the task, and silently dropping it
	// hides the misunderstanding until it shows up as a missing one.
	dec := json.NewDecoder(strings.NewReader(string(clean)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&r); err != nil {
		return r, stripped, fmt.Errorf("enrich: response is not the expected shape: %w", err)
	}
	return r, stripped, nil
}

// Schema is the post-gate that holds the model to the contract.
type Schema struct {
	// IsType is `rules.IsMemoryType` — passed in rather than imported, so the
	// enum is whatever the contract says at the moment of the call rather than
	// whatever was compiled in.
	IsType func(string) bool
	// TypesSorted renders the enum for an error message. A rejection that says
	// "not a valid type" without saying what the valid ones are sends the reader
	// to the source.
	TypesSorted func() []string
	// MaxTags and MaxAliases cap the retrieval surface. Both columns rank above
	// body, so they are scarce rather than free, and a model asked for tags will
	// happily produce thirty.
	MaxTags    int
	MaxAliases int
}

// DefaultSchema is the shipped configuration.
func DefaultSchema(isType func(string) bool, typesSorted func() []string) *Schema {
	return &Schema{
		IsType: isType, TypesSorted: typesSorted,
		MaxTags: 8, MaxAliases: 6,
	}
}

func (g *Schema) Name() string { return "schema" }

// slugRe is the shape a filename stem may take. Lower case, digits and single
// hyphens — the same shape `capture.slugify` produces, because a slug that
// round-trips differently through the two writers is a rename waiting to happen.
var slugRe = regexp.MustCompile(`^[a-z0-9]+(-[a-z0-9]+)*$`)

func (g *Schema) Check(_ context.Context, _ Request, body string) error {
	r, _, err := decodeResponse(body)
	if err != nil {
		return fmt.Errorf("%w: %v", ErrNotEligible, err)
	}
	return g.Validate(r)
}

// Validate checks one parsed response. Exported because the write path needs the
// same answer the gate gives, and two copies of a validator is how they diverge.
func (g *Schema) Validate(r Response) error {
	if strings.TrimSpace(r.Title) == "" {
		return fmt.Errorf("%w: no title", ErrNotEligible)
	}
	// An empty body is a fine answer now: it is what the pass adds below the
	// card, and most short cards need nothing added.
	if g.IsType != nil && !g.IsType(r.Type) {
		known := ""
		if g.TypesSorted != nil {
			known = "; the contract defines " + strings.Join(g.TypesSorted(), ", ")
		}
		return fmt.Errorf("%w: %q is not a memory type%s", ErrNotEligible, r.Type, known)
	}
	if r.ImportanceProposed != 0 && (r.ImportanceProposed < 1 || r.ImportanceProposed > 10) {
		return fmt.Errorf("%w: importance_proposed %d is outside 1 to 10",
			ErrNotEligible, r.ImportanceProposed)
	}
	if len(r.Related) > MaxRelated {
		return fmt.Errorf("%w: %d related, over the %d neighbours offered",
			ErrNotEligible, len(r.Related), MaxRelated)
	}
	if r.Slug != "" && !slugRe.MatchString(r.Slug) {
		return fmt.Errorf("%w: slug %q is not a lower-case hyphenated stem",
			ErrNotEligible, r.Slug)
	}
	if r.Confidence < 0 || r.Confidence > 1 {
		return fmt.Errorf("%w: confidence %v is outside [0,1]", ErrNotEligible,
			r.Confidence)
	}
	if g.MaxTags > 0 && len(r.Tags) > g.MaxTags {
		return fmt.Errorf("%w: %d tags, over the cap of %d — the tag column ranks "+
			"above body and is scarce rather than free", ErrNotEligible,
			len(r.Tags), g.MaxTags)
	}
	if g.MaxAliases > 0 && len(r.Aliases) > g.MaxAliases {
		return fmt.Errorf("%w: %d aliases, over the cap of %d", ErrNotEligible,
			len(r.Aliases), g.MaxAliases)
	}
	for _, list := range [][]string{r.Tags, r.Aliases} {
		for _, v := range list {
			if strings.TrimSpace(v) == "" {
				return fmt.Errorf("%w: an empty tag or alias", ErrNotEligible)
			}
		}
	}
	return nil
}

// ParseResponse decodes a model response into the struct, with the same
// strictness the gate applies and the same fields stripped.
func ParseResponse(raw string) (Response, error) {
	r, _, err := decodeResponse(raw)
	return r, err
}

// MaxRelated is how many neighbours the deep pass is offered, and so the most
// `related` it may return.
const MaxRelated = 5
