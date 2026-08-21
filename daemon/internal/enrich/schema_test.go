package enrich

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

// The contract's types, as a test states them. Deliberately not the real six —
// this gate's job is to enforce whatever the contract says, and hard-coding the
// live values here would make the test pass for a gate that ignored its input.
var testTypes = map[string]bool{"convention": true, "fact": true, "workflow": true}

func testSchema() *Schema {
	return DefaultSchema(
		func(v string) bool { return testTypes[v] },
		func() []string { return []string{"convention", "fact", "workflow"} },
	)
}

func good() Response {
	return Response{
		Title: "The staging gate", Type: "convention", Altitude: "canonical",
		Body:       "The staging gate runs before the deployment finishes.",
		Confidence: 0.9,
	}
}

func encode(t *testing.T, r Response) string {
	t.Helper()
	b, err := json.Marshal(r)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestAValidResponsePasses(t *testing.T) {
	if err := testSchema().Check(context.Background(), Request{}, encode(t, good())); err != nil {
		t.Errorf("a valid response was rejected: %v", err)
	}
}

// The enum comes from the contract at the moment of the call, not from a
// constant. A validator with the types compiled in would accept a retired one
// and reject a new one until somebody shipped a binary — the exact arrangement
// part 1 existed to end.
func TestARetiredTypeStopsBeingAcceptedWithoutARebuild(t *testing.T) {
	g := testSchema()
	r := good()
	r.Type = "workflow"
	if err := g.Check(context.Background(), Request{}, encode(t, r)); err != nil {
		t.Fatalf("`workflow` was rejected while the contract still had it: %v", err)
	}

	// The operator edits the rules file. Nothing is rebuilt.
	delete(testTypes, "workflow")
	t.Cleanup(func() { testTypes["workflow"] = true })

	err := g.Check(context.Background(), Request{}, encode(t, r))
	if err == nil {
		t.Fatal("`workflow` was still accepted after the contract retired it")
	}
	if !errors.Is(err, ErrNotEligible) {
		t.Errorf("wrong error kind: %v", err)
	}
	// And the rejection says what the valid values are, rather than sending the
	// reader to the source.
	if !strings.Contains(err.Error(), "convention") {
		t.Errorf("the rejection does not name the valid types: %v", err)
	}
}

func TestTheShapeIsEnforcedFieldByField(t *testing.T) {
	for _, tc := range []struct {
		name string
		mut  func(*Response)
		want string
	}{
		{"no title", func(r *Response) { r.Title = "" }, "title"},
		{"blank title", func(r *Response) { r.Title = "   " }, "title"},
		{"no body", func(r *Response) { r.Body = "" }, "body"},
		{"unknown type", func(r *Response) { r.Type = "invented" }, "memory type"},
		{"empty type", func(r *Response) { r.Type = "" }, "memory type"},
		{"bad altitude", func(r *Response) { r.Altitude = "medium" }, "altitude"},
		{"empty altitude", func(r *Response) { r.Altitude = "" }, "altitude"},
		{"slug with spaces", func(r *Response) { r.Slug = "not a slug" }, "slug"},
		{"slug in caps", func(r *Response) { r.Slug = "Not-A-Slug" }, "slug"},
		{"slug with a trailing hyphen", func(r *Response) { r.Slug = "trailing-" }, "slug"},
		{"confidence above one", func(r *Response) { r.Confidence = 1.5 }, "confidence"},
		{"confidence below zero", func(r *Response) { r.Confidence = -0.1 }, "confidence"},
		{"too many tags", func(r *Response) {
			r.Tags = []string{"a", "b", "c", "d", "e", "f", "g", "h", "i"}
		}, "tags"},
		{"too many aliases", func(r *Response) {
			r.Aliases = []string{"a", "b", "c", "d", "e", "f", "g"}
		}, "aliases"},
		{"an empty tag", func(r *Response) { r.Tags = []string{"good", " "} }, "empty tag"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := good()
			tc.mut(&r)
			err := testSchema().Check(context.Background(), Request{}, encode(t, r))
			if err == nil {
				t.Fatalf("%s was accepted", tc.name)
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Errorf("the rejection does not say what was wrong (want %q): %v",
					tc.want, err)
			}
		})
	}
}

// A good slug passes, or the shape rule is just refusing everything.
func TestAWellFormedSlugPasses(t *testing.T) {
	for _, slug := range []string{"staging-gate", "gate", "a1-b2-c3", "2026-08-21-note"} {
		r := good()
		r.Slug = slug
		if err := testSchema().Check(context.Background(), Request{}, encode(t, r)); err != nil {
			t.Errorf("slug %q was rejected: %v", slug, err)
		}
	}
}

// A model that invents a field misunderstood the task. Dropping it silently
// hides the misunderstanding until it resurfaces as a *missing* field.
func TestAnInventedFieldIsRefusedRatherThanIgnored(t *testing.T) {
	raw := `{"title":"T","type":"fact","altitude":"artifact","body":"B",` +
		`"confidence":0.5,"urgency":"high"}`
	err := testSchema().Check(context.Background(), Request{}, raw)
	if err == nil {
		t.Fatal("a response with an invented field was accepted")
	}
	if !strings.Contains(err.Error(), "urgency") {
		t.Errorf("the rejection does not name the invented field: %v", err)
	}
}

// Low confidence is not a rejection. It lands the note `unfiled` with the number
// in frontmatter, and the review queue is a query over that — which is what "no
// inbox directory" means in practice.
func TestLowConfidenceIsNotARejection(t *testing.T) {
	r := good()
	r.Confidence = 0.05
	if err := testSchema().Check(context.Background(), Request{}, encode(t, r)); err != nil {
		t.Errorf("a low-confidence response was rejected rather than queued: %v", err)
	}
}

// The gate and the write path must give the same answer, so they share one
// validator rather than keeping two that drift.
func TestParseResponseAndTheGateAgree(t *testing.T) {
	bad := `{"title":"T","type":"nope","altitude":"artifact","body":"B","confidence":0.5}`
	r, err := ParseResponse(bad)
	if err != nil {
		t.Fatalf("ParseResponse could not read a well-formed object: %v", err)
	}
	if err := testSchema().Validate(r); err == nil {
		t.Error("Validate accepted what Check rejects")
	}
	if err := testSchema().Check(context.Background(), Request{}, bad); err == nil {
		t.Error("Check accepted an unknown type")
	}
}

// A rejection leaves the note alone. The pass reports a failure, and the note
// keeps whatever capture wrote.
func TestASchemaRejectionLeavesTheNoteUnfiled(t *testing.T) {
	bad := `{"title":"T","type":"nope","altitude":"artifact","body":"B","confidence":0.5}`
	p := passWith(t, bad)
	p.AddPost(testSchema())

	out, err := p.Run(context.Background(), Request{Rel: "x.md", Raw: "raw"})
	if err == nil {
		t.Fatal("a schema violation was written")
	}
	if out.Enriched {
		t.Error("a schema violation reported the note enriched")
	}
	if out.Body != "" {
		t.Errorf("a rejected response returned a body: %q", out.Body)
	}
}
