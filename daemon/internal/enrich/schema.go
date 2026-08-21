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

// Response is the shape enrichment asks for.
type Response struct {
	// Title is the note's title. Corrected, not invented — a note that already
	// has a good one gets it back unchanged.
	Title string `json:"title"`
	// Slug is the filename stem. Only ever applied while nothing links to the
	// note; see the while-unlinked rule.
	Slug string `json:"slug,omitempty"`
	// Type is one of the contract's memory types.
	Type string `json:"type"`
	// Altitude is `canonical` or `artifact`.
	Altitude string `json:"altitude"`
	// Tags and Aliases are retrieval surface.
	Tags    []string `json:"tags,omitempty"`
	Aliases []string `json:"aliases,omitempty"`
	// Summary is present when the note is long enough to want one.
	Summary string `json:"summary,omitempty"`
	// Body is the distilled prose. The product.
	Body string `json:"body"`
	// Confidence is the model's own account of how sure it is, and it is the
	// field the review queue is a query over. Low confidence does not fail the
	// write — it lands the note `unfiled` with the number in frontmatter, which
	// is what "the review queue is a query" means in practice.
	Confidence float64 `json:"confidence"`
}

// Altitudes are the two values, and there are exactly two on purpose: the axis
// is "does this state something durable, or record a moment", and a third value
// would be a way to avoid answering.
var Altitudes = map[string]bool{"canonical": true, "artifact": true}

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
	var r Response
	obj, err := extractJSON(body)
	if err != nil {
		return fmt.Errorf("%w: %v", ErrNotEligible, err)
	}
	// Unknown fields are refused rather than ignored. A model that invents a
	// field is a model that misunderstood the task, and silently dropping it
	// hides the misunderstanding until it shows up as a missing one.
	dec := json.NewDecoder(strings.NewReader(obj))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&r); err != nil {
		return fmt.Errorf("%w: the response is not the expected shape: %v",
			ErrNotEligible, err)
	}
	return g.Validate(r)
}

// Validate checks one parsed response. Exported because the write path needs the
// same answer the gate gives, and two copies of a validator is how they diverge.
func (g *Schema) Validate(r Response) error {
	if strings.TrimSpace(r.Title) == "" {
		return fmt.Errorf("%w: no title", ErrNotEligible)
	}
	if strings.TrimSpace(r.Body) == "" {
		return fmt.Errorf("%w: no body — enrichment's product is the note, and a "+
			"response with nothing in it is a failed call rather than a short one",
			ErrNotEligible)
	}
	if g.IsType != nil && !g.IsType(r.Type) {
		known := ""
		if g.TypesSorted != nil {
			known = "; the contract defines " + strings.Join(g.TypesSorted(), ", ")
		}
		return fmt.Errorf("%w: %q is not a memory type%s", ErrNotEligible, r.Type, known)
	}
	if !Altitudes[strings.ToLower(r.Altitude)] {
		return fmt.Errorf("%w: altitude %q is neither canonical nor artifact",
			ErrNotEligible, r.Altitude)
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
// strictness the gate applies.
func ParseResponse(raw string) (Response, error) {
	var r Response
	obj, err := extractJSON(raw)
	if err != nil {
		return r, err
	}
	dec := json.NewDecoder(strings.NewReader(obj))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&r); err != nil {
		return r, fmt.Errorf("enrich: response is not the expected shape: %w", err)
	}
	return r, nil
}
