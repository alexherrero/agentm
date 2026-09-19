package index

import "testing"

// An empty frontmatter field must not swallow the line under it.
//
// `^project:\s*(.+)$` looks right and is not: Go's `\s` includes `\n`, so on a
// field the operator left blank the separator steps over the line break and
// `(.+)` captures the *next* field. Found live, on
// `designs/agentm-vault/parts/05-memory-root-trims.md`, whose empty `project:`
// sits directly above `parent_design:` — the hit came back reading
// `project: "parent_design: wiki/designs/agentm-vault.md"`.
//
// Every head field was written with the same separator, so this is one bug in
// eight places, and the fixture below leaves a blank above a populated line for
// each of them rather than testing the one field that happened to be caught.
func TestAnEmptyHeadFieldDoesNotCaptureTheNextLine(t *testing.T) {
	head := `
title:
type: reference
kind:
summary: The real summary.
status:
lifecycle: dormant
importance:
project: agentm
`
	for _, c := range []struct {
		name string
		got  string
		want string
	}{
		// The blank ones read as absent...
		{"empty title", headValue(head, headTitleRe), ""},
		{"empty kind", headValue(head, headKindRe), ""},
		{"empty status", headValue(head, headStatusRe), ""},
		// ...and the populated ones still read correctly, which is the half
		// that would catch a fix that simply broke every match.
		{"type after a blank", headValue(head, headTypeRe), "reference"},
		{"summary after a blank", headValue(head, headSummaryRe), "The real summary."},
		{"lifecycle after a blank", headValue(head, headLifecycleRe), "dormant"},
		{"project after a blank", headValue(head, headProjectRe), "agentm"},
	} {
		if c.got != c.want {
			t.Errorf("%s: got %q, want %q", c.name, c.got, c.want)
		}
	}

	if m := headImportanceRe.FindStringSubmatch(head); m != nil {
		t.Errorf("an empty `importance:` matched %q — it should find nothing "+
			"rather than reach down to the next line's digits", m[1])
	}
}

// The live shape that found it, kept verbatim so the regression is the case.
func TestTheLiveCardThatFoundTheBug(t *testing.T) {
	head := `
title: The memory-root trims
status: draft
prd:
project:
parent_design: wiki/designs/agentm-vault.md
part_slug: 05-memory-root-trims
`
	if got := headValue(head, headProjectRe); got != "" {
		t.Errorf("project: got %q, want empty — an unset field is unset, and "+
			"reporting the line below it as the project is worse than "+
			"reporting nothing, because it reads as a real value", got)
	}
	if got := headValue(head, headTitleRe); got != "The memory-root trims" {
		t.Errorf("title: got %q", got)
	}
	if got := headValue(head, headStatusRe); got != "draft" {
		t.Errorf("status: got %q", got)
	}
}

// An `importance:` that is set still parses — the digit case, separately, since
// its regex is the one that does not end in `(.+)$`.
func TestImportanceStillParsesWhenSet(t *testing.T) {
	m := headImportanceRe.FindStringSubmatch("\nimportance: 7\nstatus: active\n")
	if m == nil {
		t.Fatal("a set `importance:` no longer matches at all")
	}
	if m[1] != "7" {
		t.Errorf("importance: got %q, want \"7\"", m[1])
	}
}
