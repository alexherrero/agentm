package enrich

import (
	"strings"
	"testing"
)

const previousNote = `---
type: preference
status: unfiled
captured: 2026-09-04T09:00:00+00:00
source: operator-direct
lifecycle: pinned
via: cli
instructions: "tag:urgent"
review_flags: [near-duplicate]
related: memory/semantic/twin.md
---

a thought worth keeping
`

func rendered(t *testing.T) string {
	t.Helper()
	return RenderNote(Response{
		Title: "A thought worth keeping", Type: "preference", Altitude: "artifact",
		Confidence: 0.91, Body: "A thought worth keeping, distilled.",
	}, Stamp{})
}

// The capture's own record survives the rewrite that judges it: the transport,
// the moment, the surface, the operator's verbatim instruction, and the review
// marks all come through — quoted as they were.
func TestCarryProvenanceKeepsTheCaptureRecord(t *testing.T) {
	out := CarryProvenance(previousNote, rendered(t))
	for _, want := range []string{
		"source: operator-direct", "lifecycle: pinned", "captured: 2026-09-04T09:00:00+00:00",
		"via: cli", `instructions: "tag:urgent"`, "review_flags: [near-duplicate]",
		"related: memory/semantic/twin.md",
	} {
		if !strings.Contains(out, "\n"+want+"\n") {
			t.Fatalf("carried line %q missing from:\n%s", want, out)
		}
	}
	if !strings.HasSuffix(out, "\n\nA thought worth keeping, distilled.\n") {
		t.Fatalf("the body must be untouched:\n%s", out)
	}
	if frontmatterValue(out, "filing_confidence") != "high" {
		t.Fatalf("the pass re-judges confidence; got %q", frontmatterValue(out, "filing_confidence"))
	}
}

// A value the rendered note already sets wins; the previous copy is not appended
// beside it.
func TestCarryProvenanceNeverOverridesTheRenderedNote(t *testing.T) {
	next := "---\ntype: preference\nsource: conversation\n---\n\nbody\n"
	out := CarryProvenance(previousNote, next)
	if strings.Count(out, "\nsource: ") != 1 || !strings.Contains(out, "\nsource: conversation\n") {
		t.Fatalf("the rendered source must stand alone:\n%s", out)
	}
}

// An enriched note is an auto-filed note: with no lifecycle of its own it
// starts `active`, and a note that had nothing else to carry gains nothing else.
func TestCarryProvenanceStartsTheAgingAxis(t *testing.T) {
	out := CarryProvenance("---\ntype: preference\n---\n\nbody\n", rendered(t))
	if !strings.Contains(out, "\nlifecycle: active\n") {
		t.Fatalf("lifecycle must default to active:\n%s", out)
	}
	for _, absent := range []string{"\nsource:", "\nvia:", "\nrelated:"} {
		if strings.Contains(out, absent) {
			t.Fatalf("nothing to carry, yet %q appeared:\n%s", absent, out)
		}
	}
}

func TestFilingConfidenceForStraddlesTheFloor(t *testing.T) {
	if got := FilingConfidenceFor(ConfidenceFloor); got != "high" {
		t.Fatalf("at the floor: %q", got)
	}
	if got := FilingConfidenceFor(ConfidenceFloor - 0.01); got != "low" {
		t.Fatalf("below the floor: %q", got)
	}
}
