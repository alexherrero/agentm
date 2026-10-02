package enrich

import (
	"strings"
	"testing"
	"time"

	"github.com/alexherrero/agentm/daemon/internal/cardshape"
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
// the moment (as `created`, which `captured` folds into), the surface, the
// operator's verbatim instruction, and the review marks all come through —
// quoted as they were.
func TestCarryProvenanceKeepsTheCaptureRecord(t *testing.T) {
	out := CarryProvenance(previousNote, rendered(t))
	for _, want := range []string{
		"source: operator-direct", "lifecycle: pinned", "created: 2026-09-04T09:00:00+00:00",
		"via: cli", `instructions: "tag:urgent"`, "review_flags: [near-duplicate]",
		"related: memory/semantic/twin.md",
	} {
		if !strings.Contains(out, "\n"+want+"\n") {
			t.Fatalf("carried line %q missing from:\n%s", want, out)
		}
	}
	if strings.Contains(out, "\ncaptured:") {
		t.Fatalf("captured folds into created and does not travel beside it:\n%s", out)
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

// Every key the first full run was measured to have dropped comes across,
// except the six the card backfill retired. The audit that produced this list
// read the enrichment journal's own before/after pairs, so the case is the
// corpus's rather than an invented one.
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
		"slug: a-thought-worth-keeping",
		"fingerprint: 04301193df62afb1", "occurrences: 20",
		"source_id: idea-incubator:home-server-cluster",
		"derived_from: [personal/_inbox/a.md]",
		"promoted_at: 2026-07-12T01:59:48+00:00", "promoted_to: personal/idea/a.md",
	} {
		if !strings.Contains(out, "\n"+want+"\n") {
			t.Errorf("carried line %q missing from:\n%s", want, out)
		}
	}
	// The card backfill (agentm-vault plan 06) retired these from the card. A
	// rewrite lets them go rather than carrying them back onto a card the
	// backfill cleaned.
	for _, gone := range []string{"group:", "always_load:", "excerpt_edges_unverified:",
		"mining_rationale:", "mining_confidence:", "mining_occurrences:"} {
		if strings.Contains(out, "\n"+gone) {
			t.Errorf("retired %q came back:\n%s", gone, out)
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

// `captured` folds into `created`: the rewritten card keeps the earlier of the
// two days, in the one field, as it was written.
func TestCarryProvenanceFoldsCapturedIntoCreated(t *testing.T) {
	cases := []struct{ was, want string }{
		{"---\ntype: idea\ncaptured: '2026-06-27'\n---\n\nb\n", "created: '2026-06-27'"},
		{"---\ntype: idea\ncreated: 2026-09-12\ncaptured: 2026-08-12T05:05:58Z\n---\n\nb\n",
			"created: 2026-08-12T05:05:58Z"},
		{"---\ntype: idea\ncreated: 2026-07-14\ncaptured: 2026-07-20\n---\n\nb\n", "created: 2026-07-14"},
		{"---\ntype: idea\ncreated: 2026-07-14T03:52:08+00:00\ncaptured: 2026-07-14\n---\n\nb\n",
			"created: 2026-07-14T03:52:08+00:00"},
	}
	for _, c := range cases {
		out := CarryProvenance(c.was, rendered(t))
		if !strings.Contains(out, "\n"+c.want+"\n") || strings.Contains(out, "\ncaptured:") {
			t.Errorf("from:\n%s\ngot:\n%s", c.was, out)
		}
	}
}

// `backfilled` names what the card backfill stamped. A field the pass wrote
// itself leaves the list, a field it carried stays, and an emptied list goes.
func TestCarryProvenanceKeepsBackfillProvenanceOnlyForWhatThePassDidNotWrite(t *testing.T) {
	was := "---\ntitle: From the heading\ntype: idea\nfiling_confidence: medium\ntrust: trusted\n" +
		"created: 2026-06-27\nbackfilled: [title, filing_confidence, trust, created]\n---\n\nb\n"
	out := CarryProvenance(was, rendered(t))
	if !strings.Contains(out, "\nbackfilled: [trust, created]\n") {
		t.Fatalf("the carried stamps keep their provenance and the rewritten ones lose it:\n%s", out)
	}
	was = "---\ntitle: From the heading\ntype: idea\nbackfilled: [title]\n---\n\nb\n"
	if out := CarryProvenance(was, rendered(t)); strings.Contains(out, "backfilled:") {
		t.Fatalf("an emptied list must go:\n%s", out)
	}
}

// A composed card leaves in the card's order, the read block first and the
// machine block last, however the render and the carry laid it out.
func TestComposeWritesTheCardOrder(t *testing.T) {
	previous := "---\ntype: preference\nslug: a-thought\nsource: conversation\ncreated: 2026-09-01\n" +
		"lifecycle: active\ntrust: trusted\n---\n\na thought worth keeping\n"
	out, _, err := Compose(previous, Response{Title: "A thought", Type: "preference", Confidence: 0.9},
		Stamp{Version: "v", RulesHash: "h", ConfidenceFloor: 0.65,
			At: time.Date(2026, 9, 12, 9, 0, 0, 0, time.UTC)}, DepthLight, nil)
	if err != nil {
		t.Fatal(err)
	}
	if cardshape.Reorder(out) != out {
		t.Fatalf("composed out of the card's order:\n%s", out)
	}
	if !strings.HasPrefix(out, "---\ntitle: A thought\ntype: preference\nstatus: active\n") {
		t.Fatalf("the read block must lead:\n%s", out)
	}
}

// The second guard on the self-probe. `SelfProbe` is what keeps a synthetic
// note out of the pass; this is what keeps the marker on one that reaches a
// rewrite anyway — by a path neither gate anticipated, or by an older binary
// still running the night the fix lands.
//
// The marker is an identity rather than a value, which is why it is worth
// guarding twice. The probe retires yesterday's card by reading `probe:` back
// off the file, so a rewrite that dropped it left a card nothing would ever
// delete; the live card of 2026-09-17 was exactly that, and the next night
// would have added a second.
func TestCarryProvenanceKeepsTheProbeMarker(t *testing.T) {
	was := "---\ntitle: AgentM self-probe 2026-09-17T06:11:19Z\ntype: reference\n" +
		"status: active\nfiling_confidence: high\nprobe: self-probe\n---\n\n" +
		"Synthetic round-trip probe written by the daemon.\n"
	out := CarryProvenance(was, rendered(t))
	if !strings.Contains(out, "\nprobe: self-probe\n") {
		t.Fatalf("the marker that says what the note is must survive a rewrite:\n%s", out)
	}
}

// And through the whole write path, in the card's order: `probe` sits in the
// machine block, so a carried marker lands where the panel shows it rather than
// wherever the carry appended it.
func TestComposeKeepsTheProbeMarkerInTheCardOrder(t *testing.T) {
	previous := "---\ntitle: AgentM self-probe 2026-09-17T06:11:19Z\ntype: reference\n" +
		"status: active\nsource: daemon\ntrust: trusted\ncreated: 2026-09-17T06:11:19Z\n" +
		"lifecycle: active\nslug: agentm-self-probe-2026-09-17t06-11-19z\n" +
		"probe: self-probe\n---\n\nSynthetic round-trip probe.\n"
	out, _, err := Compose(previous, Response{
		Title: "AgentM self-probe", Type: "reference", Confidence: 0.3,
	}, Stamp{Version: "v", RulesHash: "h", ConfidenceFloor: 0.65,
		At: time.Date(2026, 9, 17, 9, 30, 21, 0, time.UTC)}, DepthDeep, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, "\nprobe: self-probe\n") {
		t.Fatalf("the composed note lost the marker:\n%s", out)
	}
	if cardshape.Reorder(out) != out {
		t.Fatalf("composed out of the card's order:\n%s", out)
	}
}

// Another writer's stamps survive the rewrite that judges the card: the lesson
// a card taught (crystallize) and the source fingerprint a second fetch
// compares against (capture). Losing the first drops the card out of its
// lesson's demotion; losing the second makes the same-source update decide
// blind. Both were being dropped until task 182.
func TestCarryProvenanceKeepsAnotherWritersStamps(t *testing.T) {
	previous := `---
type: reference
status: active
source: external-fetch
source_url: https://example.com/article
source_hash: sha256:abc123
source_version: "2026-09-28"
consolidated_into: "[[answers-that-cite-the-memories-they-came-from]]"
---

The query agent cites the memory ids it used.
`
	out := CarryProvenance(previous, rendered(t))
	for _, want := range []string{
		`consolidated_into: "[[answers-that-cite-the-memories-they-came-from]]"`,
		"source_hash: sha256:abc123",
		`source_version: "2026-09-28"`,
	} {
		if !strings.Contains(out, "\n"+want+"\n") {
			t.Fatalf("stamp %q lost in the rewrite:\n%s", want, out)
		}
	}
}

// The census. Every field the card shape names is either written by the pass,
// carried across it, or let go on purpose with a reason. A writer that adds a
// field to the card shape fails here until someone decides which, instead of
// finding out from the vault weeks later that every re-enrichment erased it.
func TestEveryCardFieldIsWrittenCarriedOrLetGoOnPurpose(t *testing.T) {
	carried := map[string]bool{}
	for _, k := range carriedFields {
		carried[k] = true
	}
	for _, field := range append(append([]string{}, cardshape.ReadOrder...), cardshape.MachineOrder...) {
		n := 0
		if passWrittenFields[field] {
			n++
		}
		if carried[field] {
			n++
		}
		if notCarried[field] != "" {
			n++
		}
		switch {
		case n == 0:
			t.Errorf("card field %q is neither written by the pass, carried, nor in notCarried with a reason", field)
		case notCarried[field] != "" && n > 1:
			t.Errorf("card field %q is in notCarried and also written or carried; pick one", field)
		}
	}
	for field := range notCarried {
		if !contains(cardshape.ReadOrder, field) && !contains(cardshape.MachineOrder, field) {
			t.Errorf("notCarried names %q, which is not a card field", field)
		}
	}
}

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}

// A pass never removes an alias already on the card (task 182 step 5, the
// operator's ruling of 2026-10-01). Capture-time aliases are the asker's own
// phrasing, the channel the filing design adopted; the 2026-09-23 rewrite of
// the gold set's rc02 card replaced four of them with the pass's two and the
// card fell out of the top 300. The pass's own new aliases are added after.
func TestCarryProvenanceKeepsEveryAliasAndAddsThePasssOwn(t *testing.T) {
	previous := "---\ntype: reference\nstatus: active\n" +
		`aliases: ["who else synthesizes memories on a timer", "agents that think while idle", ConsolidateAgent]` +
		"\n---\n\nA ConsolidateAgent runs on a timer.\n"
	next := RenderNote(Response{
		Title: "The Always-On agent's loop is dreaming", Type: "reference", Confidence: 0.9,
		Aliases: []string{"consolidateagent", "sleep cycles"}, Body: "A ConsolidateAgent runs on a timer.",
	}, Stamp{})
	out := CarryProvenance(previous, next)
	// Plain scalars where YAML allows them: the writer's yamlScalar convention.
	want := `aliases: [who else synthesizes memories on a timer, agents that think while idle, ConsolidateAgent, sleep cycles]`
	if !strings.Contains(out, "\n"+want+"\n") {
		t.Fatalf("want the previous aliases kept first and the pass's new one added, case-folded:\n%s", out)
	}
	if strings.Count(out, "\naliases:") != 1 {
		t.Fatalf("one aliases line:\n%s", out)
	}
}

// A block list is read too, and a pass that wrote no aliases keeps the card's.
func TestCarryProvenanceKeepsABlockListOfAliases(t *testing.T) {
	previous := "---\ntype: reference\nstatus: active\naliases:\n  - EnterWorktree\n  - \"isolation.mode: worktree-per-plan\"\n---\n\nbody\n"
	out := CarryProvenance(previous, rendered(t))
	if !strings.Contains(out, "\naliases: [EnterWorktree, \"isolation.mode: worktree-per-plan\"]\n") {
		t.Fatalf("want the block list carried as a flow list:\n%s", out)
	}
	if got := CarryProvenance("---\ntype: reference\n---\n\nbody\n", rendered(t)); strings.Contains(got, "aliases:") {
		t.Fatalf("no previous aliases, none invented:\n%s", got)
	}
}
