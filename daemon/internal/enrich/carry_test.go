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
		Title: "A thought worth keeping", Type: "preference",
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

// A superseded note keeps its successor. The lifecycle status and the pointer
// to the note that replaced it are one fact in two fields: the vault's
// frontmatter gate fails a `lifecycle: superseded` with no `superseded_by:`,
// and enrichment carried the status while dropping the pointer, which broke
// two notes in the first full run (2026-09-11). The successor's back-link
// rides across the same way.
func TestCarryProvenanceKeepsTheSupersessionPair(t *testing.T) {
	loser := "---\ntype: preference\nlifecycle: superseded\n" +
		"superseded_by: memory/semantic/winner.md\n---\n\nthe old wording\n"
	out := CarryProvenance(loser, rendered(t))
	if !strings.Contains(out, "\nlifecycle: superseded\n") ||
		!strings.Contains(out, "\nsuperseded_by: memory/semantic/winner.md\n") {
		t.Fatalf("the superseded pair must survive the rewrite:\n%s", out)
	}

	winner := "---\ntype: preference\nlifecycle: active\n" +
		"supersedes: memory/semantic/loser.md\n---\n\nthe new wording\n"
	out = CarryProvenance(winner, rendered(t))
	if !strings.Contains(out, "\nsupersedes: memory/semantic/loser.md\n") {
		t.Fatalf("the successor's back-link must survive the rewrite:\n%s", out)
	}
}

// Every key the first full run was measured to have dropped comes across. The
// audit that produced this list read the enrichment journal's own before/after
// pairs, so the case is the corpus's rather than an invented one.
func TestCarryProvenanceKeepsWhatTheFirstFullRunDropped(t *testing.T) {
	was := "---\ntype: preference\n" +
		"slug: a-thought-worth-keeping\n" +
		"group: memory\n" +
		"always_load: false\n" +
		"fingerprint: 04301193df62afb1\n" +
		"occurrences: 20\n" +
		"source_id: idea-incubator:home-server-cluster\n" +
		"derived_from: [personal/_inbox/a.md]\n" +
		"excerpt_edges_unverified: true\n" +
		"promoted_at: 2026-07-12T01:59:48+00:00\n" +
		"promoted_to: personal/idea/a.md\n" +
		"mining_rationale: \"follow-up marker\"\n" +
		"mining_confidence: LOW\n" +
		"mining_occurrences: 1\n" +
		"---\n\nbody\n"
	out := CarryProvenance(was, rendered(t))
	for _, want := range []string{
		"slug: a-thought-worth-keeping", "group: memory", "always_load: false",
		"fingerprint: 04301193df62afb1", "occurrences: 20",
		"source_id: idea-incubator:home-server-cluster",
		"derived_from: [personal/_inbox/a.md]", "excerpt_edges_unverified: true",
		"promoted_at: 2026-07-12T01:59:48+00:00", "promoted_to: personal/idea/a.md",
		`mining_rationale: "follow-up marker"`, "mining_confidence: LOW",
		"mining_occurrences: 1",
	} {
		if !strings.Contains(out, "\n"+want+"\n") {
			t.Errorf("carried line %q missing from:\n%s", want, out)
		}
	}
}

// `altitude` stays dropped: the design retires it at the deep pass, so
// carrying it back would undo a decision rather than preserve a fact.
func TestCarryProvenanceDoesNotResurrectAltitude(t *testing.T) {
	was := "---\ntype: preference\naltitude: artifact\n---\n\nbody\n"
	if out := CarryProvenance(was, rendered(t)); strings.Contains(out, "\naltitude:") {
		t.Fatalf("altitude is retired and must not come back:\n%s", out)
	}
}

// A dormant note keeps the date it sank. `RenderFrontmatter` writes
// `lifecycle_since` only for a card sinking now, so without the carry a note
// that was already dormant would keep the status and lose when it got it.
func TestCarryProvenanceKeepsTheLifecycleDate(t *testing.T) {
	was := "---\ntype: preference\nlifecycle: dormant\nlifecycle_since: 2026-08-01\n---\n\nquiet\n"
	out := CarryProvenance(was, rendered(t))
	if !strings.Contains(out, "\nlifecycle_since: 2026-08-01\n") {
		t.Fatalf("the date the note sank must survive the rewrite:\n%s", out)
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
	if got := FilingConfidenceFor(DefaultConfidenceFloor, 0); got != "high" {
		t.Fatalf("at the floor: %q", got)
	}
	if got := FilingConfidenceFor(DefaultConfidenceFloor-0.01, 0); got != "low" {
		t.Fatalf("below the floor: %q", got)
	}
}
