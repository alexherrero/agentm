package enrich

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

// agentm-vault § Projects and tasks: inside the projects space a pass writes a
// charter and decisions/, designs/ and research/ notes, merging into them, and
// never a tracker, a plan or a progress log.

func TestAProjectRecordIsKnownByItsPlace(t *testing.T) {
	cases := map[string]bool{
		"projects/agentm/_index.md":                          true,
		"projects/agentm/charter.md":                         true,
		"projects/agentm/decisions/a-ruling.md":              true,
		"projects/crickets/designs/x.md":                     true,
		"projects/agentm/research/bundle/notes/finding.md":   true,
		"projects/agentm/tracker.md":                         false,
		"projects/agentm/tasks/build-it/tracker.md":          false,
		"projects/agentm/tasks/build-it/plan.md":             false,
		"projects/agentm/tasks/build-it/progress.md":         false,
		"projects/agentm/decisions/plan.md":                  false,
		"projects/agentm/_harness/PLAN-online-recall.md":     false,
		"projects/agentm/_harness/progress-online-recall.md": false,
		"projects/agentm/desk/briefs/b.md":                   false,
		"projects/agentm/completed/research-note.md":         false,
		"projects/agentm/roadmap.md":                         false,
		"projects/agentm/research/.obsidian/workspace.md":    false,
		"projects/_archive/old-project/decisions/d.md":       false,
		"agent/memory/semantic/keep-git-out-of-drive.md":     false,
		"projects/agentm/decisions/a-ruling.txt":             false,
	}
	for rel, want := range cases {
		if got := IsProjectRecord(rel); got != want {
			t.Errorf("IsProjectRecord(%q) = %v, want %v", rel, got, want)
		}
	}
	if got := ProjectOf("projects/agentm/decisions/a.md"); got != "agentm" {
		t.Errorf("ProjectOf = %q, want agentm", got)
	}
	if got := ProjectOf("agent/memory/semantic/a.md"); got != "" {
		t.Errorf("ProjectOf a card = %q, want empty", got)
	}
}

func TestTheEligibilityGateKeepsSessionFilesAndReadsRecordsAsRecords(t *testing.T) {
	g := DefaultEligibility(func(string) bool { return true })
	g.IsRecordKind = func(kind string) bool { return kind == "design" || kind == "project-index" }
	g.ProjectRecord = IsProjectRecord
	check := func(rel, body string) error {
		return g.Check(context.Background(), Request{Rel: rel, Raw: body}, body)
	}
	design := "---\nkind: design\nstatus: final\n---\n\n# A design\n"
	if err := check("projects/crickets/designs/a-design.md", design); err != nil {
		t.Errorf("a design record with a record kind was refused: %v", err)
	}
	for _, rel := range []string{"projects/agentm/tasks/build-it/tracker.md",
		"projects/agentm/tasks/build-it/plan.md", "projects/agentm/_harness/progress.md",
		"projects/agentm/desk/board.md"} {
		if err := check(rel, "---\ntitle: t\n---\n\nbody\n"); !errors.Is(err, ErrNotEligible) {
			t.Errorf("%s was eligible: %v", rel, err)
		}
	}
	// A card outside the projects space keeps the record-kind refusal.
	if err := check("agent/memory/semantic/a.md", design); !errors.Is(err, ErrNotEligible) {
		t.Errorf("a record kind in a class directory was eligible: %v", err)
	}
}

const decisionRecord = "---\ntype: reference\nstatus: active\ncreated: 2026-07-01\nupdated: 2026-07-02\n" +
	"tags: [agentm, retrieval]\ngroup: projects\nslug: keep-the-wall\nimportance: 8\n---\n\n" +
	"# Keep the wall\n\nArchived notes stay walled.\n"

func deepResponse() Response {
	return Response{Title: "Something else", Type: "preference", Summary: "The wall stays.",
		Tags: []string{"retrieval", "walls"}, Aliases: []string{"the wall"}, Related: []string{"wall-note"},
		Confidence: 0.2, ImportanceProposed: 6, Body: "It follows from the lifecycle ruling."}
}

func recordStamp() Stamp {
	// The pass version running now, so a record this stamps is owed only the
	// light pass next; a stamp naming any other version is owed the deep pass.
	return Stamp{At: time.Date(2026, 9, 14, 3, 0, 0, 0, time.UTC), Version: PassVersion, RulesHash: "abc123"}
}

func TestADeepPassAddsASectionAndKeepsEveryFieldTheRecordCarried(t *testing.T) {
	next, err := ComposeRecord(decisionRecord, deepResponse(), recordStamp(), DepthDeep,
		[]Neighbour{{ID: "wall-note", Rel: "projects/agentm/decisions/wall-note.md"}})
	if err != nil {
		t.Fatal(err)
	}
	for _, line := range []string{"type: reference", "status: active", "created: 2026-07-01",
		"group: projects", "slug: keep-the-wall", "importance: 8",
		"tags: [agentm, retrieval, walls]", "aliases: [the wall]", "summary: The wall stays.",
		`related: ["[[wall-note]]"]`, "importance_proposed: 6", "updated: 2026-09-14"} {
		if !strings.Contains(next, "\n"+line+"\n") {
			t.Errorf("missing %q in:\n%s", line, next)
		}
	}
	// Keys, in the frontmatter only: the dated section's own prose may say "lifecycle".
	front, _ := splitNote(next)
	for _, absent := range []string{"\ntitle:", "\ntype: preference", "\nfiling_confidence:",
		"\nconfidence:", "\nlifecycle:", "\nupdated: 2026-07-02"} {
		if strings.Contains(front, absent) {
			t.Errorf("the record's frontmatter gained %q:\n%s", strings.TrimSpace(absent), front)
		}
	}
	if !strings.Contains(next, "\n# Keep the wall\n\nArchived notes stay walled.\n") {
		t.Errorf("the record's text did not survive:\n%s", next)
	}
	if !strings.Contains(next, DreamingHeading+" (2026-09-14)\n\nIt follows from the lifecycle ruling.") {
		t.Errorf("no dated section:\n%s", next)
	}
	if PassDepth(next) != DepthLight {
		t.Error("the stamps did not land: the record is still owed the deep pass")
	}
}

func TestALightPassLeavesARecordsBodyAsItWas(t *testing.T) {
	r := deepResponse()
	r.Body = "Nothing a light pass may add."
	next, err := ComposeRecord(decisionRecord, r, recordStamp(), DepthLight, nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, body := splitNote(next); body != "\n# Keep the wall\n\nArchived notes stay walled.\n" {
		t.Errorf("the light pass changed the body: %q", body)
	}
	if strings.Contains(next, "importance_proposed") {
		t.Error("a light pass proposed an importance")
	}
}

func TestABlockListAndUnknownFieldsStayExactlyAsTheyAre(t *testing.T) {
	record := "---\nkind: design\ntags:\n  - one\n  - two\nsources:\n  - https://example.com\n---\n\nText.\n"
	next, err := ComposeRecord(record, deepResponse(), recordStamp(), DepthDeep, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(next, "\ntags:\n  - one\n  - two\nsources:\n  - https://example.com\n") {
		t.Errorf("a block list was rewritten:\n%s", next)
	}
	if !strings.HasPrefix(next, "---\nkind: design\n") {
		t.Errorf("the record's first line moved:\n%s", next)
	}
}

func TestARecordWithNoFrontmatterIsRefused(t *testing.T) {
	if _, err := ComposeRecord("# A bare note\n", deepResponse(), recordStamp(), DepthDeep, nil); err == nil {
		t.Error("a record with no frontmatter block was composed")
	}
}
