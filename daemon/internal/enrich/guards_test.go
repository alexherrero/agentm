package enrich

import (
	"strings"
	"testing"
)

// A card that has been through the room: a `why` somebody wrote, an importance
// somebody edited past the proposal, and the excerpt it was distilled from.
const judgedNote = `---
title: Keep git out of Drive's mirrored folders
type: workflow
status: active
why: A worktree in the mirror rewrote every mtime overnight. This decides where a clone may live.
importance: 9
created: 2026-09-04
source: conversation
importance_proposed: 6
---

Repositories live outside the mirrored tree.

## Evidence

> the file provider rewrites mtimes on sync, so a worktree inside it churns
`

// What the pass returns: a rewritten body and its own judgment, and nothing
// that could know any of the three things above.
func enrichedOver(body string) string {
	return "---\ntitle: Keep git out of Drive\ntype: workflow\nstatus: active\n" +
		"confidence: 0.90\nfiling_confidence: high\n---\n\n" + body + "\n"
}

// `why` is what was happening when the note was kept, and no pass was there.
// It survives a rewrite and is never written by one.
func TestAPassNeverTouchesWhy(t *testing.T) {
	out := CarryProvenance(judgedNote, enrichedOver("A tidier sentence."))
	want := "why: A worktree in the mirror rewrote every mtime overnight. " +
		"This decides where a clone may live."
	if !strings.Contains(out, "\n"+want+"\n") {
		t.Fatalf("the why did not survive the rewrite:\n%s", out)
	}
	if strings.Count(out, "\nwhy: ") != 1 {
		t.Fatalf("the why was written twice:\n%s", out)
	}
}

// The Evidence block quotes the excerpt the note came from. It is the room's
// material, not prose a pass may rewrite, and a rewrite that dropped it would
// take the note's own evidence with it.
func TestAPassNeverTouchesTheEvidenceBlock(t *testing.T) {
	out := CarryProvenance(judgedNote, enrichedOver("A tidier sentence."))
	if !strings.Contains(out, "## Evidence\n\n> the file provider rewrites mtimes on sync") {
		t.Fatalf("the Evidence block did not survive the rewrite:\n%s", out)
	}
	if strings.Count(out, "## Evidence") != 1 {
		t.Fatalf("the Evidence block was written twice:\n%s", out)
	}
	// And the prose the pass did write is still there, ahead of it.
	if !strings.Contains(out, "A tidier sentence.") {
		t.Fatalf("carrying the evidence lost the rewrite:\n%s", out)
	}
}

// A note whose Evidence block is followed by another section keeps only the
// Evidence section — the cut is at the next heading, not at the end of file.
func TestOnlyTheEvidenceSectionIsCarried(t *testing.T) {
	previous := judgedNote + "\n## Notes\n\nsomething else entirely\n"
	out := CarryProvenance(previous, enrichedOver("A tidier sentence."))
	if !strings.Contains(out, "## Evidence") {
		t.Fatalf("the Evidence block did not survive:\n%s", out)
	}
	if strings.Contains(out, "something else entirely") {
		t.Fatalf("carrying the evidence dragged the next section with it:\n%s", out)
	}
}

// An `importance` that differs from `importance_proposed` is the operator's,
// and a pass proposing a new number does not move it. The proposal still
// lands, in the machine's half of the card.
func TestAPassNeverOverwritesAnImportanceYouSet(t *testing.T) {
	proposing := "---\ntitle: t\ntype: workflow\nstatus: active\nimportance: 4\n" +
		"importance_proposed: 4\n---\n\nA tidier sentence.\n"
	out := CarryProvenance(judgedNote, proposing)

	if !strings.Contains(out, "\nimportance: 9\n") {
		t.Fatalf("the operator's 9 was overwritten:\n%s", out)
	}
	if !strings.Contains(out, "\nimportance_proposed: 4\n") {
		t.Fatalf("the new proposal did not land:\n%s", out)
	}
}

// When the two agree, the number is the last proposal rather than anyone's
// judgment, and a new one replaces it.
func TestAnUneditedImportanceIsJustTheLastProposal(t *testing.T) {
	previous := strings.Replace(judgedNote, "importance: 9", "importance: 6", 1)
	proposing := "---\ntitle: t\ntype: workflow\nstatus: active\nimportance: 4\n" +
		"importance_proposed: 4\n---\n\nA tidier sentence.\n"
	out := CarryProvenance(previous, proposing)

	if !strings.Contains(out, "\nimportance: 4\n") {
		t.Fatalf("a proposal nobody had edited was treated as a judgment:\n%s", out)
	}
}

// A pass that proposes nothing must not lose the numbers either.
func TestImportanceSurvivesAPassThatProposesNothing(t *testing.T) {
	out := CarryProvenance(judgedNote, enrichedOver("A tidier sentence."))
	if !strings.Contains(out, "\nimportance: 9\n") || !strings.Contains(out, "\nimportance_proposed: 6\n") {
		t.Fatalf("the readings were dropped by a pass that proposed none:\n%s", out)
	}
}

// The capture door writes `created:` now. A rewrite that carried only the
// retired `captured:` spelling would silently re-date every note it touched.
func TestCreatedSurvivesTheRewrite(t *testing.T) {
	out := CarryProvenance(judgedNote, enrichedOver("A tidier sentence."))
	if !strings.Contains(out, "\ncreated: 2026-09-04\n") {
		t.Fatalf("the note lost the day it came into existence:\n%s", out)
	}
}
